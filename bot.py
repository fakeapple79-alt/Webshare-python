import asyncio
import json
import logging
import random
import string
import time
from pathlib import Path

import cloudscraper
from concurrent.futures import ThreadPoolExecutor
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ==================== CONFIG ====================
BOT_TOKEN = "8941757041:AAFAFIqPZLA_W0cBD8wovplqngQ7vnMVwkw"
AZCAPTCHA_API_KEY = "27b2a96ea593421936f11ec9e1cb6521f8c9da4e"
AZCAPTCHA_API_URL = "https://azcaptcha.com/in.php"
AZCAPTCHA_RESULT_URL = "https://azcaptcha.com/res.php"

FIXED_PASSWORD = "Hellyeah@123"
WEBSHARE_RECAPTCHA_SITEKEY = "6LeHZ6UUAAAAAKat_YS--O2tj_by3gv3r_l03j9d"
WEBSHARE_REGISTER_URL = "https://proxy.webshare.io/register"
API_BASE = "https://proxy.webshare.io/api/v2"

BASE_DIR = Path(__file__).parent
ACCOUNTS_FILE = BASE_DIR / "webshare_accounts.json"
PROXIES_FILE = BASE_DIR / "proxies.txt"

WAITING_EMAIL, WAITING_PASSWORD = range(2)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
log = logging.getLogger("webshare-bot")

# ==================== HELPERS ====================

def load_accounts() -> dict:
    if ACCOUNTS_FILE.exists():
        try:
            data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
            if "accounts" not in data:
                data["accounts"] = []
            return data
        except Exception:
            pass
    return {"accounts": []}

def save_accounts(data: dict):
    ACCOUNTS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

def save_proxies_to_file(proxies: list[str], email: str = None):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(PROXIES_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n{'='*50}\n")
        f.write(f"# {timestamp}\n")
        if email:
            f.write(f"# Email: {email}\n")
        for p in proxies:
            f.write(p + "\n")

def generate_nullz_username() -> str:
    random_part = ''.join(random.choices(string.ascii_lowercase + string.digits, k=random.randint(4, 6)))
    return f"fakeprithvi{random_part}"

# ==================== ASYNC SCRAPER ====================

class AsyncScraper:
    def __init__(self):
        self._scraper = None
        self._executor = ThreadPoolExecutor(max_workers=1)

    def _get_scraper(self):
        if self._scraper is None:
            self._scraper = cloudscraper.create_scraper(
                browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False, 'desktop': True}
            )
        return self._scraper

    async def get(self, url, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, lambda: self._get_scraper().get(url, **kwargs))

    async def post(self, url, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, lambda: self._get_scraper().post(url, **kwargs))

    def close(self):
        if self._scraper:
            self._scraper.close()
        self._executor.shutdown(wait=False)

# ==================== CAPTCHA ====================

async def solve_recaptcha(session: AsyncScraper) -> str | None:
    log.info("[AZCAPTCHA] Solving reCAPTCHA...")
    submit_data = {
        "key": AZCAPTCHA_API_KEY,
        "method": "userrecaptcha",
        "googlekey": WEBSHARE_RECAPTCHA_SITEKEY,
        "pageurl": WEBSHARE_REGISTER_URL,
        "json": 1,
    }

    try:
        resp = await session.post(AZCAPTCHA_API_URL, data=submit_data, timeout=30)
        result = resp.json()
        if result.get("status") != 1:
            log.error(f"[AZCAPTCHA] Submit failed: {result}")
            return None
        captcha_id = result["request"]
        log.info(f"[AZCAPTCHA] Task ID: {captcha_id}")
    except Exception as e:
        log.error(f"[AZCAPTCHA] Submit error: {e}")
        return None

    for i in range(60):
        await asyncio.sleep(5)
        try:
            resp = await session.get(AZCAPTCHA_RESULT_URL, params={
                "key": AZCAPTCHA_API_KEY,
                "action": "get",
                "id": captcha_id,
                "json": 1
            }, timeout=15)
            data = resp.json()
            if data.get("status") == 1:
                log.info("[AZCAPTCHA] Solved!")
                return data["request"]
            if "CAPCHA_NOT_READY" not in str(data.get("request", "")):
                log.warning(f"[AZCAPTCHA] Error: {data}")
                return None
        except Exception as e:
            log.error(f"[AZCAPTCHA] Poll error: {e}")
    log.error("[AZCAPTCHA] Timeout")
    return None

# ==================== REGISTRATION ====================

async def create_nullz_email() -> tuple[str, str]:
    username = generate_nullz_username()
    email = f"{username}@nullz.in"
    password = FIXED_PASSWORD
    log.info(f"[NULLZ] Generated: {email}")
    return email, password

async def register_webshare(session: AsyncScraper, email: str, password: str) -> dict | None:
    token = await solve_recaptcha(session)
    if not token:
        return None

    payload = {
        "email": email,
        "password": password,
        "recaptcha": token,
        "tos_accepted": True,
        "marketing_email_accepted": False,
    }

    try:
        resp = await session.post(
            f"{API_BASE}/register/",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Origin": "https://proxy.webshare.io",
                "Referer": "https://proxy.webshare.io/register",
            },
            timeout=25
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            api_token = data.get("token")
            if api_token:
                log.info(f"[REGISTER] Success: {email}")
                return {
                    "email": email,
                    "password": password,
                    "token": api_token,
                    "registered_at": int(time.time())
                }
        log.warning(f"[REGISTER] Failed {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log.error(f"[REGISTER] Exception: {e}")
    return None

async def fetch_proxies(session: AsyncScraper, token: str, count: int = 10) -> list[str]:
    try:
        resp = await session.get(
            f"{API_BASE}/proxy/list/",
            params={"mode": "direct", "page": 1, "page_size": max(count, 25)},
            headers={"Authorization": f"Token {token}"},
            timeout=20
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            proxies = []
            for p in results:
                user = p.get("username", "")
                pw = p.get("password", "")
                host = p.get("proxy_address", "")
                port = p.get("port", "")
                if user and pw and host and port:
                    proxies.append(f"{host}:{port}:{user}:{pw}")
                if len(proxies) >= count:
                    break
            return proxies
    except Exception as e:
        log.error(f"[FETCH] Error: {e}")
    return []

# ==================== BOT HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("1. Automatic (Temp Email)", callback_data="auto")],
        [InlineKeyboardButton("2. Your Account (Manual)", callback_data="manual")],
        [InlineKeyboardButton("Get Proxies", callback_data="proxies")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🌐 *Webshare Proxy Bot*\n\n"
        "Choose an option:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "auto":
        await query.edit_message_text("⏳ Starting automatic registration...\nThis may take 1-3 minutes.")
        await run_auto(query, context)

    elif query.data == "manual":
        await query.edit_message_text("📧 Please send your *email* address:")
        return WAITING_EMAIL

    elif query.data == "proxies":
        await query.edit_message_text("🔍 Fetching proxies from last account...")
        await run_get_proxies(query, context)

async def run_auto(query, context):
    session = AsyncScraper()
    try:
        email, password = await create_nullz_email()
        await query.edit_message_text(f"📧 Generated email: `{email}`\n\nSolving captcha + registering...", parse_mode="Markdown")

        acc = await register_webshare(session, email, password)
        if not acc:
            await query.edit_message_text("❌ Registration failed. Please try again later.")
            return

        data = load_accounts()
        data["accounts"].append(acc)
        data["accounts"] = data["accounts"][-20:]
        save_accounts(data)

        await query.edit_message_text(f"✅ Account created!\nEmail: `{email}`\n\nFetching proxies...", parse_mode="Markdown")

        proxies = await fetch_proxies(session, acc["token"], 10)
        if proxies:
            save_proxies_to_file(proxies, email)
            text = f"✅ *Success!* Got {len(proxies)} proxies:\n\n"
            text += "\n".join([f"`{p}`" for p in proxies])
            await query.edit_message_text(text, parse_mode="Markdown")
        else:
            await query.edit_message_text("✅ Account created but no proxies returned.")
    except Exception as e:
        log.error(f"Auto error: {e}")
        await query.edit_message_text(f"❌ Error: {str(e)[:200]}")
    finally:
        session.close()

async def receive_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = update.message.text.strip()
    if "@" not in email:
        await update.message.reply_text("❌ Invalid email. Please send a valid email:")
        return WAITING_EMAIL

    context.user_data["email"] = email
    await update.message.reply_text("🔑 Now send your *password*:", parse_mode="Markdown")
    return WAITING_PASSWORD

async def receive_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    email = context.user_data.get("email")

    if len(password) < 6:
        await update.message.reply_text("❌ Password too short. Send again:")
        return WAITING_PASSWORD

    await update.message.reply_text(f"⏳ Registering `{email}`...\nSolving captcha, please wait (1-5 min)...", parse_mode="Markdown")

    session = AsyncScraper()
    try:
        acc = await register_webshare(session, email, password)
        if not acc:
            await update.message.reply_text("❌ Registration failed. Try again later or use different email.")
            return ConversationHandler.END

        data = load_accounts()
        data["accounts"].append(acc)
        data["accounts"] = data["accounts"][-20:]
        save_accounts(data)

        await update.message.reply_text("✅ Account created successfully!\n\nFetching proxies...")

        proxies = await fetch_proxies(session, acc["token"], 10)
        if proxies:
            save_proxies_to_file(proxies, email)
            text = f"✅ *Got {len(proxies)} proxies:*\n\n"
            text += "\n".join([f"`{p}`" for p in proxies])
            await update.message.reply_text(text, parse_mode="Markdown")
        else:
            await update.message.reply_text("✅ Account created but failed to fetch proxies.")
    except Exception as e:
        log.error(f"Manual error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)[:200]}")
    finally:
        session.close()

    return ConversationHandler.END

async def run_get_proxies(query, context):
    data = load_accounts()
    accounts = data.get("accounts", [])
    if not accounts:
        await query.edit_message_text("❌ No accounts found. Register first using /start")
        return

    acc = accounts[-1]
    session = AsyncScraper()
    try:
        proxies = await fetch_proxies(session, acc["token"], 10)
        if proxies:
            text = f"✅ Proxies from `{acc['email']}`:\n\n"
            text += "\n".join([f"`{p}`" for p in proxies])
            await query.edit_message_text(text, parse_mode="Markdown")
        else:
            await query.edit_message_text("❌ Failed to fetch proxies. Token may be expired.")
    finally:
        session.close()

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END

# ==================== MAIN ====================

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="^manual$")],
        states={
            WAITING_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_email)],
            WAITING_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_password)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler, pattern="^(auto|proxies)$"))
    app.add_handler(conv_handler)

    print("Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()