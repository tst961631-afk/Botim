# -*- coding: utf-8 -*-
"""بات ویس گروهی + پیوی تنظیمات + توکن/امتیاز/رفرال/مدیریت گپ"""
from __future__ import annotations
import os, re, time, logging, sqlite3, threading, tempfile, secrets
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters,
)
from telegram.constants import ChatType, ChatMemberStatus
from telegram.request import HTTPXRequest

BOT_TOKEN = "8932340319:AAEEKFbUFWBo_3Bc3NSYy_r8QhrVvXBy1Uk"
ADMIN_ID = 7530457395
DB_PATH = "voice_bot.db"
TZ = timezone(timedelta(hours=3, minutes=30))

VOICES = {
    "f1": ("fa-IR-DilaraNeural", "زن ۱ — دلارا"),
    "f2": ("fa-IR-DilaraNeural", "زن ۲ — دلارا"),
    "m1": ("fa-IR-FaridNeural", "مرد ۱ — فرید"),
    "m2": ("fa-IR-FaridNeural", "مرد ۲ — فرید"),
}
FEMALE = {"f1", "f2"}
MALE = {"m1", "m2"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("voicebot")
_lock = threading.RLock()

# ─── DB ───
def connect():
    c = sqlite3.connect(DB_PATH, timeout=60, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c

@contextmanager
def tx():
    with _lock:
        c = connect()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

def init_db():
    with tx() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            name TEXT DEFAULT '',
            gender TEXT DEFAULT 'f1',
            speed TEXT DEFAULT 'normal',
            tokens INTEGER DEFAULT 0,
            tokens_day TEXT DEFAULT '',
            points INTEGER DEFAULT 0,
            voices_total INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            last_voice REAL DEFAULT 0,
            referred_by INTEGER,
            blocked INTEGER DEFAULT 0,
            joined_at REAL
        );
        CREATE TABLE IF NOT EXISTS groups (
            chat_id INTEGER PRIMARY KEY,
            title TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS gift_codes (
            code TEXT PRIMARY KEY,
            amount INTEGER,
            uses_left INTEGER,
            expires REAL,
            only_chat_id INTEGER,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS gift_uses (
            code TEXT, user_id INTEGER, PRIMARY KEY(code, user_id)
        );
        """)
        c.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (ADMIN_ID,))
        defaults = {
            "daily_tokens": "20",
            "token_cost": "1",
            "max_chars": "400",
            "spam_sec": "5",
            "points_per_voice": "2",
            "token_price_points": "10",
            "ref_tokens": "5",
            "gender_lock": "0",
            "locked_gender": "f1",
            "mod_enabled": "1",
            "caption": "🎙 این ویس از طرف {mention}",
            "default_welcome": "سلام! از پیوی جنسیت و سرعت را تنظیم کن، بعد در گپ با -متن ویس بساز.",
        }
        for k, v in defaults.items():
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (k, v))

def sget(k, d=None):
    with tx() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
        return r["value"] if r else d

def sset(k, v):
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (k, str(v)))

def sint(k, d=0):
    try:
        return int(float(sget(k, d)))
    except Exception:
        return int(d)

def is_admin(uid):
    uid = int(uid)
    if uid == ADMIN_ID:
        return True
    with tx() as c:
        return bool(c.execute("SELECT 1 FROM admins WHERE user_id=?", (uid,)).fetchone())

def today_str():
    return datetime.now(TZ).strftime("%Y-%m-%d")

def ensure_user(user):
    if not user or getattr(user, "is_bot", False):
        return
    now = time.time()
    day = today_str()
    daily = sint("daily_tokens", 20)
    with tx() as c:
        r = c.execute("SELECT * FROM users WHERE id=?", (user.id,)).fetchone()
        if not r:
            c.execute(
                """INSERT INTO users(id,username,name,tokens,tokens_day,joined_at)
                   VALUES (?,?,?,?,?,?)""",
                (user.id, user.username or "", user.full_name or str(user.id), daily, day, now),
            )
        else:
            c.execute(
                "UPDATE users SET username=?, name=? WHERE id=?",
                (user.username or "", user.full_name or str(user.id), user.id),
            )
            if (r["tokens_day"] or "") != day:
                # سطح روی سقف روزانه اثر دارد
                bonus = max(0, (int(r["level"] or 1) - 1) * 2)
                c.execute(
                    "UPDATE users SET tokens=?, tokens_day=? WHERE id=?",
                    (daily + bonus, day, user.id),
                )

def get_user(uid):
    with tx() as c:
        return c.execute("SELECT * FROM users WHERE id=?", (int(uid),)).fetchone()

def btn(text, data):
    return InlineKeyboardButton(str(text)[:64], callback_data=str(data)[:64])

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

def calc_level(voices_total):
    # هر ۵۰ ویس یک سطح
    return max(1, int(voices_total) // 50 + 1)

def mention_html(user):
    name = user.full_name or str(user.id)
    return f'<a href="tg://user?id={user.id}">{name}</a>'

def render_caption(template, user, gender_label, speed_label, tokens_left, chat_title=""):
    t = template or "🎙 این ویس از طرف {mention}"
    rep = {
        "{mention}": mention_html(user),
        "{name}": user.full_name or "",
        "{username}": ("@" + user.username) if user.username else "",
        "{id}": str(user.id),
        "{gender}": gender_label,
        "{speed}": speed_label,
        "{date}": datetime.now(TZ).strftime("%Y/%m/%d"),
        "{time}": datetime.now(TZ).strftime("%H:%M"),
        "{chat}": chat_title or "",
        "{tokens_left}": str(tokens_left),
    }
    for k, v in rep.items():
        t = t.replace(k, v)
    return t

def rate_for_speed(speed):
    if speed == "fast":
        return "+25%"
    if speed == "slow":
        return "-20%"
    return "+0%"

async def is_group_admin(bot, chat_id, user_id):
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        return m.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
            "administrator",
            "creator",
        )
    except Exception:
        return False

async def bot_can_delete(bot, chat_id):
    try:
        me = await bot.get_me()
        m = await bot.get_chat_member(chat_id, me.id)
        if m.status in (ChatMemberStatus.OWNER, "creator"):
            return True
        if m.status in (ChatMemberStatus.ADMINISTRATOR, "administrator"):
            return bool(getattr(m, "can_delete_messages", False))
    except Exception:
        pass
    return False

async def bot_can_restrict(bot, chat_id):
    try:
        me = await bot.get_me()
        m = await bot.get_chat_member(chat_id, me.id)
        if m.status in (ChatMemberStatus.OWNER, "creator"):
            return True
        if m.status in (ChatMemberStatus.ADMINISTRATOR, "administrator"):
            return bool(getattr(m, "can_restrict_members", False))
    except Exception:
        pass
    return False

def group_allowed(chat_id):
    with tx() as c:
        n = c.execute("SELECT COUNT(*) c FROM groups WHERE active=1").fetchone()["c"]
        if n == 0:
            return True  # اگر لیست خالی باشد همه گپ‌ها آزاد
        r = c.execute(
            "SELECT 1 FROM groups WHERE chat_id=? AND active=1", (int(chat_id),)
        ).fetchone()
        return bool(r)

async def tts_save(text, voice_id, speed, path):
    import edge_tts
    voice = VOICES.get(voice_id, VOICES["f1"])[0]
    rate = rate_for_speed(speed)
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(path)

# ─── UI ───
def pm_kb(uid):
    u = get_user(uid)
    g = u["gender"] if u else "f1"
    s = u["speed"] if u else "normal"
    return InlineKeyboardMarkup([
        [btn("🎭 جنسیت / صدا", f"pm:voice:{uid}")],
        [btn("⚡ سرعت", f"pm:speed:{uid}")],
        [btn("💎 وضعیت من", f"pm:status:{uid}")],
        [btn("🛒 خرید توکن با امتیاز", f"pm:buy:{uid}")],
        [btn("🔗 لینک دعوت", f"pm:ref:{uid}")],
        [btn("🎁 کد هدیه", f"pm:gift:{uid}")],
    ])

def admin_kb():
    return InlineKeyboardMarkup([
        [btn("⚙️ تنظیمات", "a:settings"), btn("📢 گپ‌ها", "a:groups")],
        [btn("🎁 کد هدیه", "a:gift"), btn("🚫 بلک‌لیست", "a:block")],
        [btn("📝 قالب کپشن", "a:caption"), btn("🛡 مدیریت گپ", "a:mod")],
        [btn("➕ واریز توکن", "a:addtok"), btn("👤 ادمین", "a:adm")],
        [btn("📊 آمار", "a:stats"), btn("❌ بستن", "a:close")],
    ])

# ─── handlers ───
async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    try:
        if not u.message:
            return
        user = u.effective_user
        if not user:
            return
        # اول یک جواب سریع تا معلوم شود ربات زنده است
        try:
            await u.message.reply_text("⏳ در حال آماده‌سازی...")
        except Exception as e:
            log.error("cannot reply: %s", e)
            return

        ensure_user(user)
        text = u.message.text or ""
        m = re.search(r"ref[_-]?(\d+)", text)
        if m:
            ref = int(m.group(1))
            if ref != user.id:
                with tx() as conn:
                    uu = conn.execute("SELECT referred_by FROM users WHERE id=?", (user.id,)).fetchone()
                    if uu and not uu["referred_by"]:
                        conn.execute("UPDATE users SET referred_by=? WHERE id=?", (ref, user.id))
                        reward = sint("ref_tokens", 5)
                        conn.execute(
                            "UPDATE users SET tokens=tokens+? WHERE id=?", (reward, ref)
                        )
                try:
                    await c.bot.send_message(ref, "🎉 دعوت موفق! +" + str(sint("ref_tokens", 5)) + " توکن")
                except Exception:
                    pass

        if u.effective_chat.type != ChatType.PRIVATE:
            await u.message.reply_text("تنظیمات در پیوی ربات است. پیوی را استارت کن.")
            return

        welcome = sget("default_welcome", "سلام!")
        uu = get_user(user.id)
        tokens = uu["tokens"] if uu else 0
        points = uu["points"] if uu else 0
        level = uu["level"] if uu else 1
        await u.message.reply_text(
            welcome + "\n\n"
            "💎 توکن: " + str(tokens) + " | ⭐ امتیاز: " + str(points) + " | 📶 سطح: " + str(level) + "\n"
            "در گپ بنویس: -متن ویس",
            reply_markup=pm_kb(user.id),
        )
    except Exception:
        log.exception("cmd_start")
        try:
            await u.message.reply_text("⚠️ خطا در استارت. لاگ سرور را چک کن.")
        except Exception:
            pass


async def on_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    data = q.data or ""
    user = u.effective_user
    ensure_user(user)

    if data == "a:close":
        await q.answer()
        try:
            await q.message.delete()
        except Exception:
            pass
        return

    # ownership pm
    if data.startswith("pm:"):
        try:
            if int(data.split(":")[-1]) != user.id:
                await q.answer("برای تو نیست", show_alert=True)
                return
        except Exception:
            pass

    await q.answer()

    if data.startswith("pm:voice:"):
        lock = sget("gender_lock", "0") == "1"
        rows = []
        if lock:
            lg = sget("locked_gender", "f1")
            rows.append([btn("قفل ادمین: " + VOICES.get(lg, VOICES["f1"])[1], "noop")])
        else:
            for vid, (_, label) in VOICES.items():
                rows.append([btn(label, f"pm:setv:{vid}:{user.id}")])
        rows.append([btn("🔙", f"pm:home:{user.id}")])
        await q.edit_message_text("صدا / جنسیت:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("pm:setv:"):
        parts = data.split(":")
        vid, uid = parts[2], int(parts[3])
        if user.id != uid:
            return
        if sget("gender_lock", "0") == "1":
            await q.answer("جنسیت توسط ادمین قفل است", show_alert=True)
            return
        if vid not in VOICES:
            return
        with tx() as conn:
            conn.execute("UPDATE users SET gender=? WHERE id=?", (vid, user.id))
        await q.edit_message_text("✅ صدا تنظیم شد: " + VOICES[vid][1], reply_markup=pm_kb(user.id))
        return

    if data.startswith("pm:speed:"):
        rows = [
            [btn("🐢 آرام", f"pm:sets:slow:{user.id}")],
            [btn("🚶 عادی", f"pm:sets:normal:{user.id}")],
            [btn("⚡ سریع", f"pm:sets:fast:{user.id}")],
            [btn("🔙", f"pm:home:{user.id}")],
        ]
        await q.edit_message_text("سرعت حرف زدن:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("pm:sets:"):
        parts = data.split(":")
        sp, uid = parts[2], int(parts[3])
        if user.id != uid:
            return
        with tx() as conn:
            conn.execute("UPDATE users SET speed=? WHERE id=?", (sp, user.id))
        await q.edit_message_text("✅ سرعت تنظیم شد.", reply_markup=pm_kb(user.id))
        return

    if data.startswith("pm:status:"):
        uu = get_user(user.id)
        gl = VOICES.get(uu["gender"], VOICES["f1"])[1]
        sl = {"slow": "آرام", "fast": "سریع"}.get(uu["speed"], "عادی")
        await q.edit_message_text(
            f"💎 توکن: <b>{uu['tokens']}</b>\n"
            f"⭐ امتیاز: <b>{uu['points']}</b>\n"
            f"📶 سطح: <b>{uu['level']}</b>\n"
            f"🎙 ویس کل: <b>{uu['voices_total']}</b>\n"
            f"🎭 {gl}\n⚡ {sl}",
            parse_mode="HTML",
            reply_markup=pm_kb(user.id),
        )
        return

    if data.startswith("pm:buy:"):
        price = sint("token_price_points", 10)
        uu = get_user(user.id)
        if int(uu["points"]) < price:
            await q.answer(f"امتیاز کم است (نیاز {price})", show_alert=True)
            return
        with tx() as conn:
            conn.execute(
                "UPDATE users SET points=points-?, tokens=tokens+1 WHERE id=?",
                (price, user.id),
            )
        await q.answer("۱ توکن خریدی", show_alert=True)
        uu = get_user(user.id)
        await q.edit_message_text(
            f"✅ خرید انجام شد\n💎 {uu['tokens']} | ⭐ {uu['points']}",
            reply_markup=pm_kb(user.id),
        )
        return

    if data.startswith("pm:ref:"):
        me = await c.bot.get_me()
        link = f"https://t.me/{me.username}?start=ref{user.id}"
        await q.edit_message_text(
            f"🔗 لینک دعوت:\n<code>{link}</code>\nپاداش: {sint('ref_tokens',5)} توکن",
            parse_mode="HTML",
            reply_markup=pm_kb(user.id),
        )
        return

    if data.startswith("pm:gift:"):
        set_st(c, "gift")
        await q.edit_message_text("کد هدیه را بفرست:")
        return

    if data.startswith("pm:home:"):
        await q.edit_message_text("منوی تنظیمات:", reply_markup=pm_kb(user.id))
        return

    # admin
    if data.startswith("a:") and not is_admin(user.id):
        await q.answer("دسترسی نداری", show_alert=True)
        return

    if data == "a:settings":
        await q.edit_message_text(
            f"daily_tokens={sget('daily_tokens')}\n"
            f"token_cost={sget('token_cost')}\n"
            f"max_chars={sget('max_chars')}\n"
            f"spam_sec={sget('spam_sec')}\n"
            f"points_per_voice={sget('points_per_voice')}\n"
            f"token_price_points={sget('token_price_points')}\n"
            f"ref_tokens={sget('ref_tokens')}\n"
            f"gender_lock={sget('gender_lock')} locked={sget('locked_gender')}\n\n"
            "بفرست مثلا:\n<code>spam_sec 8</code>\n<code>daily_tokens 30</code>\n"
            "<code>gender_lock 1</code>\n<code>locked_gender m1</code>",
            parse_mode="HTML",
            reply_markup=admin_kb(),
        )
        set_st(c, "a_set")
        return

    if data == "a:caption":
        set_st(c, "a_cap")
        await q.edit_message_text(
            f"قالب فعلی:\n{sget('caption')}\n\n"
            "متغیرها: {{mention}} {{name}} {{username}} {{id}} {{gender}} {{speed}} {{date}} {{time}} {{chat}} {{tokens_left}}",
            reply_markup=admin_kb(),
        )
        return

    if data == "a:mod":
        cur = sget("mod_enabled", "1")
        sset("mod_enabled", "0" if cur == "1" else "1")
        await q.edit_message_text(
            f"مدیریت گپ (سیک/بن/سکوت): {'خاموش' if cur=='1' else 'روشن'}",
            reply_markup=admin_kb(),
        )
        return

    if data == "a:groups":
        set_st(c, "a_grp")
        with tx() as conn:
            rows = conn.execute("SELECT * FROM groups").fetchall()
        lines = ["گپ‌های مجاز (خالی=همه):\n"] + [
            f"{'✅' if r['active'] else '❌'} {r['title']} | {r['chat_id']}" for r in rows
        ]
        await q.edit_message_text(
            "\n".join(lines) + "\n\nآیدی گپ را بفرست برای افزودن/حذف\nدر گپ: /addgroup",
            reply_markup=admin_kb(),
        )
        return

    if data == "a:gift":
        set_st(c, "a_gift")
        await q.edit_message_text(
            "فرمت:\n<code>کد مبلغ تعداد روز [chat_id]</code>\n"
            "مثال: <code>MEOW 10 50 7</code>\n"
            "با گپ خاص: <code>MEOW 10 50 7 -100123</code>",
            parse_mode="HTML",
            reply_markup=admin_kb(),
        )
        return

    if data == "a:block":
        set_st(c, "a_block")
        await q.edit_message_text("آیدی برای بلاک/آنبلاک:", reply_markup=admin_kb())
        return

    if data == "a:addtok":
        set_st(c, "a_addtok")
        await q.edit_message_text("آیدی تعداد_توکن\n<code>123456 20</code>", parse_mode="HTML", reply_markup=admin_kb())
        return

    if data == "a:adm":
        set_st(c, "a_adm")
        await q.edit_message_text("ادمین + آیدی / ادمین - آیدی", reply_markup=admin_kb())
        return

    if data == "a:stats":
        with tx() as conn:
            uc = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
            vt = conn.execute("SELECT SUM(voices_total) s FROM users").fetchone()["s"] or 0
        await q.edit_message_text(f"کاربران: {uc}\nویس کل: {vt}", reply_markup=admin_kb())
        return


async def do_voice(update: Update, context: ContextTypes.DEFAULT_TYPE, body: str):
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    ensure_user(user)
    uu = get_user(user.id)

    if int(uu["blocked"] or 0):
        return
    if chat.type != ChatType.PRIVATE and not group_allowed(chat.id):
        return

    cost = sint("token_cost", 1)
    if int(uu["tokens"]) < cost:
        try:
            await msg.reply_text("توکن کافی نداری. پیوی ربات: دریافت روزانه / خرید / کد هدیه")
        except Exception:
            pass
        return

    spam = sint("spam_sec", 5)
    if spam > 0 and float(uu["last_voice"] or 0) + spam > time.time():
        left = int(float(uu["last_voice"]) + spam - time.time())
        try:
            await msg.reply_text(f"کمی صبر کن ({left} ثانیه)")
        except Exception:
            pass
        return

    max_c = sint("max_chars", 400)
    if len(body) > max_c:
        body = body[:max_c]

    # gender lock
    gender = uu["gender"] or "f1"
    if sget("gender_lock", "0") == "1":
        gender = sget("locked_gender", "f1")
    speed = uu["speed"] or "normal"

    reply_to = msg.reply_to_message.message_id if msg.reply_to_message else msg.message_id

    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    path = tmp.name
    tmp.close()
    try:
        await tts_save(body, gender, speed, path)
        with tx() as conn:
            conn.execute(
                "UPDATE users SET tokens=tokens-?, points=points+?, voices_total=voices_total+1, last_voice=?, level=? WHERE id=?",
                (
                    cost,
                    sint("points_per_voice", 2),
                    time.time(),
                    calc_level(int(uu["voices_total"]) + 1),
                    user.id,
                ),
            )
        uu2 = get_user(user.id)
        gl = VOICES.get(gender, VOICES["f1"])[1]
        sl = {"slow": "آرام", "fast": "سریع"}.get(speed, "عادی")
        cap = render_caption(
            sget("caption", "🎙 این ویس از طرف {mention}"),
            user, gl, sl, int(uu2["tokens"]),
            getattr(chat, "title", "") or "",
        )
        with open(path, "rb") as f:
            sent = await context.bot.send_voice(
                chat_id=chat.id,
                voice=f,
                caption=cap,
                parse_mode="HTML",
                reply_to_message_id=reply_to,
            )
        try:
            await context.bot.set_message_reaction(chat.id, sent.message_id, reaction="🎙")
        except Exception:
            pass
        # delete original if possible
        if chat.type != ChatType.PRIVATE and await bot_can_delete(context.bot, chat.id):
            try:
                await msg.delete()
            except Exception:
                pass
    except Exception as e:
        log.exception("voice")
        try:
            await msg.reply_text("⚠️ خطا: " + str(e)[:100])
        except Exception:
            pass
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


async def handle_mod(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True اگر پیام مدیریتی بود و مصرف شد."""
    if sget("mod_enabled", "1") != "1":
        return False
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return False
    text = (msg.text or "").strip()
    if not text:
        return False

    # فقط یک کلمه یا خیلی کوتاه
    low = text.lower()
    cmd = None
    if low in ("سیک", "بن", "ban"):
        cmd = "ban"
    elif low in ("سکوت", "mute"):
        cmd = "mute"
    else:
        return False

    # فقط ادمین گپ
    if not await is_group_admin(context.bot, chat.id, user.id):
        return True  # نادیده — جواب نده

    if not await bot_can_restrict(context.bot, chat.id):
        # بات ادمین نیست → هیچ جوابی نده
        return True

    if not msg.reply_to_message or not msg.reply_to_message.from_user:
        return True  # بدون ریپلای نادیده

    target = msg.reply_to_message.from_user
    if target.is_bot:
        return True
    if await is_group_admin(context.bot, chat.id, target.id):
        try:
            await msg.reply_text("این فرد ادمین است.")
        except Exception:
            pass
        return True

    try:
        if cmd == "ban":
            await context.bot.ban_chat_member(chat.id, target.id)
            await msg.reply_text("کاربر بن شد.")
        else:
            # mute 1 hour
            until = int(time.time()) + 3600
            await context.bot.restrict_chat_member(
                chat.id,
                target.id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            await msg.reply_text("یک ساعت سکوت اعمال شد.")
    except Exception as e:
        try:
            await msg.reply_text("خطا: " + str(e)[:100])
        except Exception:
            pass
    return True


async def on_text(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.message.text:
        return
    user = u.effective_user
    chat = u.effective_chat
    text = u.message.text.strip()
    ensure_user(user)

    # mod commands in groups
    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        if await handle_mod(u, c):
            return
        # voice
        if text.startswith("-"):
            body = text[1:].strip()
            if body:
                await do_voice(u, c, body)
            return
        return

    # private
    if chat.type != ChatType.PRIVATE:
        return

    low = re.sub(r"^/", "", text).strip().lower()
    st = get_st(c)

    if low in ("پنل", "admin") and is_admin(user.id):
        clear_st(c)
        await u.message.reply_text("🎛 پنل ادمین", reply_markup=admin_kb())
        return

    if low in ("منو", "menu"):
        await u.message.reply_text("منو:", reply_markup=pm_kb(user.id))
        return
    if low in ("start", "استارت") or text.startswith("/start"):
        await cmd_start(u, c)
        return

    if st and st["kind"] == "gift":
        code = text.strip().upper()
        with tx() as conn:
            g = conn.execute(
                "SELECT * FROM gift_codes WHERE code=? AND active=1", (code,)
            ).fetchone()
            if not g or int(g["uses_left"]) < 1:
                await u.message.reply_text("کد نامعتبر")
                clear_st(c)
                return
            if g["expires"] and float(g["expires"]) < time.time():
                await u.message.reply_text("کد منقضی شده")
                clear_st(c)
                return
            if g["only_chat_id"]:
                await u.message.reply_text(
                    "این کد فقط داخل گپ خاص قابل استفاده است. کد را در همان گپ بفرست."
                )
                clear_st(c)
                return
            if conn.execute(
                "SELECT 1 FROM gift_uses WHERE code=? AND user_id=?", (code, user.id)
            ).fetchone():
                await u.message.reply_text("قبلاً استفاده کردی")
                clear_st(c)
                return
            conn.execute("UPDATE gift_codes SET uses_left=uses_left-1 WHERE code=?", (code,))
            conn.execute("INSERT INTO gift_uses(code,user_id) VALUES (?,?)", (code, user.id))
            conn.execute("UPDATE users SET tokens=tokens+? WHERE id=?", (int(g["amount"]), user.id))
        clear_st(c)
        await u.message.reply_text(f"✅ +{g['amount']} توکن")
        return

    # gift in group with only_chat_id — handled below in group? skip for brevity in group as text starting without -

    if st and is_admin(user.id):
        kind = st["kind"]
        if kind == "a_set":
            m = re.match(r"(\w+)\s+(.+)", text)
            if m and m.group(1) in (
                "daily_tokens", "token_cost", "max_chars", "spam_sec",
                "points_per_voice", "token_price_points", "ref_tokens",
                "gender_lock", "locked_gender",
            ):
                sset(m.group(1), m.group(2).strip())
                clear_st(c)
                await u.message.reply_text("OK", reply_markup=admin_kb())
            return
        if kind == "a_cap":
            sset("caption", text)
            clear_st(c)
            await u.message.reply_text("قالب ذخیره شد", reply_markup=admin_kb())
            return
        if kind == "a_grp":
            try:
                cid = int(text.strip())
            except ValueError:
                await u.message.reply_text("آیدی عددی")
                return
            with tx() as conn:
                r = conn.execute("SELECT 1 FROM groups WHERE chat_id=?", (cid,)).fetchone()
                if r:
                    conn.execute("DELETE FROM groups WHERE chat_id=?", (cid,))
                    await u.message.reply_text("حذف شد", reply_markup=admin_kb())
                else:
                    conn.execute(
                        "INSERT INTO groups(chat_id,title,active) VALUES (?,?,1)",
                        (cid, str(cid)),
                    )
                    await u.message.reply_text("افزودن شد", reply_markup=admin_kb())
            clear_st(c)
            return
        if kind == "a_gift":
            parts = text.split()
            if len(parts) >= 4:
                code, amount, uses, days = parts[0].upper(), parts[1], parts[2], parts[3]
                only = int(parts[4]) if len(parts) >= 5 else None
                try:
                    amount, uses, days = int(amount), int(uses), int(days)
                except ValueError:
                    await u.message.reply_text("اعداد نامعتبر")
                    return
                exp = time.time() + days * 86400
                with tx() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO gift_codes(code,amount,uses_left,expires,only_chat_id,active) VALUES (?,?,?,?,?,1)",
                        (code, amount, uses, exp, only),
                    )
                clear_st(c)
                await u.message.reply_text(f"کد {code} ثبت شد", reply_markup=admin_kb())
            return
        if kind == "a_block":
            try:
                tid = int(re.sub(r"\D", "", text))
            except Exception:
                await u.message.reply_text("آیدی؟")
                return
            ensure_user(type("U", (), {"id": tid, "username": "", "full_name": str(tid), "is_bot": False})())
            with tx() as conn:
                cur = conn.execute("SELECT blocked FROM users WHERE id=?", (tid,)).fetchone()
                nb = 0 if cur and cur["blocked"] else 1
                conn.execute("UPDATE users SET blocked=? WHERE id=?", (nb, tid))
            clear_st(c)
            await u.message.reply_text("بلاک تغییر کرد", reply_markup=admin_kb())
            return
        if kind == "a_addtok":
            parts = text.split()
            if len(parts) >= 2:
                try:
                    tid, amt = int(parts[0]), int(parts[1])
                except ValueError:
                    await u.message.reply_text("نامعتبر")
                    return
                ensure_user(type("U", (), {"id": tid, "username": "", "full_name": str(tid), "is_bot": False})())
                with tx() as conn:
                    conn.execute("UPDATE users SET tokens=tokens+? WHERE id=?", (amt, tid))
                clear_st(c)
                await u.message.reply_text("OK", reply_markup=admin_kb())
            return
        if kind == "a_adm":
            m = re.match(r"ادمین\s*([+-])\s*(\d+)", text)
            if m:
                with tx() as conn:
                    if m.group(1) == "+":
                        conn.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (int(m.group(2)),))
                    elif int(user.id) == ADMIN_ID:
                        conn.execute("DELETE FROM admins WHERE user_id=?", (int(m.group(2)),))
                clear_st(c)
                await u.message.reply_text("OK", reply_markup=admin_kb())
            return

    # private voice test with -
    if text.startswith("-"):
        body = text[1:].strip()
        if body:
            await do_voice(u, c, body)
        return


async def cmd_addgroup(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_admin(u.effective_user.id):
        return
    chat = u.effective_chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await u.message.reply_text("فقط در گپ")
        return
    with tx() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO groups(chat_id,title,active) VALUES (?,?,1)",
            (chat.id, chat.title or str(chat.id)),
        )
    await u.message.reply_text("گپ به لیست مجاز اضافه شد.")


async def open_admin_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.effective_user or not is_admin(u.effective_user.id):
        return
    if u.effective_chat.type != ChatType.PRIVATE:
        return
    await u.message.reply_text("🎛 پنل ادمین", reply_markup=admin_kb())


async def post_init(app_):
    try:
        await app_.bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        log.warning("delete_webhook: %s", e)
    me = await app_.bot.get_me()
    log.info("bot identity: @%s id=%s", me.username, me.id)


def main():
    init_db()
    req = HTTPXRequest(connect_timeout=60.0, read_timeout=90.0, write_timeout=90.0, pool_timeout=60.0)
    get_req = HTTPXRequest(connect_timeout=60.0, read_timeout=90.0, write_timeout=90.0, pool_timeout=60.0)
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(req)
        .get_updates_request(get_req)
        .post_init(post_init)
        .build()
    )

    async def safe_text(update, context):
        try:
            await on_text(update, context)
        except Exception as e:
            log.exception("text")
            try:
                await update.effective_message.reply_text("⚠️ " + str(e)[:120])
            except Exception:
                pass

    async def safe_cb(update, context):
        try:
            await on_cb(update, context)
        except Exception as e:
            log.exception("cb")
            try:
                await update.callback_query.answer(str(e)[:100], show_alert=True)
            except Exception:
                pass

    app.add_handler(CommandHandler("start", cmd_start), group=0)
    app.add_handler(CommandHandler("addgroup", cmd_addgroup), group=0)
    app.add_handler(CommandHandler("admin", open_admin_cmd), group=0)
    app.add_handler(CallbackQueryHandler(safe_cb), group=0)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, safe_text), group=1)
    log.info("voice bot up — polling...")
    app.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
        bootstrap_retries=10,
    )


if __name__ == "__main__":
    main()
