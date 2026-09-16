# -*- coding: utf-8 -*-
"""
🦝 رِیو (Rivo) — بازی اقتصادی تلگرامی با محوریت راکون و ویلو
یک فایل کامل: SQLite + پنل ادمین + شهر + غارت مکانی + مارکت شهردار
دستورات بدون / (مثال: پروفایل | ویلو | شهر)
"""
from __future__ import annotations
import json, os, re, time, logging, random, asyncio, sqlite3, threading
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters,
)
from telegram.constants import ChatType

BOT_TOKEN = "8727762178:AAGrdb5XFjhkcdoOEIFy1s8U71idRpN0DX8"
ADMIN_ID = 7530457395
# اگر اینترنت به api.telegram.org وصل نمی‌شود، پروکسی بگذار (مثال):
# PROXY_URL = "http://127.0.0.1:10809"
# PROXY_URL = "socks5://127.0.0.1:1080"
PROXY_URL = None
DB_PATH = "rivo_game.db"
TZ = timezone(timedelta(hours=3, minutes=30))  # تهران

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("rivo")

_lock = threading.RLock()

# ───────────────────────── DB ─────────────────────────
def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def tx():
    with _lock:
        conn = db()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db():
    with tx() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                name TEXT,
                willow INTEGER DEFAULT 0,
                bank INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1,
                xp INTEGER DEFAULT 0,
                willow_collected INTEGER DEFAULT 0,
                ops_ok INTEGER DEFAULT 0,
                ops_fail INTEGER DEFAULT 0,
                invites INTEGER DEFAULT 0,
                missions_done INTEGER DEFAULT 0,
                raccoon_count INTEGER DEFAULT 1,
                status TEXT DEFAULT 'active',
                joined_at REAL,
                last_active REAL,
                last_willow REAL DEFAULT 0,
                last_gather REAL DEFAULT 0,
                last_raid REAL DEFAULT 0,
                jail_until REAL DEFAULT 0,
                referred_by INTEGER,
                street_rescues INTEGER DEFAULT 0,
                inv TEXT DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS raccoons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER,
                species TEXT DEFAULT 'خاکستری',
                rarity TEXT DEFAULT 'معمولی',
                level INTEGER DEFAULT 1,
                xp INTEGER DEFAULT 0,
                power INTEGER DEFAULT 10,
                speed INTEGER DEFAULT 10,
                luck INTEGER DEFAULT 10,
                capacity INTEGER DEFAULT 10,
                value INTEGER DEFAULT 1000,
                is_main INTEGER DEFAULT 1,
                photo_file_id TEXT,
                FOREIGN KEY(owner_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS cities (
                chat_id INTEGER PRIMARY KEY,
                title TEXT,
                level INTEGER DEFAULT 1,
                xp INTEGER DEFAULT 0,
                treasury INTEGER DEFAULT 0,
                mayor_id INTEGER,
                willow_users TEXT DEFAULT '[]',
                market TEXT DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS factories (
                user_id INTEGER PRIMARY KEY,
                level INTEGER DEFAULT 1,
                busy_until REAL DEFAULT 0,
                ready INTEGER DEFAULT 0,
                reward INTEGER DEFAULT 0,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                kind TEXT,
                amount INTEGER,
                meta TEXT,
                ts REAL
            );
            CREATE TABLE IF NOT EXISTS gift_codes (
                code TEXT PRIMARY KEY,
                amount INTEGER,
                uses_left INTEGER,
                max_per_user INTEGER DEFAULT 1,
                expires_at REAL,
                active INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS gift_uses (
                code TEXT,
                user_id INTEGER,
                ts REAL,
                PRIMARY KEY(code, user_id)
            );
            CREATE TABLE IF NOT EXISTS raid_places (
                id TEXT PRIMARY KEY,
                name TEXT,
                min_level INTEGER DEFAULT 1,
                pool INTEGER DEFAULT 100000,
                stages INTEGER DEFAULT 3,
                fail_chance REAL DEFAULT 0.2,
                jail_sec INTEGER DEFAULT 3600,
                cooldown INTEGER DEFAULT 1800,
                active INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS shop_items (
                id TEXT PRIMARY KEY,
                name TEXT,
                kind TEXT,
                price INTEGER,
                effect TEXT,
                stock INTEGER DEFAULT -1,
                photo_file_id TEXT,
                active INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS street_raccoons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                rarity TEXT,
                price INTEGER,
                power INTEGER,
                photo_file_id TEXT,
                active INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT,
                detail TEXT,
                ts REAL
            );
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY
            );
            """
        )
        c.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (ADMIN_ID,))
        defaults = {
            "willow_min": "3000",
            "willow_max": "7000",
            "willow_cd": "300",
            "willow_cd_min": "120",
            "willow_cd_per_city_lv": "15",
            "willow_cd_per_rescue": "5",
            "start_willow": "15000",
            "transfer_tax": "0.02",
            "transfer_max": "0",
            "bank_cap": "50000000",
            "gather_cd": "600",
            "factory_base_time": "1800",
            "factory_base_reward": "25000",
            "city_xp_per_willow": "1",
            "currency": "ویلو",
        }
        for k, v in defaults.items():
            c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
        # default raid places
        places = [
            ("alley", "کوچه پشتی", 1, 150000, 3, 0.18, 1800, 1200),
            ("market", "انبار بازار", 3, 400000, 4, 0.25, 3600, 1800),
            ("vault", "صندوق مرکزی", 5, 900000, 5, 0.32, 5400, 2400),
        ]
        for p in places:
            c.execute(
                "INSERT OR IGNORE INTO raid_places(id,name,min_level,pool,stages,fail_chance,jail_sec,cooldown,active) VALUES (?,?,?,?,?,?,?,?,1)",
                p,
            )
        # shop defaults
        shop = [
            ("food_s", "خوراک ساده", "food", 5000, "xp:5", -1),
            ("food_m", "خوراک مرغوب", "food", 15000, "xp:20", -1),
            ("food_l", "ضیافت راکون", "food", 40000, "xp:60", -1),
            ("boost_cd", "شتاب ویلو", "boost", 60000, "cd_half:1", -1),
            ("jail_card", "کارت آزادی", "boost", 80000, "jail_pass:1", -1),
        ]
        for s in shop:
            c.execute(
                "INSERT OR IGNORE INTO shop_items(id,name,kind,price,effect,stock,active) VALUES (?,?,?,?,?,?,1)",
                s,
            )
        streets = [
            ("راکون کوچه", "معمولی", 25000, 12),
            ("راکون مهتابی", "کمیاب", 80000, 22),
            ("راکون سایه", "نادر", 200000, 35),
        ]
        for s in streets:
            c.execute(
                "INSERT OR IGNORE INTO street_raccoons(name,rarity,price,power,active) VALUES (?,?,?,?,1)",
                s,
            )


def sget(key, default=None):
    with tx() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default


def sset(key, value):
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (key, str(value)))


def sint(key, default=0):
    try:
        return int(float(sget(key, default)))
    except Exception:
        return int(default)


def sfloat(key, default=0.0):
    try:
        return float(sget(key, default))
    except Exception:
        return float(default)


def is_admin(uid):
    with tx() as c:
        r = c.execute("SELECT 1 FROM admins WHERE user_id=?", (int(uid),)).fetchone()
        return bool(r) or int(uid) == ADMIN_ID


def is_main(uid):
    return int(uid) == ADMIN_ID


def log_event(kind, detail):
    with tx() as c:
        c.execute("INSERT INTO logs(kind,detail,ts) VALUES (?,?,?)", (kind, str(detail)[:500], time.time()))


def add_tx(user_id, kind, amount, meta=""):
    with tx() as c:
        c.execute(
            "INSERT INTO transactions(user_id,kind,amount,meta,ts) VALUES (?,?,?,?,?)",
            (int(user_id), kind, int(amount), str(meta)[:200], time.time()),
        )


def num(n):
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


def parse_amount(text):
    t = (text or "").strip().replace(",", "").replace(" ", "").replace("،", "")
    t = t.replace("کا", "k").replace("ک", "k").replace("م", "m").replace("ب", "b")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kmbKMB])?", t, re.I)
    if not m:
        return None
    val = float(m.group(1))
    suf = (m.group(2) or "").lower()
    mul = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(suf)
    return int(val * mul) if mul is not None else None


def to_roman(n):
    n = int(n)
    if n <= 0:
        return "0"
    vals = [(1000,"M"),(900,"CM"),(500,"D"),(400,"CD"),(100,"C"),(90,"XC"),(50,"L"),(40,"XL"),(10,"X"),(9,"IX"),(5,"V"),(4,"IV"),(1,"I")]
    out = []
    for v, s in vals:
        while n >= v:
            out.append(s)
            n -= v
    return "".join(out)


def mono(s):
    return f"<code>{s}</code>"


def fmt_time(sec):
    sec = int(max(0, sec))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}س {m}د"
    if m:
        return f"{m}د {s}ث"
    return f"{s}ث"


def btn(text, data, style=None):
    kw = {"text": str(text)[:64], "callback_data": data}
    if style in ("danger", "success", "primary"):
        kw["style"] = style
    try:
        return InlineKeyboardButton(**kw)
    except TypeError:
        kw.pop("style", None)
        return InlineKeyboardButton(**kw)


def mention(uid, name=None):
    if not name:
        with tx() as c:
            r = c.execute("SELECT name FROM users WHERE id=?", (int(uid),)).fetchone()
            name = r["name"] if r else str(uid)
    return f'<a href="tg://user?id={uid}">{name}</a>'


def ensure_user(user):
    if not user or getattr(user, "is_bot", False):
        return
    now = time.time()
    with tx() as c:
        r = c.execute("SELECT id FROM users WHERE id=?", (user.id,)).fetchone()
        if not r:
            start_w = sint("start_willow", 15000)
            c.execute(
                """INSERT INTO users(id,username,name,willow,joined_at,last_active)
                   VALUES (?,?,?,?,?,?)""",
                (user.id, user.username or "", user.full_name or str(user.id), start_w, now, now),
            )
            c.execute(
                """INSERT INTO raccoons(owner_id,species,rarity,level,power,speed,luck,capacity,value,is_main)
                   VALUES (?,?,?,?,?,?,?,?,?,1)""",
                (user.id, "خاکستری", "معمولی", 1, 10, 10, 10, 10, 1000),
            )
            c.execute("INSERT OR IGNORE INTO factories(user_id) VALUES (?)", (user.id,))
            add_tx(user.id, "start", start_w, "welcome")
            log_event("join", user.id)
        else:
            c.execute(
                "UPDATE users SET username=?, name=?, last_active=? WHERE id=?",
                (user.username or "", user.full_name or str(user.id), now, user.id),
            )


def get_user(uid):
    with tx() as c:
        return c.execute("SELECT * FROM users WHERE id=?", (int(uid),)).fetchone()


def get_main_raccoon(uid):
    with tx() as c:
        r = c.execute(
            "SELECT * FROM raccoons WHERE owner_id=? AND is_main=1 ORDER BY id LIMIT 1",
            (int(uid),),
        ).fetchone()
        if not r:
            c.execute(
                """INSERT INTO raccoons(owner_id,species,rarity,level,power,speed,luck,capacity,value,is_main)
                   VALUES (?,?,?,?,?,?,?,?,?,1)""",
                (int(uid), "خاکستری", "معمولی", 1, 10, 10, 10, 10, 1000),
            )
            r = c.execute(
                "SELECT * FROM raccoons WHERE owner_id=? AND is_main=1 LIMIT 1", (int(uid),)
            ).fetchone()
        return r


def change_willow(uid, delta, kind, meta=""):
    """Atomic balance change. Returns new balance or raises ValueError."""
    with tx() as c:
        r = c.execute("SELECT willow, status FROM users WHERE id=?", (int(uid),)).fetchone()
        if not r:
            raise ValueError("no user")
        if r["status"] != "active":
            raise ValueError("blocked")
        new = int(r["willow"]) + int(delta)
        if new < 0:
            raise ValueError("insufficient")
        c.execute("UPDATE users SET willow=? WHERE id=?", (new, int(uid)))
        if delta > 0:
            c.execute(
                "UPDATE users SET willow_collected = willow_collected + ? WHERE id=?",
                (int(delta), int(uid)),
            )
        c.execute(
            "INSERT INTO transactions(user_id,kind,amount,meta,ts) VALUES (?,?,?,?,?)",
            (int(uid), kind, int(delta), str(meta)[:200], time.time()),
        )
        return new


def willow_cooldown(uid, chat_id=None):
    cd = sint("willow_cd", 300)
    cd_min = sint("willow_cd_min", 120)
    reduce = 0
    if chat_id:
        with tx() as c:
            city = c.execute("SELECT level FROM cities WHERE chat_id=?", (int(chat_id),)).fetchone()
            if city:
                reduce += (int(city["level"]) - 1) * sint("willow_cd_per_city_lv", 15)
    u = get_user(uid)
    if u:
        reduce += int(u["street_rescues"] or 0) * sint("willow_cd_per_rescue", 5)
    return max(cd_min, cd - reduce)


def ensure_city(chat):
    if not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return None
    with tx() as c:
        r = c.execute("SELECT * FROM cities WHERE chat_id=?", (chat.id,)).fetchone()
        if not r:
            mayor = None
            try:
                pass
            except Exception:
                pass
            c.execute(
                "INSERT INTO cities(chat_id,title,level,xp,treasury,mayor_id) VALUES (?,?,1,0,0,NULL)",
                (chat.id, chat.title or str(chat.id)),
            )
            r = c.execute("SELECT * FROM cities WHERE chat_id=?", (chat.id,)).fetchone()
        else:
            c.execute("UPDATE cities SET title=? WHERE chat_id=?", (chat.title or r["title"], chat.id))
        return dict(r) if r else None


def set_st(ctx, kind, extra=None):
    ctx.user_data["st"] = {"kind": kind, "extra": extra or {}, "ts": time.time()}


def get_st(ctx):
    st = ctx.user_data.get("st")
    if not st:
        return None
    if time.time() - st.get("ts", 0) > 900:
        ctx.user_data.pop("st", None)
        return None
    return st


def clear_st(ctx):
    ctx.user_data.pop("st", None)


# ───────────────────────── UI ─────────────────────────
def main_menu_kb():
    return InlineKeyboardMarkup([
        [btn("🦝 راکون من", "m:raccoon", "primary"), btn("🌿 دریافت ویلو", "m:willow", "success")],
        [btn("🏦 بانک", "m:bank", "primary"), btn("🏭 کارخانه", "m:factory", "primary")],
        [btn("🛒 بازار", "m:shop", "primary"), btn("🏙 مارکت شهر", "m:citymarket", "primary")],
        [btn("🎣 جمع‌آوری", "m:gather", "primary"), btn("⚔️ غارت", "m:raid", "danger")],
        [btn("🎁 کد هدیه", "m:gift", "success"), btn("👥 دوستان", "m:friends", "primary")],
        [btn("🏆 رتبه‌بندی", "m:lb", "primary"), btn("🎯 مأموریت‌ها", "m:missions", "primary")],
        [btn("🏙 شهر", "m:city", "primary"), btn("👤 پروفایل", "m:profile", "primary")],
        [btn("📖 راهنما", "m:help", "primary")],
    ])


def back_main():
    return InlineKeyboardMarkup([[btn("🔙 منوی اصلی", "m:home", "primary")]])


def admin_kb():
    return InlineKeyboardMarkup([
        [btn("👥 لیست کاربران (کپی)", "a:ulist", "primary")],
        [btn("➕ واریز", "a:add", "success"), btn("➖ کسر", "a:sub", "danger")],
        [btn("📦 بازیابی دسته‌ای", "a:restore", "success")],
        [btn("🌿 تنظیم ویلو", "a:willow", "primary"), btn("⚔️ مکان غارت", "a:raids", "primary")],
        [btn("🛒 آیتم فروشگاه", "a:shop", "primary"), btn("🦝 راکون خیابانی", "a:street", "primary")],
        [btn("🎁 کد هدیه", "a:code", "success"), btn("📢 همگانی", "a:bcast", "primary")],
        [btn("📊 آمار", "a:stats", "primary"), btn("👤 ادمین‌ها", "a:admins", "primary")],
        [btn("❌ بستن", "a:close", "danger")],
    ])


def help_full():
    return """📖 <b>راهنمای کامل رِیو</b>
━━━━━━━━━━━━━━━━
🦝 <b>هویت</b>
بازی اقتصادی با راکون‌ها. ارز رسمی: <b>🌿 ویلو</b>.

🌿 <b>ویلو</b>
با دکمه «دریافت ویلو» یا نوشتن <code>ویلو</code> در گپ، ویلو می‌گیری.
کول‌داون پایه ۵ دقیقه است؛ با <b>سطح شهر</b> و <b>نجات راکون خیابانی</b> کمتر می‌شود.

🦝 <b>راکون من</b>
راکون اصلی‌ات سطح، قدرت، سرعت و شانس دارد.
با خوراک از بازار ارتقا می‌یابد.

🏦 <b>بانک</b>
واریز و برداشت. انتقال با نوشتن:
<code>انتقال 10k</code> + ریپلای روی کاربر
کارمزد از تنظیمات ادمین.

🏭 <b>کارخانه</b>
تولید زمان‌دار؛ حتی وقتی آفلاینی زمان می‌گذرد.
دوباره «کارخانه» بزن تا محصول را بگیری.

🛒 <b>بازار</b>
خوراک و تقویت‌کننده از فروشگاه سیستم.

🏙 <b>مارکت شهر</b>
فقط <b>شهردار (مالک گپ)</b> می‌تواند کالا/راکون از فروشگاه ادمین بخرد و با قیمت کمتر برای اعضای گپ بگذارد.

🎣 <b>جمع‌آوری</b>
راکون به منطقه می‌رود؛ ممکن است ویلو، آیتم یا چیزی پیدا نکند.

⚔️ <b>غارت</b>
حمله به <b>مکان‌های تعیین‌شده توسط ادمین</b> (نه کیف بازیکن‌ها).
چند مرحله‌ای است؛ شکست = بازداشت.

🏙 <b>شهر</b>
هر گپ یک شهر دارد با سطح رومی (مثل <code>IV</code>)، خزانه و دونیت.

🎁 <b>کد هدیه</b>
کدی که ادمین ساخته را وارد کن.

👥 <b>دوستان</b>
لینک دعوت اختصاصی و پاداش.

🏆 <b>رتبه‌بندی</b>
بیشترین ویلو و سطح.

دستورات متنی (بدون /):
<code>پروفایل</code> · <code>ویلو</code> · <code>شهر</code> · <code>بانک</code>
<code>بازار</code> · <code>مارکت</code> · <code>غارت</code> · <code>کارخانه</code>
<code>رتبه</code> · <code>راهنما</code> · <code>منو</code>
ادمین: <code>پنل</code> (فقط پیوی)
"""


# ───────────────────────── HANDLERS ─────────────────────────
async def on_start_text(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """شروع با کلمه استارت یا /start"""
    user = u.effective_user
    ensure_user(user)
    uw = get_user(user.id)
    text = (
        f"🦝 <b>به دنیای رِیو خوش آمدی</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"اینجا راکون‌ها برای <b>ویلو</b> می‌جنگند.\n"
        f"موجودی اولیه: 🌿 <b>{num(uw['willow'])}</b>\n\n"
        f"از منو شروع کن یا بنویس: <code>ویلو</code>"
    )
    await u.message.reply_text(text, parse_mode="HTML", reply_markup=main_menu_kb())


async def show_profile(bot, chat_id, uid, message=None):
    u = get_user(uid)
    if not u:
        return
    rac = get_main_raccoon(uid)
    with tx() as c:
        rank = c.execute(
            "SELECT COUNT(*)+1 AS r FROM users WHERE willow > ?", (u["willow"],)
        ).fetchone()["r"]
    jl = max(0, float(u["jail_until"] or 0) - time.time())
    jail = f"\n🚔 بازداشت: {fmt_time(jl)}" if jl else ""
    text = (
        f"👤 <b>پروفایل رِیو</b>\n━━━━━━━━━━━━━━━━\n"
        f"{mention(uid, u['name'])}\n"
        f"⭐ سطح {mono(to_roman(u['level']))} · XP {num(u['xp'])}\n"
        f"🌿 ویلو: <b>{num(u['willow'])}</b>\n"
        f"🏦 بانک: <b>{num(u['bank'])}</b>\n"
        f"🦝 راکون: {rac['species']} | سطح {mono(to_roman(rac['level']))}\n"
        f"⚔️ قدرت {rac['power']} · 💨 {rac['speed']} · 🍀 {rac['luck']}\n"
        f"🏆 رتبه ویلو: {mono(str(rank))}\n"
        f"🌿 جمع‌شده: {num(u['willow_collected'])}\n"
        f"✅ عملیات: {num(u['ops_ok'])} | ❌ {num(u['ops_fail'])}\n"
        f"🕊 نجات خیابانی: {num(u['street_rescues'])}\n"
        f"👥 دعوت: {num(u['invites'])}{jail}"
    )
    photo = rac["photo_file_id"]
    kb = InlineKeyboardMarkup([[btn("🔙 منوی اصلی", "m:home", "primary")]])
    if not photo:
        try:
            ph = await bot.get_user_profile_photos(int(uid), limit=1)
            if ph.total_count:
                photo = ph.photos[0][-1].file_id
        except Exception:
            pass
    if photo:
        try:
            await bot.send_photo(chat_id, photo=photo, caption=text, parse_mode="HTML", reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)


async def do_willow(u: Update, c: ContextTypes.DEFAULT_TYPE, from_cb=False):
    user = u.effective_user
    ensure_user(user)
    chat = u.effective_chat
    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        ensure_city(chat)
    uu = get_user(user.id)
    if uu["status"] != "active":
        msg = "حسابت مسدود است."
        if from_cb:
            await u.callback_query.answer(msg, show_alert=True)
        else:
            await u.message.reply_text(msg)
        return
    jl = max(0, float(uu["jail_until"] or 0) - time.time())
    if jl:
        msg = f"🚔 در بازداشتی: {fmt_time(jl)}"
        if from_cb:
            await u.callback_query.edit_message_text(msg, reply_markup=back_main())
        else:
            await u.message.reply_text(msg)
        return
    cd = willow_cooldown(user.id, chat.id if chat.type != ChatType.PRIVATE else None)
    left = float(uu["last_willow"] or 0) + cd - time.time()
    if left > 0:
        text = f"⏳ راکونت هنوز آماده‌ی ویلو نیست.\nزمان باقی: <b>{fmt_time(left)}</b>"
        if from_cb:
            await u.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=back_main())
        else:
            await u.message.reply_text(text, parse_mode="HTML")
        return
    amin, amax = sint("willow_min", 3000), sint("willow_max", 7000)
    amount = random.randint(min(amin, amax), max(amin, amax))
    # city bonus
    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        with tx() as conn:
            city = conn.execute("SELECT level, willow_users FROM cities WHERE chat_id=?", (chat.id,)).fetchone()
            if city:
                amount += (int(city["level"]) - 1) * 200
                try:
                    users = json.loads(city["willow_users"] or "[]")
                except Exception:
                    users = []
                if user.id not in users:
                    users.append(user.id)
                conn.execute(
                    "UPDATE cities SET willow_users=?, xp = xp + ? WHERE chat_id=?",
                    (json.dumps(users), sint("city_xp_per_willow", 1), chat.id),
                )
                # level up city
                city2 = conn.execute("SELECT level, xp FROM cities WHERE chat_id=?", (chat.id,)).fetchone()
                need = 50 * int(city2["level"])
                if int(city2["xp"]) >= need:
                    conn.execute(
                        "UPDATE cities SET level = level + 1, xp = xp - ? WHERE chat_id=?",
                        (need, chat.id),
                    )
    new_bal = change_willow(user.id, amount, "willow", "collect")
    with tx() as conn:
        conn.execute("UPDATE users SET last_willow=? WHERE id=?", (time.time(), user.id))
    text = (
        f"🦝 راکونت دوباره دست به کار شد!\n"
        f"🌿 <b>+{num(amount)}</b> ویلو\n"
        f"موجودی: <b>{num(new_bal)}</b>\n"
        f"⏳ بعدی: <b>{fmt_time(cd)}</b>"
    )
    if from_cb:
        await u.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=back_main())
    else:
        await u.message.reply_text(text, parse_mode="HTML")


async def on_callback(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    data = q.data or ""
    user = u.effective_user
    ensure_user(user)
    chat = u.effective_chat

    if data == "a:close":
        await q.answer()
        try:
            await q.message.delete()
        except Exception:
            pass
        return

    # ---- main menu ----
    if data == "m:home":
        await q.answer()
        await q.edit_message_text("🦝 <b>منوی رِیو</b>\nیک بخش را انتخاب کن:", parse_mode="HTML", reply_markup=main_menu_kb())
        return
    if data == "m:help":
        await q.answer()
        await q.edit_message_text(help_full(), parse_mode="HTML", reply_markup=back_main())
        return
    if data == "m:profile":
        await q.answer()
        try:
            await q.message.delete()
        except Exception:
            pass
        await show_profile(c.bot, chat.id, user.id)
        return
    if data == "m:willow":
        await q.answer()
        await do_willow(u, c, from_cb=True)
        return
    if data == "m:raccoon":
        await q.answer()
        rac = get_main_raccoon(user.id)
        with tx() as conn:
            allr = conn.execute("SELECT * FROM raccoons WHERE owner_id=?", (user.id,)).fetchall()
        lines = [
            f"🦝 <b>راکون اصلی</b>",
            f"{rac['species']} · {rac['rarity']}",
            f"سطح {mono(to_roman(rac['level']))} · ارزش {num(rac['value'])}",
            f"⚔️{rac['power']} 💨{rac['speed']} 🍀{rac['luck']} 📦{rac['capacity']}",
            f"\n📋 مجموعه: <b>{len(allr)}</b> راکون",
        ]
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=back_main())
        return
    if data == "m:bank":
        await q.answer()
        uu = get_user(user.id)
        kb = InlineKeyboardMarkup([
            [btn("⬆️ واریز", f"bank:in:{user.id}", "success"), btn("⬇️ برداشت", f"bank:out:{user.id}", "danger")],
            [btn("📜 تاریخچه", f"bank:log:{user.id}", "primary")],
            [btn("🔙", "m:home", "primary")],
        ])
        await q.edit_message_text(
            f"🏦 <b>بانک رِیو</b>\n🌿 کیف: <b>{num(uu['willow'])}</b>\n🏦 بانک: <b>{num(uu['bank'])}</b>\nسقف: {num(sint('bank_cap'))}",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return
    if data.startswith("bank:in:") or data.startswith("bank:out:"):
        await q.answer()
        owner = int(data.split(":")[2])
        if user.id != owner:
            await q.answer("مال تو نیست", show_alert=True)
            return
        set_st(c, "bank_in" if data.startswith("bank:in") else "bank_out")
        await q.edit_message_text("مبلغ را به صورت عددی بفرست (مثال 10k):")
        return
    if data.startswith("bank:log:"):
        await q.answer()
        if user.id != int(data.split(":")[2]):
            return
        with tx() as conn:
            rows = conn.execute(
                "SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 15", (user.id,)
            ).fetchall()
        lines = ["📜 <b>آخرین تراکنش‌ها</b>\n"]
        for r in rows:
            lines.append(f"• {r['kind']}: {num(r['amount'])}")
        await q.edit_message_text("\n".join(lines) or "خالی", parse_mode="HTML", reply_markup=back_main())
        return

    if data == "m:factory":
        await q.answer()
        now = time.time()
        with tx() as conn:
            f = conn.execute("SELECT * FROM factories WHERE user_id=?", (user.id,)).fetchone()
            if not f:
                conn.execute("INSERT INTO factories(user_id) VALUES (?)", (user.id,))
                f = conn.execute("SELECT * FROM factories WHERE user_id=?", (user.id,)).fetchone()
            f = dict(f)
        if float(f["busy_until"] or 0) > now:
            await q.edit_message_text(
                f"🏭 در حال تولید...\n⏱ {fmt_time(float(f['busy_until']) - now)}",
                reply_markup=back_main(),
            )
            return
        if int(f.get("ready") or 0):
            reward = int(f.get("reward") or 0)
            change_willow(user.id, reward, "factory", f"lv{f['level']}")
            with tx() as conn:
                conn.execute("UPDATE factories SET ready=0, reward=0 WHERE user_id=?", (user.id,))
            await q.edit_message_text(f"🏭 تحویل شد: 🌿 <b>+{num(reward)}</b>", parse_mode="HTML", reply_markup=back_main())
            return
        tsec = sint("factory_base_time", 1800)
        reward = sint("factory_base_reward", 25000) * int(f["level"])
        with tx() as conn:
            conn.execute(
                "UPDATE factories SET busy_until=?, ready=1, reward=? WHERE user_id=?",
                (now + tsec, reward, user.id),
            )
        kb = InlineKeyboardMarkup([
            [btn("⬆️ ارتقا کارخانه", f"fac:up:{user.id}", "success")],
            [btn("🔙", "m:home", "primary")],
        ])
        await q.edit_message_text(
            f"🏭 تولید شروع شد\nسطح کارخانه: {mono(to_roman(f['level']))}\nپاداش: {num(reward)}\n⏱ {fmt_time(tsec)}",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return
    if data.startswith("fac:up:"):
        await q.answer()
        if user.id != int(data.split(":")[2]):
            return
        cost = 50000
        try:
            change_willow(user.id, -cost, "factory_up", "")
        except ValueError:
            await q.answer("ویلو کافی نیست", show_alert=True)
            return
        with tx() as conn:
            conn.execute("UPDATE factories SET level = level + 1 WHERE user_id=?", (user.id,))
            lv = conn.execute("SELECT level FROM factories WHERE user_id=?", (user.id,)).fetchone()["level"]
        await q.edit_message_text(f"🏭 ارتقا یافت → سطح {mono(to_roman(lv))}", parse_mode="HTML", reply_markup=back_main())
        return

    if data == "m:shop":
        await q.answer()
        with tx() as conn:
            items = conn.execute("SELECT * FROM shop_items WHERE active=1").fetchall()
        lines = ["🛒 <b>بازار رِیو</b>\n━━━━━━━━━━━━━━━━\n"]
        rows = []
        for it in items:
            lines.append(f"• <b>{it['name']}</b> — 🌿 {num(it['price'])}")
            rows.append([btn(f"{it['name'][:18]} | {num(it['price'])}", f"buy:{it['id']}:{user.id}", "success")])
        rows.append([btn("🔙", "m:home", "primary")])
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        return
    if data.startswith("buy:"):
        await q.answer()
        _, iid, owner = data.split(":")
        if user.id != int(owner):
            await q.answer("مال تو نیست", show_alert=True)
            return
        with tx() as conn:
            it = conn.execute("SELECT * FROM shop_items WHERE id=? AND active=1", (iid,)).fetchone()
        if not it:
            await q.answer("ناموجود", show_alert=True)
            return
        try:
            change_willow(user.id, -int(it["price"]), "shop", iid)
        except ValueError:
            await q.answer("ویلو کافی نیست", show_alert=True)
            return
        # apply effect
        eff = it["effect"] or ""
        with tx() as conn:
            inv = conn.execute("SELECT inv FROM users WHERE id=?", (user.id,)).fetchone()["inv"]
            try:
                invd = json.loads(inv or "{}")
            except Exception:
                invd = {}
            if eff.startswith("xp:"):
                xp = int(eff.split(":")[1])
                conn.execute("UPDATE users SET xp = xp + ? WHERE id=?", (xp, user.id))
                rac = get_main_raccoon(user.id)
                conn.execute("UPDATE raccoons SET xp = xp + ?, value = value + ? WHERE id=?", (xp, xp * 10, rac["id"]))
            elif eff.startswith("cd_half:"):
                invd["cd_half"] = int(invd.get("cd_half") or 0) + 1
            elif eff.startswith("jail_pass:"):
                invd["jail_pass"] = int(invd.get("jail_pass") or 0) + 1
            conn.execute("UPDATE users SET inv=? WHERE id=?", (json.dumps(invd, ensure_ascii=False), user.id))
        await q.edit_message_text(f"✅ خرید: <b>{it['name']}</b>", parse_mode="HTML", reply_markup=back_main())
        return

    if data == "m:city":
        await q.answer()
        if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            await q.edit_message_text("شهر فقط در گپ معنا دارد.\nربات را به گپ اضافه کن.", reply_markup=back_main())
            return
        city = ensure_city(chat)
        with tx() as conn:
            city = dict(conn.execute("SELECT * FROM cities WHERE chat_id=?", (chat.id,)).fetchone())
        try:
            wusers = json.loads(city.get("willow_users") or "[]")
        except Exception:
            wusers = []
        # detect mayor = chat creator if possible
        mayor = city.get("mayor_id")
        kb = InlineKeyboardMarkup([
            [btn("🕊 دونیت به خزانه", f"don:{chat.id}:{user.id}", "success")],
            [btn("🏙 مارکت شهر", "m:citymarket", "primary")],
            [btn("🔙", "m:home", "primary")],
        ])
        await q.edit_message_text(
            f"🏙 <b>{city['title']}</b>\n━━━━━━━━━━━━━━━━\n"
            f"👑 سطح شهر  {mono(to_roman(city['level']))}\n"
            f"✨ XP  {num(city['xp'])}\n"
            f"💰 خزانه  {num(city['treasury'])}\n"
            f"👥 ویلو‌زن‌ها  {num(len(wusers))}\n"
            f"🌿 پاداش ویلو با سطح شهر بیشتر می‌شود",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return
    if data.startswith("don:"):
        await q.answer()
        parts = data.split(":")
        if user.id != int(parts[2]):
            return
        set_st(c, "donate_city", {"chat_id": int(parts[1])})
        await q.edit_message_text("مبلغ دونیت به خزانه شهر را بفرست:")
        return

    if data == "m:raid":
        await q.answer()
        uu = get_user(user.id)
        jl = max(0, float(uu["jail_until"] or 0) - time.time())
        if jl:
            await q.edit_message_text(f"🚔 بازداشت: {fmt_time(jl)}", reply_markup=back_main())
            return
        with tx() as conn:
            places = conn.execute("SELECT * FROM raid_places WHERE active=1").fetchall()
        rows = []
        lines = ["⚔️ <b>غارت — مکان‌های رسمی</b>\nپول از مکان است، نه از بازیکن‌ها\n"]
        for p in places:
            lines.append(f"• {p['name']} | سطح {p['min_level']}+ | صندوق {num(p['pool'])}")
            rows.append([btn(f"{p['name']} · {num(p['pool'])}", f"raid:{p['id']}:{user.id}", "danger")])
        rows.append([btn("🔙", "m:home", "primary")])
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        return
    if data.startswith("raid:") and data.count(":") == 2:
        await q.answer()
        _, pid, owner = data.split(":")
        if user.id != int(owner):
            await q.answer("مال تو نیست", show_alert=True)
            return
        with tx() as conn:
            p = conn.execute("SELECT * FROM raid_places WHERE id=? AND active=1", (pid,)).fetchone()
        if not p:
            return
        uu = get_user(user.id)
        if int(uu["level"]) < int(p["min_level"]):
            await q.answer("سطح‌ات کم است", show_alert=True)
            return
        cd_left = float(uu["last_raid"] or 0) + int(p["cooldown"]) - time.time()
        if cd_left > 0:
            await q.answer(f"کول‌داون {fmt_time(cd_left)}", show_alert=True)
            return
        # multi stage animation
        await q.edit_message_text("🕶️ ورود به منطقه...")
        await asyncio.sleep(0.8)
        stages = int(p["stages"])
        for i in range(1, stages + 1):
            try:
                await q.edit_message_text(f"⚔️ مرحله {mono(to_roman(i))} / {mono(to_roman(stages))}...")
            except Exception:
                pass
            await asyncio.sleep(0.9)
        fail = random.random() < float(p["fail_chance"])
        # jail pass?
        with tx() as conn:
            inv = json.loads(conn.execute("SELECT inv FROM users WHERE id=?", (user.id,)).fetchone()["inv"] or "{}")
        if fail:
            if int(inv.get("jail_pass") or 0) > 0:
                inv["jail_pass"] = int(inv["jail_pass"]) - 1
                with tx() as conn:
                    conn.execute("UPDATE users SET inv=?, ops_fail = ops_fail + 1, last_raid=? WHERE id=?",
                                 (json.dumps(inv), time.time(), user.id))
                await q.edit_message_text("🚓 شکست — اما کارت آزادی نجاتت داد!", reply_markup=back_main())
                return
            sec = int(p["jail_sec"])
            with tx() as conn:
                conn.execute(
                    "UPDATE users SET jail_until=?, ops_fail = ops_fail + 1, last_raid=? WHERE id=?",
                    (time.time() + sec, time.time(), user.id),
                )
                # pool slightly grows on fail (tension)
                conn.execute("UPDATE raid_places SET pool = pool + ? WHERE id=?", (int(p["pool"] * 0.02), pid))
            await q.edit_message_text(
                f"🚓 <b>دستگیر شدی</b>\nمکان: {p['name']}\nبازداشت: {fmt_time(sec)}",
                parse_mode="HTML",
                reply_markup=back_main(),
            )
            return
        loot = int(int(p["pool"]) * random.uniform(0.08, 0.18))
        loot = max(1000, loot)
        with tx() as conn:
            pool = conn.execute("SELECT pool FROM raid_places WHERE id=?", (pid,)).fetchone()["pool"]
            loot = min(loot, int(pool))
            conn.execute("UPDATE raid_places SET pool = pool - ? WHERE id=?", (loot, pid))
            conn.execute(
                "UPDATE users SET ops_ok = ops_ok + 1, last_raid=? WHERE id=?",
                (time.time(), user.id),
            )
        change_willow(user.id, loot, "raid", pid)
        await q.edit_message_text(
            f"✅ <b>غارت موفق</b>\n📍 {p['name']}\n🌿 <b>+{num(loot)}</b>\nصندوق باقی: {num(int(p['pool']) - loot)}",
            parse_mode="HTML",
            reply_markup=back_main(),
        )
        return

    if data == "m:gather":
        await q.answer()
        uu = get_user(user.id)
        cd = sint("gather_cd", 600)
        left = float(uu["last_gather"] or 0) + cd - time.time()
        if left > 0:
            await q.edit_message_text(f"🎣 هنوز زود است: {fmt_time(left)}", reply_markup=back_main())
            return
        rac = get_main_raccoon(user.id)
        chance = 0.55 + int(rac["luck"]) / 200
        with tx() as conn:
            conn.execute("UPDATE users SET last_gather=? WHERE id=?", (time.time(), user.id))
        if random.random() > chance:
            await q.edit_message_text("🌫️ چیزی پیدا نشد... راکونت خسته برگشت.", reply_markup=back_main())
            return
        gain = random.randint(2000, 9000) + int(rac["level"]) * 200
        change_willow(user.id, gain, "gather", "")
        # rare street rescue
        extra = ""
        if random.random() < 0.06:
            with tx() as conn:
                conn.execute("UPDATE users SET street_rescues = street_rescues + 1 WHERE id=?", (user.id,))
            extra = "\n🕊 یک راکون خیابانی نجات دادی! کول‌داون ویلو بهتر شد."
        await q.edit_message_text(f"🎣 جمع‌آوری موفق\n🌿 +{num(gain)}{extra}", parse_mode="HTML", reply_markup=back_main())
        return

    if data == "m:gift":
        await q.answer()
        set_st(c, "gift")
        await q.edit_message_text("🎁 کد هدیه را بفرست:")
        return
    if data == "m:friends":
        await q.answer()
        me = await c.bot.get_me()
        link = f"https://t.me/{me.username}?start=ref{user.id}"
        uu = get_user(user.id)
        await q.edit_message_text(
            f"👥 <b>دعوت دوستان</b>\nلینک تو:\n{link}\n\nدعوت‌های موفق: <b>{num(uu['invites'])}</b>",
            parse_mode="HTML",
            reply_markup=back_main(),
        )
        return
    if data == "m:lb":
        await q.answer()
        with tx() as conn:
            rows = conn.execute("SELECT id,name,willow,level FROM users WHERE status='active' ORDER BY willow DESC LIMIT 10").fetchall()
        lines = ["🏆 <b>رتبه‌بندی ویلو</b>\n━━━━━━━━━━━━━━━━\n"]
        for i, r in enumerate(rows, 1):
            lines.append(f"{mono(to_roman(i))} {mention(r['id'], r['name'])}")
            lines.append(f"    🌿 {num(r['willow'])} · سطح {to_roman(r['level'])}\n")
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=back_main())
        return
    if data == "m:missions":
        await q.answer()
        uu = get_user(user.id)
        lines = [
            "🎯 <b>مأموریت‌ها</b>\n",
            f"• روزانه: یک‌بار ویلو بگیر — {'✅' if float(uu['last_willow'] or 0) > time.time()-86400 else '⬜'}",
            f"• جمع‌آوری: یک‌بار فعالیت — {'✅' if float(uu['last_gather'] or 0) > time.time()-86400 else '⬜'}",
            f"• غارت موفق کل: {num(uu['ops_ok'])}",
            "\nپاداش مأموریت‌های پیشرفته از پنل ادمین قابل گسترش است.",
        ]
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=back_main())
        return
    if data == "m:citymarket":
        await q.answer()
        if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            await q.edit_message_text("مارکت شهر فقط در گپ.", reply_markup=back_main())
            return
        ensure_city(chat)
        with tx() as conn:
            city = dict(conn.execute("SELECT * FROM cities WHERE chat_id=?", (chat.id,)).fetchone())
            streets = conn.execute("SELECT * FROM street_raccoons WHERE active=1").fetchall()
        try:
            market = json.loads(city.get("market") or "[]")
        except Exception:
            market = []
        lines = ["🏙 <b>مارکت شهر</b>\nقیمت‌ها توسط شهردار تنظیم می‌شود\n"]
        rows = []
        if not market:
            lines.append("هنوز کالایی نیست.\nشهردار از فروشگاه ادمین شارژ کند.")
        for i, it in enumerate(market[:20]):
            lines.append(f"• {it.get('name')} — 🌿 {num(it.get('price', 0))}")
            rows.append([btn(f"خرید {it.get('name')[:15]}", f"cmbuy:{chat.id}:{i}:{user.id}", "success")])
        # mayor can stock
        rows.append([btn("➕ شارژ مارکت (شهردار)", f"cmstock:{chat.id}:{user.id}", "primary")])
        rows.append([btn("🔙", "m:home", "primary")])
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("cmstock:"):
        await q.answer()
        _, cid, uid = data.split(":")
        if user.id != int(uid):
            return
        # only chat creator / mayor
        try:
            member = await c.bot.get_chat_member(int(cid), user.id)
            status = member.status
        except Exception:
            status = ""
        if status not in ("creator", "administrator") and not is_admin(user.id):
            await q.answer("فقط شهردار/ادمین گپ", show_alert=True)
            return
        with tx() as conn:
            streets = conn.execute("SELECT * FROM street_raccoons WHERE active=1").fetchall()
        rows = []
        for s in streets:
            rows.append([btn(f"{s['name']} ({s['rarity']})", f"cmadd:{cid}:{s['id']}:{user.id}", "success")])
        rows.append([btn("🔙", "m:citymarket", "primary")])
        await q.edit_message_text("کدام راکون خیابانی را به مارکت شهر اضافه کنی؟\n(از موجودی ادمین)", reply_markup=InlineKeyboardMarkup(rows))
        return
    if data.startswith("cmadd:"):
        await q.answer()
        _, cid, sid, uid = data.split(":")
        if user.id != int(uid):
            return
        with tx() as conn:
            s = conn.execute("SELECT * FROM street_raccoons WHERE id=?", (int(sid),)).fetchone()
            city = conn.execute("SELECT market FROM cities WHERE chat_id=?", (int(cid),)).fetchone()
            market = json.loads(city["market"] or "[]")
            # member price = 85% of base
            price = int(int(s["price"]) * 0.85)
            market.append({"name": s["name"], "rarity": s["rarity"], "price": price, "power": s["power"], "photo": s["photo_file_id"]})
            conn.execute("UPDATE cities SET market=? WHERE chat_id=?", (json.dumps(market, ensure_ascii=False), int(cid)))
        await q.edit_message_text(f"به مارکت شهر اضافه شد: {s['name']} با قیمت {num(price)}", reply_markup=back_main())
        return
    if data.startswith("cmbuy:"):
        await q.answer()
        _, cid, idx, uid = data.split(":")
        if user.id != int(uid):
            return
        idx = int(idx)
        with tx() as conn:
            city = conn.execute("SELECT market FROM cities WHERE chat_id=?", (int(cid),)).fetchone()
            market = json.loads(city["market"] or "[]")
            if idx >= len(market):
                await q.answer("ناموجود", show_alert=True)
                return
            it = market[idx]
            price = int(it["price"])
        try:
            change_willow(user.id, -price, "city_market", it["name"])
        except ValueError:
            await q.answer("ویلو کم", show_alert=True)
            return
        with tx() as conn:
            conn.execute(
                """INSERT INTO raccoons(owner_id,species,rarity,level,power,speed,luck,capacity,value,is_main,photo_file_id)
                   VALUES (?,?,?,1,?,?,?,?,?,0,?)""",
                (user.id, it["name"], it.get("rarity", "معمولی"), int(it.get("power") or 10), 10, 10, 10, price, it.get("photo")),
            )
            conn.execute("UPDATE users SET raccoon_count = raccoon_count + 1, street_rescues = street_rescues + 1 WHERE id=?", (user.id,))
            market.pop(idx)
            conn.execute("UPDATE cities SET market=? WHERE chat_id=?", (json.dumps(market, ensure_ascii=False), int(cid)))
        await q.edit_message_text(f"🦝 <b>{it['name']}</b> به مجموعه اضافه شد!", parse_mode="HTML", reply_markup=back_main())
        return

    # ---- ADMIN ----
    if data.startswith("a:") and not is_admin(user.id):
        await q.answer("دسترسی نداری", show_alert=True)
        return
    if data == "a:home" or data == "پنل":
        await q.answer()
        await q.edit_message_text("🎛 <b>پنل مدیریت رِیو</b>", parse_mode="HTML", reply_markup=admin_kb())
        return
    if data == "a:ulist":
        await q.answer()
        with tx() as conn:
            rows = conn.execute(
                "SELECT u.id, u.willow, u.level, u.bank, r.level AS rl FROM users u "
                "LEFT JOIN raccoons r ON r.owner_id=u.id AND r.is_main=1 ORDER BY u.willow DESC LIMIT 200"
            ).fetchall()
        lines = ["USERS mono list (copy)\n"]
        for r in rows:
            lines.append(f"{r['id']}|{r['willow']}|{r['level']}|{r['bank']}|{r['rl'] or 1}")
        text = "\n".join(lines)
        # split if long
        if len(text) > 3500:
            text = "\n".join(lines[:80]) + "\n..."
        await q.edit_message_text(mono(text), parse_mode="HTML", reply_markup=admin_kb())
        return
    if data == "a:add":
        set_st(c, "a_add")
        await q.edit_message_text("فرمت:\n<code>آیدی مبلغ</code>\nمثال: <code>123456 50k</code>", parse_mode="HTML")
        return
    if data == "a:sub":
        set_st(c, "a_sub")
        await q.edit_message_text("فرمت کسر:\n<code>آیدی مبلغ</code>", parse_mode="HTML")
        return
    if data == "a:restore":
        set_st(c, "a_restore")
        await q.edit_message_text(
            "لیست mono را بفرست (هر خط):\n<code>id|willow|level|bank|raccoon_level</code>\n"
            "همان خروجی «لیست کاربران»",
            parse_mode="HTML",
        )
        return
    if data == "a:willow":
        set_st(c, "a_willow")
        await q.edit_message_text(
            f"ویلو min={sget('willow_min')} max={sget('willow_max')} cd={sget('willow_cd')}\n"
            "بفرست:\n<code>min 3000</code>\n<code>max 7000</code>\n<code>cd 300</code>",
            parse_mode="HTML",
        )
        return
    if data == "a:stats":
        with tx() as conn:
            uc = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
            tw = conn.execute("SELECT SUM(willow) s FROM users").fetchone()["s"] or 0
            tb = conn.execute("SELECT SUM(bank) s FROM users").fetchone()["s"] or 0
            rc = conn.execute("SELECT COUNT(*) c FROM raccoons").fetchone()["c"]
        await q.edit_message_text(
            f"📊 کاربران: {num(uc)}\n🌿 ویلو گردش: {num(tw)}\n🏦 بانک‌ها: {num(tb)}\n🦝 راکون‌ها: {num(rc)}",
            reply_markup=admin_kb(),
        )
        return
    if data == "a:code":
        set_st(c, "a_code")
        await q.edit_message_text("فرمت:\n<code>کد مبلغ تعداد</code>\nمثال: <code>RIVO 20k 100</code>", parse_mode="HTML")
        return
    if data == "a:bcast":
        set_st(c, "a_bcast")
        await q.edit_message_text("متن همگانی را بفرست:")
        return
    if data == "a:raids":
        with tx() as conn:
            places = conn.execute("SELECT * FROM raid_places").fetchall()
        lines = ["⚔️ مکان‌های غارت\n"]
        for p in places:
            lines.append(f"{p['id']}: {p['name']} pool={num(p['pool'])} fail={p['fail_chance']}")
        lines.append("\nشارژ صندوق:\n<code>pool alley 500000</code>")
        set_st(c, "a_raids")
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=admin_kb())
        return
    if data == "a:street":
        set_st(c, "a_street_photo")
        await q.edit_message_text("برای راکون خیابانی عکس بفرست، بعد نام|نادر بودن|قیمت|قدرت")
        return
    if data == "a:admins":
        set_st(c, "a_admins")
        with tx() as conn:
            ads = conn.execute("SELECT user_id FROM admins").fetchall()
        await q.edit_message_text(
            "ادمین‌ها:\n" + "\n".join(str(a["user_id"]) for a in ads) + "\n\n<code>ادمین + آیدی</code>\n<code>ادمین - آیدی</code>",
            parse_mode="HTML",
        )
        return
    if data == "a:shop":
        await q.edit_message_text("فروشگاه از دیتابیس shop_items مدیریت می‌شود.\nقیمت: <code>قیمت food_s 6000</code>", parse_mode="HTML", reply_markup=admin_kb())
        set_st(c, "a_shop")
        return

    await q.answer()


async def on_text(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.message.text:
        return
    user = u.effective_user
    chat = u.effective_chat
    text = (u.message.text or "").strip()
    log.info("msg from %s chat=%s text=%r", user.id if user else None, chat.id if chat else None, text[:80])
    ensure_user(user)
    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        ensure_city(chat)

    # referral deep link
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        if len(parts) > 1 and parts[1].startswith("ref"):
            try:
                ref = int(parts[1][3:])
                if ref != user.id:
                    uu = get_user(user.id)
                    if not uu["referred_by"]:
                        with tx() as conn:
                            conn.execute("UPDATE users SET referred_by=? WHERE id=? AND (referred_by IS NULL OR referred_by=0)", (ref, user.id))
                            conn.execute("UPDATE users SET invites = invites + 1 WHERE id=?", (ref,))
                        try:
                            change_willow(user.id, 5000, "ref_bonus", str(ref))
                            change_willow(ref, 8000, "ref_reward", str(user.id))
                        except Exception:
                            pass
            except Exception:
                pass
        await on_start_text(u, c)
        return

    st = get_st(c)

    # admin states
    if st and is_admin(user.id) and chat.type == ChatType.PRIVATE:
        kind = st["kind"]
        if kind in ("a_add", "a_sub"):
            parts = text.split()
            if len(parts) < 2:
                await u.message.reply_text("آیدی مبلغ")
                return
            try:
                tid = int(parts[0])
            except ValueError:
                await u.message.reply_text("آیدی عددی")
                return
            amt = parse_amount(parts[1])
            if not amt:
                await u.message.reply_text("مبلغ نامعتبر")
                return
            ensure_user(type("U", (), {"id": tid, "username": "", "full_name": str(tid), "is_bot": False})())
            try:
                if kind == "a_add":
                    change_willow(tid, amt, "admin_add", str(user.id))
                else:
                    change_willow(tid, -amt, "admin_sub", str(user.id))
            except ValueError as e:
                await u.message.reply_text(str(e))
                return
            clear_st(c)
            await u.message.reply_text("انجام شد.", reply_markup=admin_kb())
            return
        if kind == "a_restore":
            ok = 0
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("USERS"):
                    continue
                parts = line.split("|")
                if len(parts) < 2:
                    continue
                try:
                    tid = int(parts[0])
                    willow = int(parts[1])
                    level = int(parts[2]) if len(parts) > 2 else 1
                    bank = int(parts[3]) if len(parts) > 3 else 0
                    rl = int(parts[4]) if len(parts) > 4 else 1
                except ValueError:
                    continue
                ensure_user(type("U", (), {"id": tid, "username": "", "full_name": str(tid), "is_bot": False})())
                with tx() as conn:
                    conn.execute("UPDATE users SET willow=?, level=?, bank=? WHERE id=?", (willow, level, bank, tid))
                    conn.execute("UPDATE raccoons SET level=? WHERE owner_id=? AND is_main=1", (rl, tid))
                ok += 1
            clear_st(c)
            await u.message.reply_text(f"بازیابی {ok} کاربر", reply_markup=admin_kb())
            return
        if kind == "a_willow":
            m = re.match(r"^(min|max|cd)\s+(\d+)$", text, re.I)
            if m:
                sset("willow_" + m.group(1).lower() if m.group(1).lower() != "cd" else "willow_cd", m.group(2))
                if m.group(1).lower() == "cd":
                    sset("willow_cd", m.group(2))
                clear_st(c)
                await u.message.reply_text("ذخیره شد", reply_markup=admin_kb())
            return
        if kind == "a_code":
            parts = text.split()
            if len(parts) >= 3:
                code, amt, uses = parts[0].upper(), parse_amount(parts[1]), parse_amount(parts[2])
                if amt and uses:
                    with tx() as conn:
                        conn.execute(
                            "INSERT OR REPLACE INTO gift_codes(code,amount,uses_left,expires_at,active) VALUES (?,?,?,?,1)",
                            (code, amt, int(uses), time.time() + 30 * 86400),
                        )
                    clear_st(c)
                    await u.message.reply_text(f"کد {code} ثبت شد", reply_markup=admin_kb())
            return
        if kind == "a_bcast":
            with tx() as conn:
                ids = [r["id"] for r in conn.execute("SELECT id FROM users").fetchall()]
            ok = fail = 0
            for tid in ids:
                try:
                    await c.bot.send_message(int(tid), text)
                    ok += 1
                except Exception:
                    fail += 1
            clear_st(c)
            await u.message.reply_text(f"✅{ok} ❌{fail}", reply_markup=admin_kb())
            return
        if kind == "a_raids":
            m = re.match(r"pool\s+(\w+)\s+(\d+)", text, re.I)
            if m:
                with tx() as conn:
                    conn.execute("UPDATE raid_places SET pool=? WHERE id=?", (int(m.group(2)), m.group(1)))
                clear_st(c)
                await u.message.reply_text("صندوق بروز شد", reply_markup=admin_kb())
            return
        if kind == "a_admins":
            m = re.match(r"ادمین\s*([+-])\s*(\d+)", text)
            if m:
                op, aid = m.group(1), int(m.group(2))
                with tx() as conn:
                    if op == "+":
                        conn.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (aid,))
                    elif is_main(user.id) and aid != ADMIN_ID:
                        conn.execute("DELETE FROM admins WHERE user_id=?", (aid,))
                clear_st(c)
                await u.message.reply_text("OK", reply_markup=admin_kb())
            return
        if kind == "a_shop":
            m = re.match(r"قیمت\s+(\w+)\s+(\d+)", text)
            if m:
                with tx() as conn:
                    conn.execute("UPDATE shop_items SET price=? WHERE id=?", (int(m.group(2)), m.group(1)))
                clear_st(c)
                await u.message.reply_text("OK", reply_markup=admin_kb())
            return
        if kind == "gift":
            code = text.strip().upper()
            with tx() as conn:
                g = conn.execute("SELECT * FROM gift_codes WHERE code=? AND active=1", (code,)).fetchone()
                if not g or int(g["uses_left"]) < 1:
                    await u.message.reply_text("کد نامعتبر")
                    clear_st(c)
                    return
                used = conn.execute("SELECT 1 FROM gift_uses WHERE code=? AND user_id=?", (code, user.id)).fetchone()
                if used:
                    await u.message.reply_text("قبلاً استفاده کردی")
                    clear_st(c)
                    return
                conn.execute("UPDATE gift_codes SET uses_left = uses_left - 1 WHERE code=?", (code,))
                conn.execute("INSERT INTO gift_uses(code,user_id,ts) VALUES (?,?,?)", (code, user.id, time.time()))
            change_willow(user.id, int(g["amount"]), "gift", code)
            clear_st(c)
            await u.message.reply_text(f"🎁 +{num(g['amount'])} ویلو")
            return
        if kind == "donate_city":
            amt = parse_amount(text)
            if not amt:
                await u.message.reply_text("مبلغ نامعتبر")
                return
            cid = st["extra"]["chat_id"]
            try:
                change_willow(user.id, -amt, "donate_city", str(cid))
            except ValueError:
                await u.message.reply_text("ویلو کم")
                return
            with tx() as conn:
                conn.execute("UPDATE cities SET treasury = treasury + ? WHERE chat_id=?", (amt, cid))
            clear_st(c)
            await u.message.reply_text(f"🕊 دونیت {num(amt)} به خزانه شهر")
            return
        if kind in ("bank_in", "bank_out"):
            amt = parse_amount(text)
            if not amt:
                await u.message.reply_text("مبلغ نامعتبر")
                return
            uu = get_user(user.id)
            if kind == "bank_in":
                if int(uu["willow"]) < amt:
                    await u.message.reply_text("موجودی کم")
                    return
                cap = sint("bank_cap")
                if cap and int(uu["bank"]) + amt > cap:
                    await u.message.reply_text("سقف بانک")
                    return
                with tx() as conn:
                    conn.execute("UPDATE users SET willow = willow - ?, bank = bank + ? WHERE id=?", (amt, amt, user.id))
                add_tx(user.id, "bank_in", -amt, "")
            else:
                if int(uu["bank"]) < amt:
                    await u.message.reply_text("بانک کم")
                    return
                with tx() as conn:
                    conn.execute("UPDATE users SET willow = willow + ?, bank = bank - ? WHERE id=?", (amt, amt, user.id))
                add_tx(user.id, "bank_out", amt, "")
            clear_st(c)
            await u.message.reply_text("انجام شد.")
            return

    # plain commands (no slash required)
    cmd = re.sub(r"^/", "", text).strip()
    low = cmd

    if re.fullmatch(r"(منو|menu)", low, re.I):
        await u.message.reply_text("🦝 منوی رِیو:", reply_markup=main_menu_kb())
        return
    if re.fullmatch(r"(راهنما|help)", low, re.I):
        await u.message.reply_text(help_full(), parse_mode="HTML")
        return
    if re.fullmatch(r"(پروفایل|profile)", low, re.I):
        await show_profile(c.bot, chat.id, user.id)
        return
    if re.fullmatch(r"(ویلو|willow|دریافت ویلو)", low, re.I):
        await do_willow(u, c, from_cb=False)
        return
    if re.fullmatch(r"(شهر|city)", low, re.I):
        if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            await u.message.reply_text("شهر فقط در گپ")
            return
        # fake callback path
        class Q:
            pass
        # simpler: duplicate city text
        city = ensure_city(chat)
        with tx() as conn:
            city = dict(conn.execute("SELECT * FROM cities WHERE chat_id=?", (chat.id,)).fetchone())
        wusers = json.loads(city.get("willow_users") or "[]")
        kb = InlineKeyboardMarkup([
            [btn("🕊 دونیت", f"don:{chat.id}:{user.id}", "success")],
            [btn("🏙 مارکت شهر", "m:citymarket", "primary")],
        ])
        await u.message.reply_text(
            f"🏙 <b>{city['title']}</b>\n👑 سطح {mono(to_roman(city['level']))}\n"
            f"💰 خزانه {num(city['treasury'])}\n👥 ویلو‌زن‌ها {num(len(wusers))}",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return
    if re.fullmatch(r"(بانک|bank)", low, re.I):
        uu = get_user(user.id)
        kb = InlineKeyboardMarkup([
            [btn("⬆️ واریز", f"bank:in:{user.id}", "success"), btn("⬇️ برداشت", f"bank:out:{user.id}", "danger")],
        ])
        await u.message.reply_text(f"🏦 کیف {num(uu['willow'])} | بانک {num(uu['bank'])}", reply_markup=kb)
        return
    if re.fullmatch(r"(بازار|فروشگاه|shop)", low, re.I):
        with tx() as conn:
            items = conn.execute("SELECT * FROM shop_items WHERE active=1").fetchall()
        rows = [[btn(f"{it['name']}|{num(it['price'])}", f"buy:{it['id']}:{user.id}", "success")] for it in items]
        await u.message.reply_text("🛒 بازار:", reply_markup=InlineKeyboardMarkup(rows))
        return
    if re.fullmatch(r"(مارکت|مارکت شهر)", low, re.I):
        await u.message.reply_text("از منو «مارکت شهر» را بزن یا در گپ باش.")
        return
    if re.fullmatch(r"(غارت|raid)", low, re.I):
        with tx() as conn:
            places = conn.execute("SELECT * FROM raid_places WHERE active=1").fetchall()
        rows = [[btn(p["name"], f"raid:{p['id']}:{user.id}", "danger")] for p in places]
        await u.message.reply_text("⚔️ مکان غارت:", reply_markup=InlineKeyboardMarkup(rows))
        return
    if re.fullmatch(r"(کارخانه|factory)", low, re.I):
        await u.message.reply_text("از منو کارخانه را باز کن:", reply_markup=main_menu_kb())
        return
    if re.fullmatch(r"(رتبه|رتبه‌بندی|لیدربرد)", low, re.I):
        with tx() as conn:
            rows = conn.execute("SELECT id,name,willow,level FROM users ORDER BY willow DESC LIMIT 10").fetchall()
        lines = ["🏆 رتبه‌بندی\n"]
        for i, r in enumerate(rows, 1):
            lines.append(f"{mono(to_roman(i))} {mention(r['id'], r['name'])} — {num(r['willow'])}")
        await u.message.reply_text("\n".join(lines), parse_mode="HTML")
        return
    if re.fullmatch(r"(پنل|admin)", low, re.I):
        if chat.type == ChatType.PRIVATE and is_admin(user.id):
            await u.message.reply_text("🎛 پنل مدیریت", reply_markup=admin_kb())
        return

    m = re.match(r"^(?:انتقال)\s+(.+)$", low, re.I)
    if m:
        amt = parse_amount(m.group(1))
        if not amt or not u.message.reply_to_message or not u.message.reply_to_message.from_user:
            await u.message.reply_text("ریپلای کن و بنویس: انتقال 10k")
            return
        to = u.message.reply_to_message.from_user
        if to.id == user.id or to.is_bot:
            await u.message.reply_text("نامعتبر")
            return
        tax = int(amt * sfloat("transfer_tax", 0.02))
        send = amt - tax
        try:
            change_willow(user.id, -amt, "transfer_out", str(to.id))
            ensure_user(to)
            change_willow(to.id, send, "transfer_in", str(user.id))
        except ValueError:
            await u.message.reply_text("موجودی کم")
            return
        await u.message.reply_text(
            f"✅ انتقال {num(send)} ویلو\n{mention(user.id, user.full_name)} → {mention(to.id, to.full_name)}",
            parse_mode="HTML",
        )
        return


async def on_photo(u: Update, c: ContextTypes.DEFAULT_TYPE):
    st = get_st(c)
    if not st or not is_admin(u.effective_user.id):
        return
    if st.get("kind") == "a_street_photo":
        fid = u.message.photo[-1].file_id
        set_st(c, "a_street_meta", {"photo": fid})
        await u.message.reply_text("حالا بفرست:\n<code>نام|نادر بودن|قیمت|قدرت</code>\nمثال: راکون شب|کمیاب|90000|25", parse_mode="HTML")
        return


async def on_text_street_meta(u: Update, c: ContextTypes.DEFAULT_TYPE):
    st = get_st(c)
    if not st or st.get("kind") != "a_street_meta":
        return False
    if not is_admin(u.effective_user.id):
        return False
    parts = (u.message.text or "").split("|")
    if len(parts) < 4:
        await u.message.reply_text("فرمت: نام|نادر بودن|قیمت|قدرت")
        return True
    name, rarity, price, power = parts[0].strip(), parts[1].strip(), parse_amount(parts[2]), parse_amount(parts[3])
    if not price or not power:
        await u.message.reply_text("قیمت/قدرت نامعتبر")
        return True
    with tx() as conn:
        conn.execute(
            "INSERT INTO street_raccoons(name,rarity,price,power,photo_file_id,active) VALUES (?,?,?,?,?,1)",
            (name, rarity, int(price), int(power), st["extra"].get("photo")),
        )
    clear_st(c)
    await u.message.reply_text(f"راکون خیابانی ثبت شد: {name}", reply_markup=admin_kb())
    return True


_orig = on_text

async def on_text(u: Update, c: ContextTypes.DEFAULT_TYPE):  # noqa: F811
    if await on_text_street_meta(u, c):
        return
    await _orig(u, c)


def main():
    init_db()
    from telegram.request import HTTPXRequest
    req = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=60.0,
        read_timeout=60.0,
        write_timeout=60.0,
        pool_timeout=60.0,
        proxy=PROXY_URL if PROXY_URL else None,
    )
    builder = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(req)
        .get_updates_request(req)
        .connect_timeout(60.0)
        .read_timeout(60.0)
        .write_timeout(60.0)
        .pool_timeout(60.0)
    )
    app = builder.build()
    async def _safe_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            await on_text(update, context)
        except Exception:
            log.exception("on_text error")
            try:
                if update.effective_message:
                    await update.effective_message.reply_text("⚠️ خطای موقت. دوباره امتحان کن.")
            except Exception:
                pass

    async def _safe_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            await on_callback(update, context)
        except Exception:
            log.exception("on_callback error")
            try:
                if update.callback_query:
                    await update.callback_query.answer("خطا", show_alert=True)
            except Exception:
                pass

    async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            # normalize as /start for on_text referral logic
            if update.message and update.message.text and not update.message.text.startswith("/start"):
                update.message.text = "/start"
            await on_text(update, context)
        except Exception:
            log.exception("start error")
            try:
                await update.message.reply_text("raccoon error")
            except Exception:
                pass

    app.add_handler(CommandHandler("start", _cmd_start))
    app.add_handler(CallbackQueryHandler(_safe_cb))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    # همه پیام‌های متنی (با یا بدون /)
    app.add_handler(MessageHandler(filters.TEXT, _safe_text))
    app.add_handler(MessageHandler(filters.COMMAND, _safe_text))
    log.info("Rivo bot starting (timeout=60s proxy=%s)", bool(PROXY_URL))
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        bootstrap_retries=10,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
