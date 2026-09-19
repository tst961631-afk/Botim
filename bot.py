# -*- coding: utf-8 -*-
"""
Telegram Game Bot - Pydroid 3
Library: python-telegram-bot 22.x
Database: SQLite (automatic)

IMPORTANT:
1) Put your BotFather token in BOT_TOKEN below.
2) Put your Telegram numeric user ID in OWNER_ID.
3) Install: pip install -U python-telegram-bot
4) Run: python bot.py

This bot is a game/economy bot only.
No group moderation, anti-spam, locks, bans, or welcome system.
The chance games are non-wagering: users do not lose points and there is no cash gambling.
"""

import logging
import random
import sqlite3
import time
from datetime import datetime
from functools import wraps

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ============================================================
# CONFIG - ONLY CHANGE THESE TWO VALUES
# ============================================================

BOT_TOKEN = "8948581158:AAF7KaVHQf4wu_CIJi9XxVD5cNF3LNycXU0"
OWNER_ID = 7530457395

# ============================================================

DB_FILE = "game_bot.db"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ------------------------- DATABASE --------------------------

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            points INTEGER DEFAULT 0,
            tokens INTEGER DEFAULT 0,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            games INTEGER DEFAULT 0,
            lucky_plays INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            created_at INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            role TEXT NOT NULL,
            added_at INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            kind TEXT NOT NULL,
            actor_id INTEGER,
            target_id INTEGER,
            note TEXT DEFAULT '',
            created_at INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS gift_codes (
            code TEXT PRIMARY KEY,
            amount INTEGER NOT NULL,
            max_uses INTEGER NOT NULL,
            uses INTEGER DEFAULT 0,
            created_by INTEGER,
            expires_at INTEGER DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS gift_claims (
            code TEXT,
            user_id INTEGER,
            claimed_at INTEGER,
            PRIMARY KEY(code, user_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS shop (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price INTEGER NOT NULL,
            stock INTEGER DEFAULT -1,
            description TEXT DEFAULT '',
            active INTEGER DEFAULT 1
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            item_id INTEGER,
            price INTEGER,
            created_at INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS lucky_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            game TEXT,
            result TEXT,
            reward INTEGER,
            created_at INTEGER
        )
    """)

    # Owner is always an owner.
    cur.execute(
        "INSERT OR REPLACE INTO admins(user_id, role, added_at) VALUES(?,?,?)",
        (OWNER_ID, "owner", int(time.time()))
    )

    conn.commit()
    conn.close()


def register_user(tg_user):
    conn = db()
    now = int(time.time())
    conn.execute("""
        INSERT INTO users(user_id, username, first_name, created_at)
        VALUES(?,?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name
    """, (
        tg_user.id,
        tg_user.username or "",
        tg_user.first_name or "",
        now
    ))
    conn.commit()
    conn.close()


def get_user(user_id):
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row


def change_points(user_id, amount, kind, actor_id=None, target_id=None, note=""):
    conn = db()
    row = conn.execute("SELECT points FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        conn.close()
        return False, 0

    new_balance = row["points"] + amount
    if new_balance < 0:
        conn.close()
        return False, row["points"]

    conn.execute(
        "UPDATE users SET points=? WHERE user_id=?",
        (new_balance, user_id)
    )
    conn.execute("""
        INSERT INTO transactions
        (user_id, amount, kind, actor_id, target_id, note, created_at)
        VALUES(?,?,?,?,?,?,?)
    """, (user_id, amount, kind, actor_id, target_id, note, int(time.time())))
    conn.commit()
    conn.close()
    return True, new_balance


# -------------------------- ROLES ---------------------------

ROLE_NAMES = {
    "owner": "👑 مالک",
    "senior": "🛡 مدیر ارشد",
    "settings": "⚙️ مدیر تنظیمات",
    "economy": "💰 مدیر اقتصاد",
    "rewards": "🎁 مدیر جوایز",
    "shop": "🏪 مدیر فروشگاه",
    "game": "🎮 مدیر بازی",
    "stats": "📊 مدیر آمار",
}


def get_role(user_id):
    if user_id == OWNER_ID:
        return "owner"
    conn = db()
    row = conn.execute("SELECT role FROM admins WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row["role"] if row else None


def can_manage(user_id, allowed):
    role = get_role(user_id)
    if role == "owner":
        return True
    return role in allowed


def role_label(role):
    return ROLE_NAMES.get(role, role)


# ------------------------- KEYBOARDS -------------------------

def main_keyboard(user_id):
    buttons = [
        [
            InlineKeyboardButton("🎮 بازی", callback_data="menu_games"),
            InlineKeyboardButton("🏦 بانک", callback_data="menu_bank"),
        ],
        [
            InlineKeyboardButton("🛒 فروشگاه", callback_data="menu_shop"),
            InlineKeyboardButton("🎁 کد هدیه", callback_data="menu_gift"),
        ],
        [
            InlineKeyboardButton("🏆 رتبه‌بندی", callback_data="menu_rank"),
            InlineKeyboardButton("📊 آمار", callback_data="menu_stats"),
        ],
        [
            InlineKeyboardButton("👤 پروفایل", callback_data="menu_profile"),
            InlineKeyboardButton("📖 راهنما", callback_data="menu_help"),
        ],
    ]

    if get_role(user_id):
        buttons.append([
            InlineKeyboardButton("👑 پنل مدیریت", callback_data="admin_panel")
        ])

    buttons.append([InlineKeyboardButton("❌ بستن", callback_data="close")])
    return InlineKeyboardMarkup(buttons)


def back_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 بازگشت", callback_data="main")]
    ])


def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👑 مدیران", callback_data="admin_roles"),
            InlineKeyboardButton("💰 اقتصاد", callback_data="admin_economy"),
        ],
        [
            InlineKeyboardButton("🎁 جوایز", callback_data="admin_rewards"),
            InlineKeyboardButton("🛒 فروشگاه", callback_data="admin_shop"),
        ],
        [
            InlineKeyboardButton("🎮 بازی", callback_data="admin_game"),
            InlineKeyboardButton("📊 آمار", callback_data="admin_stats"),
        ],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="main")],
    ])


# -------------------------- HELPERS --------------------------

def fmt(n):
    return f"{int(n):,}"


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def level_for_xp(xp):
    # Simple progression: every 500 XP = one level.
    return max(1, xp // 500 + 1)


def add_xp(user_id, amount):
    conn = db()
    row = conn.execute("SELECT xp FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        conn.close()
        return
    xp = row["xp"] + amount
    level = level_for_xp(xp)
    conn.execute("UPDATE users SET xp=?, level=? WHERE user_id=?", (xp, level, user_id))
    conn.commit()
    conn.close()


def mention_user(row):
    name = row["first_name"] or row["username"] or str(row["user_id"])
    return name.replace("<", "").replace(">", "")


def reply_required(func):
    @wraps(func)
    async def wrapper(update, context):
        if not update.message.reply_to_message:
            await update.message.reply_text(
                "❗ این دستور باید با ریپلای روی پیام کاربر استفاده شود."
            )
            return
        return await func(update, context)
    return wrapper


# --------------------------- START --------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    text = (
        "🎮 <b>به بازی خوش آمدی!</b>\n\n"
        "💰 پوینت جمع کن، بازی کن، خرید کن و رتبه‌ات را بالا ببر.\n\n"
        "از دکمه‌های زیر استفاده کن یا در گپ دستورات متنی را بفرست."
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(update.effective_user.id)
    )


# ----------------------- PROFILE / BANK ---------------------

async def show_profile(update, user_id):
    u = get_user(user_id)
    if not u:
        return "❌ کاربر ثبت نشده است."

    username = f"@{u['username']}" if u["username"] else "بدون یوزرنیم"
    return (
        f"👤 <b>پروفایل</b>\n\n"
        f"نام: {mention_user(u)}\n"
        f"یوزرنیم: {username}\n"
        f"🆔 ID: <code>{u['user_id']}</code>\n\n"
        f"💰 پوینت: <b>{fmt(u['points'])}</b>\n"
        f"💎 توکن: <b>{fmt(u['tokens'])}</b>\n"
        f"⭐ XP: <b>{fmt(u['xp'])}</b>\n"
        f"🏅 Level: <b>{u['level']}</b>\n"
        f"🎮 بازی‌ها: {fmt(u['games'])}\n"
        f"🏆 بردها: {fmt(u['wins'])}"
    )


async def bank_text(user_id):
    u = get_user(user_id)
    return (
        "🏦 <b>بانک</b>\n\n"
        f"💰 موجودی: <b>{fmt(u['points'])}</b> پوینت\n"
        f"💎 توکن: <b>{fmt(u['tokens'])}</b>\n\n"
        "برای انتقال به کاربر، روی پیام او ریپلای کن و بنویس:\n"
        "<code>انتقال 1000</code>"
    )


# --------------------------- GAMES --------------------------

async def games_menu(update, context):
    text = (
        "🎮 <b>بازی‌ها</b>\n\n"
        "🎲 تاس روزانه — دریافت جایزه تصادفی بدون شرط‌بندی\n"
        "🎡 چرخ شانس — جایزه تصادفی بدون کم‌شدن موجودی\n"
        "🎁 جعبه شانس — یک جایزه رایگان\n\n"
        "هیچ‌کدام نیاز به پرداخت یا شرط‌بندی ندارند."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎲 تاس روزانه", callback_data="game_dice")],
        [InlineKeyboardButton("🎡 چرخ شانس", callback_data="game_wheel")],
        [InlineKeyboardButton("🎁 جعبه شانس", callback_data="game_box")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="main")],
    ])
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def play_lucky(update, context, game):
    uid = update.effective_user.id
    register_user(update.effective_user)

    # Simple daily cooldown per game.
    conn = db()
    last = conn.execute("""
        SELECT created_at FROM lucky_log
        WHERE user_id=? AND game=?
        ORDER BY id DESC LIMIT 1
    """, (uid, game)).fetchone()

    if last and int(time.time()) - last["created_at"] < 24 * 3600:
        remaining = 24 * 3600 - (int(time.time()) - last["created_at"])
        h = remaining // 3600
        m = (remaining % 3600) // 60
        conn.close()
        return f"⏳ این بازی را امروز انجام داده‌ای.\nزمان باقی‌مانده: {h} ساعت و {m} دقیقه"

    rewards = [100, 250, 500, 1000, 2500, 5000]
    weights = [30, 25, 20, 12, 8, 5]
    reward = random.choices(rewards, weights=weights, k=1)[0]

    labels = {
        "dice": "🎲 تاس روزانه",
        "wheel": "🎡 چرخ شانس",
        "box": "🎁 جعبه شانس",
    }

    conn.execute("""
        INSERT INTO lucky_log(user_id, game, result, reward, created_at)
        VALUES(?,?,?,?,?)
    """, (uid, game, str(reward), reward, int(time.time())))
    conn.execute("""
        UPDATE users
        SET points=points+?, games=games+1, lucky_plays=lucky_plays+1, wins=wins+1
        WHERE user_id=?
    """, (reward, uid))
    conn.commit()
    conn.close()

    add_xp(uid, 50)

    return (
        f"{labels.get(game, '🎮 بازی')}\n\n"
        f"🎉 جایزه تو: <b>{fmt(reward)}</b> پوینت\n"
        f"💰 موجودی جدید: <b>{fmt(get_user(uid)['points'])}</b>\n"
        f"⭐ +50 XP"
    )


# ------------------------- SHOP -----------------------------

async def shop_text():
    conn = db()
    rows = conn.execute("SELECT * FROM shop WHERE active=1 ORDER BY id").fetchall()
    conn.close()

    if not rows:
        return "🛒 <b>فروشگاه</b>\n\nفعلاً آیتمی برای فروش وجود ندارد."

    lines = ["🛒 <b>فروشگاه</b>\n"]
    for r in rows:
        stock = "∞" if r["stock"] < 0 else fmt(r["stock"])
        lines.append(
            f"#{r['id']} — <b>{r['name']}</b>\n"
            f"💰 قیمت: {fmt(r['price'])}\n"
            f"📦 موجودی: {stock}\n"
            f"📝 {r['description']}\n"
            f"برای خرید: <code>خرید {r['id']}</code>\n"
        )
    return "\n".join(lines)


async def buy_item(update, context, item_id):
    uid = update.effective_user.id
    register_user(update.effective_user)

    conn = db()
    item = conn.execute(
        "SELECT * FROM shop WHERE id=? AND active=1", (item_id,)
    ).fetchone()

    if not item:
        conn.close()
        await update.message.reply_text("❌ آیتم پیدا نشد.")
        return

    if item["stock"] == 0:
        conn.close()
        await update.message.reply_text("❌ این آیتم تمام شده است.")
        return

    user = conn.execute("SELECT points FROM users WHERE user_id=?", (uid,)).fetchone()
    if user["points"] < item["price"]:
        conn.close()
        await update.message.reply_text("❌ موجودی پوینت کافی نیست.")
        return

    conn.execute(
        "UPDATE users SET points=points-? WHERE user_id=?",
        (item["price"], uid)
    )
    if item["stock"] > 0:
        conn.execute(
            "UPDATE shop SET stock=stock-1 WHERE id=?", (item_id,)
        )

    conn.execute("""
        INSERT INTO purchases(user_id,item_id,price,created_at)
        VALUES(?,?,?,?)
    """, (uid, item_id, item["price"], int(time.time())))

    conn.execute("""
        INSERT INTO transactions
        (user_id, amount, kind, actor_id, target_id, note, created_at)
        VALUES(?,?,?,?,?,?,?)
    """, (
        uid, -item["price"], "purchase", uid, None,
        item["name"], int(time.time())
    ))
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ خرید انجام شد!\n\n"
        f"🛒 {item['name']}\n"
        f"💰 هزینه: {fmt(item['price'])}\n"
        f"💳 موجودی: {fmt(get_user(uid)['points'])}"
    )


# ---------------------- BANK COMMANDS -----------------------

@reply_required
async def admin_deposit(update, context):
    uid = update.effective_user.id
    if not can_manage(uid, {"senior", "economy"}):
        await update.message.reply_text("⛔ دسترسی نداری.")
        return

    args = context.args
    if len(args) != 1 or not args[0].isdigit():
        await update.message.reply_text("فرمت: واریز 50000\n(حتماً با ریپلای)")
        return

    amount = int(args[0])
    target = update.message.reply_to_message.from_user
    register_user(target)

    ok, balance = change_points(
        target.id, amount, "admin_deposit",
        actor_id=uid, target_id=target.id, note="واریز توسط مدیر"
    )
    if not ok:
        await update.message.reply_text("❌ تراکنش انجام نشد.")
        return

    await update.message.reply_text(
        f"✅ واریز انجام شد.\n\n"
        f"👤 {target.first_name}\n"
        f"➕ {fmt(amount)} پوینت\n"
        f"💰 موجودی جدید: {fmt(balance)}"
    )


@reply_required
async def admin_withdraw(update, context):
    uid = update.effective_user.id
    if not can_manage(uid, {"senior", "economy"}):
        await update.message.reply_text("⛔ دسترسی نداری.")
        return

    args = context.args
    if len(args) != 1 or not args[0].isdigit():
        await update.message.reply_text("فرمت: برداشت 20000\n(حتماً با ریپلای)")
        return

    amount = int(args[0])
    target = update.message.reply_to_message.from_user
    register_user(target)

    ok, balance = change_points(
        target.id, -amount, "admin_withdraw",
        actor_id=uid, target_id=target.id, note="برداشت توسط مدیر"
    )
    if not ok:
        await update.message.reply_text("❌ موجودی کاربر کافی نیست.")
        return

    await update.message.reply_text(
        f"✅ برداشت انجام شد.\n\n"
        f"👤 {target.first_name}\n"
        f"➖ {fmt(amount)} پوینت\n"
        f"💰 موجودی جدید: {fmt(balance)}"
    )


@reply_required
async def transfer_reply(update, context):
    sender = update.effective_user
    register_user(sender)

    args = context.args
    if len(args) != 1 or not args[0].isdigit():
        await update.message.reply_text(
            "فرمت صحیح:\nانتقال 1000\n\nو باید روی پیام گیرنده ریپلای کنی."
        )
        return

    amount = int(args[0])
    if amount <= 0:
        await update.message.reply_text("❌ مبلغ باید بیشتر از صفر باشد.")
        return

    target = update.message.reply_to_message.from_user
    register_user(target)

    if target.id == sender.id:
        await update.message.reply_text("❌ نمی‌توانی به خودت انتقال بدهی.")
        return

    conn = db()
    sender_row = conn.execute(
        "SELECT points FROM users WHERE user_id=?", (sender.id,)
    ).fetchone()

    if sender_row["points"] < amount:
        conn.close()
        await update.message.reply_text("❌ موجودی کافی نیست.")
        return

    conn.execute(
        "UPDATE users SET points=points-? WHERE user_id=?",
        (amount, sender.id)
    )
    conn.execute(
        "UPDATE users SET points=points+? WHERE user_id=?",
        (amount, target.id)
    )

    ts = int(time.time())
    conn.execute("""
        INSERT INTO transactions
        (user_id, amount, kind, actor_id, target_id, note, created_at)
        VALUES(?,?,?,?,?,?,?)
    """, (sender.id, -amount, "transfer_out", sender.id, target.id, "انتقال بانکی", ts))

    conn.execute("""
        INSERT INTO transactions
        (user_id, amount, kind, actor_id, target_id, note, created_at)
        VALUES(?,?,?,?,?,?,?)
    """, (target.id, amount, "transfer_in", sender.id, target.id, "دریافت بانکی", ts))

    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"💸 <b>انتقال بانکی</b>\n\n"
        f"👤 فرستنده: {sender.first_name}\n"
        f"👤 گیرنده: {target.first_name}\n"
        f"💰 مبلغ: <b>{fmt(amount)}</b>\n\n"
        f"✅ انتقال انجام شد.",
        parse_mode=ParseMode.HTML
    )


# ------------------------- GIFTS ----------------------------

async def claim_gift(update, context, code):
    uid = update.effective_user.id
    register_user(update.effective_user)

    conn = db()
    gift = conn.execute("SELECT * FROM gift_codes WHERE code=?", (code,)).fetchone()

    if not gift:
        conn.close()
        await update.message.reply_text("❌ کد هدیه معتبر نیست.")
        return

    if gift["expires_at"] and gift["expires_at"] < int(time.time()):
        conn.close()
        await update.message.reply_text("⏰ این کد منقضی شده است.")
        return

    if gift["uses"] >= gift["max_uses"]:
        conn.close()
        await update.message.reply_text("❌ ظرفیت این کد تمام شده است.")
        return

    already = conn.execute(
        "SELECT 1 FROM gift_claims WHERE code=? AND user_id=?",
        (code, uid)
    ).fetchone()

    if already:
        conn.close()
        await update.message.reply_text("❌ قبلاً از این کد استفاده کرده‌ای.")
        return

    conn.execute(
        "UPDATE users SET points=points+? WHERE user_id=?",
        (gift["amount"], uid)
    )
    conn.execute(
        "UPDATE gift_codes SET uses=uses+1 WHERE code=?", (code,)
    )
    conn.execute(
        "INSERT INTO gift_claims(code,user_id,claimed_at) VALUES(?,?,?)",
        (code, uid, int(time.time()))
    )
    conn.execute("""
        INSERT INTO transactions
        (user_id, amount, kind, actor_id, target_id, note, created_at)
        VALUES(?,?,?,?,?,?,?)
    """, (uid, gift["amount"], "gift", None, uid, code, int(time.time())))

    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"🎁 کد قبول شد!\n\n"
        f"➕ {fmt(gift['amount'])} پوینت\n"
        f"💰 موجودی: {fmt(get_user(uid)['points'])}"
    )


# ---------------------- ADMIN COMMANDS ----------------------

async def admin_panel(update, context):
    if not get_role(update.effective_user.id):
        await update.message.reply_text("⛔ دسترسی نداری.")
        return

    await update.message.reply_text(
        "👑 <b>پنل مدیریت</b>\n\n"
        "هر نقش فقط بخش مربوط به خودش را کنترل می‌کند.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_keyboard()
    )


async def add_admin(update, context):
    uid = update.effective_user.id
    if uid != OWNER_ID:
        await update.message.reply_text("⛔ فقط مالک می‌تواند مدیر اضافه کند.")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text(
            "روی پیام شخص ریپلای کن و بنویس:\n"
            "مدیر اقتصاد"
        )
        return

    text = normalize(update.message.text)
    role = None
    for key, title in [
        ("senior", "مدیر ارشد"),
        ("settings", "مدیر تنظیمات"),
        ("economy", "مدیر اقتصاد"),
        ("rewards", "مدیر جوایز"),
        ("shop", "مدیر فروشگاه"),
        ("game", "مدیر بازی"),
        ("stats", "مدیر آمار"),
    ]:
        if title in text:
            role = key
            break

    if not role:
        await update.message.reply_text(
            "نقش را مشخص کن:\n"
            "مدیر ارشد\nمدیر تنظیمات\nمدیر اقتصاد\nمدیر جوایز\n"
            "مدیر فروشگاه\nمدیر بازی\nمدیر آمار"
        )
        return

    target = update.message.reply_to_message.from_user
    register_user(target)

    conn = db()
    conn.execute(
        "INSERT OR REPLACE INTO admins(user_id,role,added_at) VALUES(?,?,?)",
        (target.id, role, int(time.time()))
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ مدیر اضافه شد.\n\n"
        f"👤 {target.first_name}\n"
        f"👑 نقش: {role_label(role)}"
    )


@reply_required
async def remove_admin(update, context):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ فقط مالک می‌تواند مدیر حذف کند.")
        return

    target = update.message.reply_to_message.from_user
    if target.id == OWNER_ID:
        await update.message.reply_text("❌ مالک قابل حذف نیست.")
        return

    conn = db()
    conn.execute("DELETE FROM admins WHERE user_id=?", (target.id,))
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ دسترسی مدیریتی {target.first_name} حذف شد."
    )


async def create_gift(update, context):
    uid = update.effective_user.id
    if not can_manage(uid, {"owner", "rewards"}):
        await update.message.reply_text("⛔ دسترسی نداری.")
        return

    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "فرمت:\n"
            "کدسازی CODE AMOUNT MAX_USES\n\n"
            "مثال:\n"
            "کدسازی KI4N2026 50000 100"
        )
        return

    code = args[0].upper()
    if not args[1].isdigit() or not args[2].isdigit():
        await update.message.reply_text("❌ مبلغ و ظرفیت باید عدد باشند.")
        return

    amount = int(args[1])
    max_uses = int(args[2])

    conn = db()
    try:
        conn.execute("""
            INSERT INTO gift_codes(code,amount,max_uses,created_by)
            VALUES(?,?,?,?)
        """, (code, amount, max_uses, uid))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        await update.message.reply_text("❌ این کد از قبل وجود دارد.")
        return
    conn.close()

    await update.message.reply_text(
        f"🎁 کد ساخته شد:\n\n"
        f"<code>{code}</code>\n"
        f"💰 مبلغ: {fmt(amount)}\n"
        f"👥 ظرفیت: {max_uses}",
        parse_mode=ParseMode.HTML
    )


async def add_shop_item(update, context):
    uid = update.effective_user.id
    if not can_manage(uid, {"owner", "shop"}):
        await update.message.reply_text("⛔ دسترسی نداری.")
        return

    # Format: آیتم NAME | PRICE | STOCK | DESCRIPTION
    raw = update.message.text
    parts = raw.split("|")
    if len(parts) < 4:
        await update.message.reply_text(
            "فرمت:\n"
            "آیتم نام | قیمت | موجودی | توضیحات\n\n"
            "مثال:\n"
            "آیتم VIP | 50000 | 100 | دسترسی ویژه"
        )
        return

    try:
        name = parts[0].replace("آیتم", "", 1).strip()
        price = int(parts[1].strip())
        stock = int(parts[2].strip())
        desc = parts[3].strip()
    except ValueError:
        await update.message.reply_text("❌ قیمت و موجودی باید عدد باشند.")
        return

    conn = db()
    conn.execute(
        "INSERT INTO shop(name,price,stock,description) VALUES(?,?,?,?)",
        (name, price, stock, desc)
    )
    conn.commit()
    conn.close()

    await update.message.reply_text("✅ آیتم فروشگاه اضافه شد.")


# -------------------------- STATS ---------------------------

async def stats_text():
    conn = db()
    users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    total_points = conn.execute("SELECT COALESCE(SUM(points),0) s FROM users").fetchone()["s"]
    games = conn.execute("SELECT COALESCE(SUM(games),0) s FROM users").fetchone()["s"]
    purchases = conn.execute("SELECT COUNT(*) c FROM purchases").fetchone()["c"]
    conn.close()

    return (
        "📊 <b>آمار بازی</b>\n\n"
        f"👥 کاربران: <b>{fmt(users)}</b>\n"
        f"💰 مجموع پوینت کاربران: <b>{fmt(total_points)}</b>\n"
        f"🎮 تعداد بازی‌ها: <b>{fmt(games)}</b>\n"
        f"🛒 خریدها: <b>{fmt(purchases)}</b>"
    )


async def rank_text():
    conn = db()
    rows = conn.execute("""
        SELECT * FROM users
        ORDER BY points DESC
        LIMIT 10
    """).fetchall()
    conn.close()

    if not rows:
        return "🏆 هنوز کاربری ثبت نشده است."

    lines = ["🏆 <b>۱۰ نفر برتر</b>\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, r in enumerate(rows, 1):
        medal = medals[i-1] if i <= 3 else f"{i}."
        lines.append(
            f"{medal} {mention_user(r)} — 💰 {fmt(r['points'])}"
        )
    return "\n".join(lines)


async def transaction_history(user_id):
    conn = db()
    rows = conn.execute("""
        SELECT * FROM transactions
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 15
    """, (user_id,)).fetchall()
    conn.close()

    if not rows:
        return "📜 هنوز تراکنشی ثبت نشده است."

    lines = ["📜 <b>آخرین تراکنش‌ها</b>\n"]
    for r in rows:
        sign = "+" if r["amount"] > 0 else ""
        lines.append(
            f"{sign}{fmt(r['amount'])} — {r['kind']}"
            + (f" — {r['note']}" if r["note"] else "")
        )
    return "\n".join(lines)


# ------------------------ TEXT PARSER -----------------------

def normalize(text):
    return (
        text.replace("ي", "ی")
        .replace("ى", "ی")
        .replace("ك", "ک")
        .strip()
    )


async def text_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    register_user(update.effective_user)
    text = normalize(update.message.text)
    low = text.lower()

    # Do not interfere with regular conversation.
    if low in {"منو", "پنل", "شروع"}:
        await start(update, context)
        return

    if low in {"راهنما", "help", "/help"}:
        await help_command(update, context)
        return

    if low in {"پروفایل", "پروفایل من"}:
        await update.message.reply_text(
            await show_profile(update, update.effective_user.id),
            parse_mode=ParseMode.HTML
        )
        return

    if low in {"موجودی", "بانک"}:
        await update.message.reply_text(
            await bank_text(update.effective_user.id),
            parse_mode=ParseMode.HTML
        )
        return

    if low == "بازی":
        await games_menu(update, context)
        return

    if low == "فروشگاه":
        await update.message.reply_text(
            await shop_text(),
            parse_mode=ParseMode.HTML
        )
        return

    if low == "رتبه":
        await update.message.reply_text(
            await rank_text(),
            parse_mode=ParseMode.HTML
        )
        return

    if low == "آمار":
        await update.message.reply_text(
            await stats_text(),
            parse_mode=ParseMode.HTML
        )
        return

    if low in {"تاریخچه", "تاریخچه بانک"}:
        await update.message.reply_text(
            await transaction_history(update.effective_user.id),
            parse_mode=ParseMode.HTML
        )
        return

    if low.startswith("انتقال "):
        # Let dedicated command-style handler logic run here.
        args = text.split()
        if len(args) == 2:
            context.args = args[1:]
            await transfer_reply(update, context)
        else:
            await update.message.reply_text("فرمت: انتقال 1000 (با ریپلای)")
        return

    if low.startswith("واریز "):
        args = text.split()
        if len(args) == 2:
            context.args = args[1:]
            await admin_deposit(update, context)
        return

    if low.startswith("برداشت "):
        args = text.split()
        if len(args) == 2:
            context.args = args[1:]
            await admin_withdraw(update, context)
        return

    if low.startswith("خرید "):
        args = text.split()
        if len(args) == 2 and args[1].isdigit():
            await buy_item(update, context, int(args[1]))
        return

    if low.startswith("کد "):
        args = text.split()
        if len(args) == 2:
            await claim_gift(update, context, args[1].upper())
        return

    if low.startswith("مدیر ") or low.startswith("مدیر ارشد"):
        await add_admin(update, context)
        return

    if low == "حذف مدیر":
        await remove_admin(update, context)
        return

    if low.startswith("کدسازی "):
        context.args = text.split()[1:]
        await create_gift(update, context)
        return

    if low.startswith("آیتم "):
        await add_shop_item(update, context)
        return


# --------------------------- HELP ---------------------------

async def help_command(update, context):
    text = (
        "📖 <b>راهنمای کامل</b>\n\n"
        "👤 <b>کاربری</b>\n"
        "• پروفایل\n"
        "• موجودی\n"
        "• بانک\n"
        "• بازی\n"
        "• فروشگاه\n"
        "• رتبه\n"
        "• آمار\n"
        "• تاریخچه\n\n"
        "💸 <b>انتقال بانکی</b>\n"
        "روی پیام گیرنده ریپلای کن:\n"
        "<code>انتقال 1000</code>\n\n"
        "🛒 <b>خرید</b>\n"
        "<code>خرید 1</code>\n\n"
        "🎁 <b>کد هدیه</b>\n"
        "<code>کد KI4N2026</code>\n\n"
        "👑 <b>مدیریت</b>\n"
        "واریز و برداشت فقط با ریپلای روی کاربر انجام می‌شود.\n"
        "مثال:\n"
        "<code>واریز 50000</code>\n"
        "<code>برداشت 20000</code>\n\n"
        "🎮 بازی‌های شانس رایگان هستند و در آن‌ها شرط‌بندی یا از دست دادن پوینت وجود ندارد."
    )
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=back_keyboard()
    )


# ---------------------- CALLBACK HANDLER --------------------

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    uid = q.from_user.id
    register_user(q.from_user)
    data = q.data

    if data == "main":
        await q.edit_message_text(
            "🎮 <b>منوی اصلی</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(uid)
        )
        return

    if data == "close":
        await q.edit_message_text("❌ منو بسته شد.")
        return

    if data == "menu_profile":
        await q.edit_message_text(
            await show_profile(update, uid),
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard()
        )
        return

    if data == "menu_bank":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📜 تاریخچه", callback_data="bank_history")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="main")],
        ])
        await q.edit_message_text(
            await bank_text(uid),
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )
        return

    if data == "bank_history":
        await q.edit_message_text(
            await transaction_history(uid),
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard()
        )
        return

    if data == "menu_games":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎲 تاس روزانه", callback_data="game_dice")],
            [InlineKeyboardButton("🎡 چرخ شانس", callback_data="game_wheel")],
            [InlineKeyboardButton("🎁 جعبه شانس", callback_data="game_box")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="main")],
        ])
        await q.edit_message_text(
            "🎮 <b>بازی‌ها</b>\n\n"
            "هر بازی روزی یک‌بار قابل استفاده است و جایزه رایگان می‌دهد.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )
        return

    if data.startswith("game_"):
        game = data.replace("game_", "", 1)
        result = await play_lucky(update, context, game)
        await q.edit_message_text(
            result,
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard()
        )
        return

    if data == "menu_shop":
        await q.edit_message_text(
            await shop_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard()
        )
        return

    if data == "menu_gift":
        await q.edit_message_text(
            "🎁 <b>کد هدیه</b>\n\n"
            "در گپ بنویس:\n"
            "<code>کد CODE</code>\n\n"
            "مثال: <code>کد KI4N2026</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard()
        )
        return

    if data == "menu_rank":
        await q.edit_message_text(
            await rank_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard()
        )
        return

    if data == "menu_stats":
        await q.edit_message_text(
            await stats_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard()
        )
        return

    if data == "menu_help":
        # Inline equivalent of help.
        text = (
            "📖 <b>راهنما</b>\n\n"
            "💰 موجودی — نمایش موجودی\n"
            "👤 پروفایل — نمایش مشخصات\n"
            "🎮 بازی — بازی‌های روزانه\n"
            "🛒 فروشگاه — نمایش آیتم‌ها\n"
            "🏆 رتبه — نمایش برترین‌ها\n"
            "📜 تاریخچه — تراکنش‌ها\n\n"
            "💸 انتقال فقط با ریپلای:\n"
            "<code>انتقال 1000</code>\n\n"
            "👑 واریز/برداشت مدیران هم فقط با ریپلای انجام می‌شود."
        )
        await q.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=back_keyboard()
        )
        return

    if data == "admin_panel":
        if not get_role(uid):
            await q.edit_message_text("⛔ دسترسی نداری.", reply_markup=back_keyboard())
            return
        await q.edit_message_text(
            "👑 <b>پنل مدیریت</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_keyboard()
        )
        return

    if data == "admin_roles":
        if uid != OWNER_ID:
            await q.answer("فقط مالک به مدیریت نقش‌ها دسترسی دارد.", show_alert=True)
            return
        conn = db()
        rows = conn.execute("SELECT * FROM admins ORDER BY role").fetchall()
        conn.close()
        lines = ["👑 <b>مدیران</b>\n"]
        for r in rows:
            lines.append(f"• <code>{r['user_id']}</code> — {role_label(r['role'])}")
        lines.append("\nبرای افزودن مدیر، روی پیام او ریپلای کن و بنویس «مدیر اقتصاد».")
        await q.edit_message_text(
            "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=back_keyboard()
        )
        return

    if data == "admin_economy":
        if not can_manage(uid, {"senior", "economy"}):
            await q.answer("دسترسی نداری.", show_alert=True)
            return
        await q.edit_message_text(
            "💰 <b>مدیریت اقتصاد</b>\n\n"
            "واریز/برداشت فقط با ریپلای:\n"
            "<code>واریز 50000</code>\n"
            "<code>برداشت 20000</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard()
        )
        return

    if data == "admin_rewards":
        if not can_manage(uid, {"senior", "rewards"}):
            await q.answer("دسترسی نداری.", show_alert=True)
            return
        await q.edit_message_text(
            "🎁 <b>مدیریت جوایز</b>\n\n"
            "ساخت کد:\n"
            "<code>کدسازی CODE AMOUNT MAX_USES</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard()
        )
        return

    if data == "admin_shop":
        if not can_manage(uid, {"senior", "shop"}):
            await q.answer("دسترسی نداری.", show_alert=True)
            return
        await q.edit_message_text(
            "🛒 <b>مدیریت فروشگاه</b>\n\n"
            "افزودن آیتم:\n"
            "<code>آیتم نام | قیمت | موجودی | توضیحات</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard()
        )
        return

    if data == "admin_game":
        if not can_manage(uid, {"senior", "game"}):
            await q.answer("دسترسی نداری.", show_alert=True)
            return
        await q.edit_message_text(
            "🎮 <b>مدیریت بازی</b>\n\n"
            "بازی‌های شانس فعلی رایگان و بدون شرط‌بندی هستند.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard()
        )
        return

    if data == "admin_stats":
        if not can_manage(uid, {"senior", "stats"}):
            await q.answer("دسترسی نداری.", show_alert=True)
            return
        await q.edit_message_text(
            await stats_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard()
        )
        return


# -------------------------- COMMANDS ------------------------

async def cmd_start(update, context):
    await start(update, context)


async def cmd_help(update, context):
    await help_command(update, context)


async def cmd_panel(update, context):
    await admin_panel(update, context)


async def cmd_games(update, context):
    await games_menu(update, context)


async def cmd_profile(update, context):
    register_user(update.effective_user)
    await update.message.reply_text(
        await show_profile(update, update.effective_user.id),
        parse_mode=ParseMode.HTML
    )


async def cmd_balance(update, context):
    register_user(update.effective_user)
    await update.message.reply_text(
        await bank_text(update.effective_user.id),
        parse_mode=ParseMode.HTML
    )


async def cmd_transfer(update, context):
    await transfer_reply(update, context)


async def cmd_deposit(update, context):
    await admin_deposit(update, context)


async def cmd_withdraw(update, context):
    await admin_withdraw(update, context)


# ---------------------------- MAIN --------------------------

def main():
    if BOT_TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        print("\n[ERROR] BOT_TOKEN را داخل bot.py وارد کن.\n")
        return

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # Slash commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("panel", cmd_panel))
    app.add_handler(CommandHandler("games", cmd_games))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("transfer", cmd_transfer))
    app.add_handler(CommandHandler("deposit", cmd_deposit))
    app.add_handler(CommandHandler("withdraw", cmd_withdraw))

    # Inline buttons
    app.add_handler(CallbackQueryHandler(callbacks))

    # Persian text commands
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_commands))

    print("====================================")
    print("🎮 Game Bot is running...")
    print("📁 Database:", DB_FILE)
    print("====================================")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
