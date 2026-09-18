# -*- coding: utf-8 -*-
"""بات ویس گروهی + پیوی تنظیمات + توکن/امتیاز/رفرال + خوش‌آمد"""
from __future__ import annotations
import os, re, time, logging, sqlite3, threading, tempfile
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters,
)
from telegram.constants import ChatType
from telegram.request import HTTPXRequest

BOT_TOKEN = "8932340319:AAEEKFbUFWBo_3Bc3NSYy_r8QhrVvXBy1Uk"
ADMIN_ID = 7530457395
DB_PATH = "voice_bot.db"
TZ = timezone(timedelta(hours=3, minutes=30))

ENGINE = {
    "f1": "fa-IR-DilaraNeural",
    "f2": "fa-IR-DilaraNeural",
    "m1": "fa-IR-FaridNeural",
    "m2": "fa-IR-FaridNeural",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("voicebot")
_lock = threading.RLock()


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
            "welcome_enabled": "1",
            "caption": "🎙 این ویس از طرف {mention}",
            "default_welcome": "سلام! از پیوی جنسیت و سرعت را تنظیم کن، بعد در گپ با - متن ویس بساز.",
            "help_text": "",
            "member_welcome": "سلام {mention} به گروه خوش آمدی 👋",
            "welcome_media_type": "",
            "welcome_media_id": "",
            "voice_f1": "زن ۱",
            "voice_f2": "زن ۲",
            "voice_m1": "مرد ۱",
            "voice_m2": "مرد ۲",
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


def voice_label(vid):
    return sget("voice_" + vid, vid) or vid


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
                "INSERT INTO users(id,username,name,tokens,tokens_day,joined_at) VALUES (?,?,?,?,?,?)",
                (user.id, user.username or "", user.full_name or str(user.id), daily, day, now),
            )
        else:
            c.execute(
                "UPDATE users SET username=?, name=? WHERE id=?",
                (user.username or "", user.full_name or str(user.id), user.id),
            )
            if (r["tokens_day"] or "") != day:
                bonus = max(0, (int(r["level"] or 1) - 1) * 2)
                c.execute(
                    "UPDATE users SET tokens=?, tokens_day=? WHERE id=?",
                    (daily + bonus, day, user.id),
                )


def get_user(uid):
    with tx() as c:
        return c.execute("SELECT * FROM users WHERE id=?", (int(uid),)).fetchone()


def btn(text, data, style=None):
    kw = {"text": str(text)[:64], "callback_data": str(data)[:64]}
    if style in ("success", "danger", "primary"):
        kw["style"] = style
    try:
        return InlineKeyboardButton(**kw)
    except TypeError:
        kw.pop("style", None)
        return InlineKeyboardButton(**kw)


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
    return max(1, int(voices_total) // 50 + 1)


def mention_html(user):
    name = user.full_name or str(user.id)
    return '<a href="tg://user?id=%s">%s</a>' % (user.id, name)


def mono(s):
    return "<code>%s</code>" % str(s).replace("<", "").replace(">", "")


VARS_HELP = (
    "متغیرها (Mono):\n"
    + mono("{mention}") + " تگ قابل‌کلیک\n"
    + mono("{name}") + " اسم\n"
    + mono("{username}") + " یوزرنیم\n"
    + mono("{id}") + " آیدی\n"
    + mono("{gender}") + " صدا\n"
    + mono("{speed}") + " سرعت\n"
    + mono("{date}") + " تاریخ\n"
    + mono("{time}") + " ساعت\n"
    + mono("{chat}") + " اسم گپ\n"
    + mono("{tokens_left}") + " توکن باقی"
)


def render_tpl(template, user, gender_label="", speed_label="", tokens_left="", chat_title=""):
    t = template or ""
    rep = {
        "{mention}": mention_html(user),
        "{name}": user.full_name or "",
        "{username}": ("@" + user.username) if getattr(user, "username", None) else "",
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


async def bot_can_delete(bot, chat_id):
    try:
        from telegram.constants import ChatMemberStatus
        me = await bot.get_me()
        m = await bot.get_chat_member(chat_id, me.id)
        if m.status in (ChatMemberStatus.OWNER, "creator"):
            return True
        if m.status in (ChatMemberStatus.ADMINISTRATOR, "administrator"):
            return bool(getattr(m, "can_delete_messages", False))
    except Exception:
        pass
    return False


def group_allowed(chat_id):
    with tx() as c:
        n = c.execute("SELECT COUNT(*) c FROM groups WHERE active=1").fetchone()["c"]
        if n == 0:
            return True
        return bool(c.execute("SELECT 1 FROM groups WHERE chat_id=? AND active=1", (int(chat_id),)).fetchone())


async def tts_save(text, voice_id, speed, path):
    voice = ENGINE.get(voice_id, ENGINE["f1"])
    rate = rate_for_speed(speed)
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        await communicate.save(path)
        return
    except ImportError:
        log.warning("edge_tts missing")
    except Exception as e:
        log.warning("edge_tts failed: %s", e)
    try:
        from gtts import gTTS
        gTTS(text=text, lang="fa").save(path)
    except ImportError:
        raise RuntimeError("موتور ویس نصب نیست: pip install edge-tts")


def parse_voice_cmd(text: str):
    if not text:
        return None
    raw = text
    m = re.match(r"^-\s*س\s+(.*)$", raw)
    if m:
        return m.group(1).strip() or None
    m = re.match(r"^-\s+(.*)$", raw)
    if m:
        return m.group(1).strip() or None
    m = re.match(r"^-(.*)$", raw)
    if m:
        body = m.group(1).strip()
        if body.startswith("س "):
            body = body[2:].strip()
        elif body == "س":
            return None
        return body or None
    return None


def default_help():
    custom = (sget("help_text") or "").strip()
    if custom:
        return custom
    return (
        "📖 راهنما\n\nدر گپ اول پیام:\n"
        + mono("-سلام") + "\n"
        + mono("- س سلام") + "\n"
        + mono("-س سلام") + "\n\n"
        "ریپلای روی کسی + -متن → ویس روی همان پیام او\n"
        "تنظیم صدا/سرعت در پیوی ربات"
    )


def pm_kb(uid):
    return InlineKeyboardMarkup([
        [btn("🎭 جنسیت / صدا", "pm:voice:%s" % uid, "primary")],
        [btn("⚡ سرعت", "pm:speed:%s" % uid, "primary")],
        [btn("💎 وضعیت من", "pm:status:%s" % uid, "success")],
        [btn("🛒 خرید توکن", "pm:buy:%s" % uid, "success")],
        [btn("🔗 دعوت", "pm:ref:%s" % uid, "primary"), btn("🎁 کد هدیه", "pm:gift:%s" % uid, "success")],
        [btn("📖 راهنما", "pm:help:%s" % uid, "primary")],
    ])


def admin_kb():
    wel = sget("welcome_enabled", "1") == "1"
    return InlineKeyboardMarkup([
        [btn("⚙️ تنظیمات", "a:settings", "primary"), btn("📢 گپ‌ها", "a:groups", "primary")],
        [btn("🎁 کد هدیه", "a:gift", "success"), btn("🚫 بلک‌لیست", "a:block", "danger")],
        [btn("📝 کپشن ویس", "a:caption", "primary"), btn("🎭 اسم صداها", "a:vnames", "primary")],
        [btn("👋 خوش‌آمد: " + ("روشن" if wel else "خاموش"), "a:wel", "success" if wel else "danger")],
        [btn("💬 متن خوش‌آمد", "a:weltxt", "primary"), btn("🖼 عکس/گیف خوش‌آمد", "a:welmedia", "primary")],
        [btn("🗑 پاک کردن مدیا خوش‌آمد", "a:welclear", "danger")],
        [btn("📄 متن راهنما", "a:help", "primary"), btn("💬 متن استارت", "a:startw", "primary")],
        [btn("➕ واریز توکن", "a:addtok", "success"), btn("👤 ادمین", "a:adm", "primary")],
        [btn("📊 آمار", "a:stats", "primary"), btn("❌ بستن", "a:close", "danger")],
    ])


async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    try:
        if not u.message or not u.effective_user:
            return
        user = u.effective_user
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
                        conn.execute("UPDATE users SET tokens=tokens+? WHERE id=?", (sint("ref_tokens", 5), ref))
                try:
                    await c.bot.send_message(ref, "🎉 دعوت موفق! +%s توکن" % sint("ref_tokens", 5))
                except Exception:
                    pass
        if u.effective_chat.type != ChatType.PRIVATE:
            await u.message.reply_text("تنظیمات در پیوی ربات است.")
            return
        welcome = sget("default_welcome", "سلام!")
        uu = get_user(user.id)
        await u.message.reply_text(
            welcome + "\n\n💎 توکن: %s | ⭐ امتیاز: %s | 📶 سطح: %s\nدر گپ: - متن" % (
                uu["tokens"] if uu else 0, uu["points"] if uu else 0, uu["level"] if uu else 1
            ),
            reply_markup=pm_kb(user.id),
        )
    except Exception:
        log.exception("start")
        try:
            await u.message.reply_text("⚠️ خطا در استارت")
        except Exception:
            pass


async def cmd_help(u: Update, c: ContextTypes.DEFAULT_TYPE):
    ensure_user(u.effective_user)
    await u.message.reply_text(default_help(), parse_mode="HTML")


async def on_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    data = q.data or ""
    user = u.effective_user
    ensure_user(user)

    if data == "noop":
        await q.answer()
        return
    if data == "a:close":
        clear_st(c)
        await q.answer()
        try:
            await q.message.delete()
        except Exception:
            pass
        return
    if data == "a:home":
        clear_st(c)
        await q.answer()
        await q.edit_message_text("🎛 پنل ادمین", reply_markup=admin_kb())
        return

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
            rows.append([btn("قفل: " + voice_label(sget("locked_gender", "f1")), "noop", "danger")])
        else:
            for vid in ENGINE:
                rows.append([btn(voice_label(vid), "pm:setv:%s:%s" % (vid, user.id), "primary")])
        rows.append([btn("🔙", "pm:home:%s" % user.id, "danger")])
        await q.edit_message_text("صدا / جنسیت:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("pm:setv:"):
        vid = data.split(":")[2]
        if sget("gender_lock", "0") == "1":
            await q.answer("قفل ادمین", show_alert=True)
            return
        if vid in ENGINE:
            with tx() as conn:
                conn.execute("UPDATE users SET gender=? WHERE id=?", (vid, user.id))
        await q.edit_message_text("✅ صدا: " + voice_label(vid), reply_markup=pm_kb(user.id))
        return

    if data.startswith("pm:speed:"):
        await q.edit_message_text("سرعت:", reply_markup=InlineKeyboardMarkup([
            [btn("🐢 آرام", "pm:sets:slow:%s" % user.id, "primary")],
            [btn("🚶 عادی", "pm:sets:normal:%s" % user.id, "success")],
            [btn("⚡ سریع", "pm:sets:fast:%s" % user.id, "danger")],
            [btn("🔙", "pm:home:%s" % user.id, "danger")],
        ]))
        return

    if data.startswith("pm:sets:"):
        sp = data.split(":")[2]
        with tx() as conn:
            conn.execute("UPDATE users SET speed=? WHERE id=?", (sp, user.id))
        await q.edit_message_text("✅ سرعت ذخیره شد", reply_markup=pm_kb(user.id))
        return

    if data.startswith("pm:status:"):
        uu = get_user(user.id)
        sl = {"slow": "آرام", "fast": "سریع"}.get(uu["speed"], "عادی")
        await q.edit_message_text(
            "💎 توکن: %s\n⭐ امتیاز: %s\n📶 سطح: %s\n🎙 ویس: %s\n🎭 %s\n⚡ %s" % (
                uu["tokens"], uu["points"], uu["level"], uu["voices_total"],
                voice_label(uu["gender"]), sl
            ),
            reply_markup=pm_kb(user.id),
        )
        return

    if data.startswith("pm:buy:"):
        price = sint("token_price_points", 10)
        uu = get_user(user.id)
        if int(uu["points"]) < price:
            await q.answer("امتیاز کم (نیاز %s)" % price, show_alert=True)
            return
        with tx() as conn:
            conn.execute("UPDATE users SET points=points-?, tokens=tokens+1 WHERE id=?", (price, user.id))
        uu = get_user(user.id)
        await q.edit_message_text("✅ ۱ توکن\n💎 %s | ⭐ %s" % (uu["tokens"], uu["points"]), reply_markup=pm_kb(user.id))
        return

    if data.startswith("pm:ref:"):
        me = await c.bot.get_me()
        await q.edit_message_text(
            "🔗 لینک:\n" + mono("https://t.me/%s?start=ref%s" % (me.username, user.id))
            + "\nپاداش: %s توکن" % sint("ref_tokens", 5),
            parse_mode="HTML",
            reply_markup=pm_kb(user.id),
        )
        return

    if data.startswith("pm:gift:"):
        set_st(c, "gift")
        await q.edit_message_text("کد هدیه را بفرست:")
        return

    if data.startswith("pm:help:"):
        await q.edit_message_text(default_help(), parse_mode="HTML", reply_markup=pm_kb(user.id))
        return

    if data.startswith("pm:home:"):
        await q.edit_message_text("منو:", reply_markup=pm_kb(user.id))
        return

    if data.startswith("a:") and not is_admin(user.id):
        await q.answer("دسترسی نداری", show_alert=True)
        return

    if data == "a:wel":
        cur = sget("welcome_enabled", "1") == "1"
        sset("welcome_enabled", "0" if cur else "1")
        now = sget("welcome_enabled", "1") == "1"
        await q.edit_message_text("👋 خوش‌آمد: " + ("🟢 روشن" if now else "🔴 خاموش"), reply_markup=admin_kb())
        return

    if data == "a:caption":
        set_st(c, "a_cap")
        await q.edit_message_text(
            "📝 کپشن ویس\nفعلی:\n%s\n\n%s\n\nقالب جدید را بفرست." % (sget("caption", ""), VARS_HELP),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[btn("🔙 پنل", "a:home", "danger")]]),
        )
        return

    if data == "a:vnames":
        set_st(c, "a_vname")
        await q.edit_message_text(
            "اسم صداها:\nf1=%s\nf2=%s\nm1=%s\nm2=%s\n\nبفرست: %s" % (
                voice_label("f1"), voice_label("f2"), voice_label("m1"), voice_label("m2"),
                mono("f1 نازنین"),
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[btn("🔙 پنل", "a:home", "danger")]]),
        )
        return

    if data == "a:help":
        set_st(c, "a_help")
        await q.edit_message_text(
            "متن راهنما را بفرست.\nفعلی:\n%s" % (sget("help_text") or "—"),
            reply_markup=InlineKeyboardMarkup([[btn("🔙 پنل", "a:home", "danger")]]),
        )
        return

    if data == "a:startw":
        set_st(c, "a_startw")
        await q.edit_message_text(
            "متن استارت را بفرست.\nفعلی:\n%s" % sget("default_welcome", ""),
            reply_markup=InlineKeyboardMarkup([[btn("🔙 پنل", "a:home", "danger")]]),
        )
        return

    if data == "a:weltxt":
        set_st(c, "a_weltxt")
        await q.edit_message_text(
            "متن خوش‌آمد ممبر:\n%s\n\nفعلی:\n%s" % (VARS_HELP, sget("member_welcome", "")),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[btn("🔙 پنل", "a:home", "danger")]]),
        )
        return

    if data == "a:welmedia":
        set_st(c, "a_welmedia")
        mt = sget("welcome_media_type") or "—"
        await q.edit_message_text(
            "🖼 یک عکس یا گیف برای خوش‌آمد بفرست.\nنوع فعلی: %s" % mt,
            reply_markup=InlineKeyboardMarkup([[btn("🔙 پنل", "a:home", "danger")]]),
        )
        return

    if data == "a:welclear":
        sset("welcome_media_type", "")
        sset("welcome_media_id", "")
        await q.edit_message_text("مدیا خوش‌آمد پاک شد.", reply_markup=admin_kb())
        return

    if data == "a:settings":
        set_st(c, "a_set")
        await q.edit_message_text(
            "تنظیمات (کلید مقدار):\n"
            + "\n".join(mono("%s %s" % (k, sget(k))) for k in (
                "daily_tokens", "token_cost", "max_chars", "spam_sec",
                "points_per_voice", "token_price_points", "ref_tokens",
                "gender_lock", "locked_gender",
            ))
            + "\n\nمثال: " + mono("spam_sec 8"),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[btn("🔙 پنل", "a:home", "danger")]]),
        )
        return

    if data == "a:groups":
        set_st(c, "a_grp")
        with tx() as conn:
            rows = conn.execute("SELECT * FROM groups").fetchall()
        lines = ["گپ‌های مجاز (خالی=همه)"] + [
            "%s %s | %s" % ("✅" if r["active"] else "❌", r["title"], r["chat_id"]) for r in rows
        ]
        await q.edit_message_text(
            "\n".join(lines) + "\n\nآیدی بفرست یا در گپ /addgroup",
            reply_markup=InlineKeyboardMarkup([[btn("🔙 پنل", "a:home", "danger")]]),
        )
        return

    if data == "a:gift":
        set_st(c, "a_gift")
        await q.edit_message_text(
            "فرمت:\n" + mono("کد مبلغ تعداد روز") + "\nگپ خاص:\n" + mono("کد مبلغ تعداد روز chat_id"),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[btn("🔙 پنل", "a:home", "danger")]]),
        )
        return

    if data == "a:block":
        set_st(c, "a_block")
        await q.edit_message_text("آیدی بلاک/آنبلاک:", reply_markup=InlineKeyboardMarkup([[btn("🔙 پنل", "a:home", "danger")]]))
        return

    if data == "a:addtok":
        set_st(c, "a_addtok")
        await q.edit_message_text("آیدی تعداد\n" + mono("123 20"), parse_mode="HTML",
                                 reply_markup=InlineKeyboardMarkup([[btn("🔙 پنل", "a:home", "danger")]]))
        return

    if data == "a:adm":
        set_st(c, "a_adm")
        await q.edit_message_text("ادمین + آیدی  یا  ادمین - آیدی",
                                 reply_markup=InlineKeyboardMarkup([[btn("🔙 پنل", "a:home", "danger")]]))
        return

    if data == "a:stats":
        with tx() as conn:
            uc = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
            vt = conn.execute("SELECT SUM(voices_total) s FROM users").fetchone()["s"] or 0
        await q.edit_message_text("کاربران: %s\nویس کل: %s" % (uc, vt), reply_markup=admin_kb())
        return


def resolve_reply_to(msg):
    """آیدی پیامی که باید ویس روی آن ریپلای شود."""
    if not msg:
        return None
    # ریپلای معمولی تلگرام
    rtm = getattr(msg, "reply_to_message", None)
    if rtm is not None and getattr(rtm, "message_id", None):
        return int(rtm.message_id)
    # ریپلای خارجی (نسخه‌های جدید API)
    ext = getattr(msg, "external_reply", None)
    if ext is not None and getattr(ext, "message_id", None):
        return int(ext.message_id)
    return None


async def do_voice(update: Update, context: ContextTypes.DEFAULT_TYPE, body: str):
    # همیشه از message اصلی استفاده کن نه effective_message جایگزین
    msg = update.message or update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user or not chat:
        return
    ensure_user(user)
    uu = get_user(user.id)
    if not uu or int(uu["blocked"] or 0):
        return
    if chat.type != ChatType.PRIVATE and not group_allowed(chat.id):
        return
    cost = sint("token_cost", 1)
    if int(uu["tokens"]) < cost:
        try:
            await msg.reply_text("توکن کافی نیست. پیوی ربات را باز کن.")
        except Exception:
            pass
        return
    spam = sint("spam_sec", 5)
    if spam > 0 and float(uu["last_voice"] or 0) + spam > time.time():
        left = int(float(uu["last_voice"]) + spam - time.time())
        try:
            await msg.reply_text("صبر کن %s ثانیه" % left)
        except Exception:
            pass
        return
    max_c = sint("max_chars", 400)
    if len(body) > max_c:
        body = body[:max_c]
    gender = uu["gender"] or "f1"
    if sget("gender_lock", "0") == "1":
        gender = sget("locked_gender", "f1")
    speed = uu["speed"] or "normal"

    # اگر روی کسی ریپلای شده → ویس روی همان پیام او
    # اگر نه → روی پیام خود فرستنده
    target_reply_id = resolve_reply_to(msg)
    if target_reply_id is None:
        target_reply_id = int(msg.message_id)

    log.info(
        "voice reply_to=%s has_reply=%s chat=%s from=%s",
        target_reply_id,
        bool(getattr(msg, "reply_to_message", None)),
        chat.id,
        user.id,
    )

    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    path = tmp.name
    tmp.close()
    try:
        await tts_save(body, gender, speed, path)
        with tx() as conn:
            conn.execute(
                "UPDATE users SET tokens=tokens-?, points=points+?, voices_total=voices_total+1, last_voice=?, level=? WHERE id=?",
                (cost, sint("points_per_voice", 2), time.time(), calc_level(int(uu["voices_total"]) + 1), user.id),
            )
        uu2 = get_user(user.id)
        sl = {"slow": "آرام", "fast": "سریع"}.get(speed, "عادی")
        # کپشن همچنان از طرف سازنده ویس (کسی که - زد)
        cap = render_tpl(
            sget("caption", "🎙 این ویس از طرف {mention}"),
            user, voice_label(gender), sl, uu2["tokens"], getattr(chat, "title", "") or "",
        )

        send_kw = dict(
            chat_id=chat.id,
            caption=cap,
            parse_mode="HTML",
            reply_to_message_id=target_reply_id,
            allow_sending_without_reply=True,
        )
        # تاپیک‌ها / فروم
        thread_id = getattr(msg, "message_thread_id", None)
        if thread_id:
            send_kw["message_thread_id"] = thread_id

        with open(path, "rb") as f:
            send_kw["voice"] = f
            try:
                sent = await context.bot.send_voice(**send_kw)
            except TypeError:
                # نسخه‌های قدیمی‌تر بدون allow_sending_without_reply
                send_kw.pop("allow_sending_without_reply", None)
                f.seek(0)
                sent = await context.bot.send_voice(**send_kw)
            except Exception as e:
                # اگر ریپلای روی پیام هدف خطا داد، یک‌بار بدون ریپلای هدف روی همان چت بفرست
                log.warning("send_voice reply failed: %s — retry", e)
                send_kw.pop("reply_to_message_id", None)
                send_kw.pop("allow_sending_without_reply", None)
                f.seek(0)
                sent = await context.bot.send_voice(**send_kw)

        try:
            await context.bot.set_message_reaction(chat.id, sent.message_id, reaction="🎙")
        except Exception:
            pass

        # حذف پیام -متن بعد از ارسال ویس (نیاز به ادمین بودن ربات)
        if chat.type != ChatType.PRIVATE and await bot_can_delete(context.bot, chat.id):
            try:
                await context.bot.delete_message(chat.id, msg.message_id)
            except Exception:
                pass
    except Exception as e:
        log.exception("voice")
        try:
            await msg.reply_text("⚠️ خطا: " + str(e)[:140])
        except Exception:
            pass
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


async def on_text(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.message.text:
        return
    user = u.effective_user
    chat = u.effective_chat
    text = u.message.text.strip()
    ensure_user(user)
    low = re.sub(r"^/", "", text).strip().lower()

    if low in ("راهنما", "help"):
        await u.message.reply_text(default_help(), parse_mode="HTML")
        return

    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        body = parse_voice_cmd(text)
        if body:
            await do_voice(u, c, body)
        return

    if chat.type != ChatType.PRIVATE:
        return

    if low in ("پنل", "admin") and is_admin(user.id):
        clear_st(c)
        await u.message.reply_text("🎛 پنل ادمین", reply_markup=admin_kb())
        return
    if low in ("منو", "menu"):
        await u.message.reply_text("منو:", reply_markup=pm_kb(user.id))
        return

    st = get_st(c)
    if st and st["kind"] == "gift":
        code = text.strip().upper()
        with tx() as conn:
            g = conn.execute("SELECT * FROM gift_codes WHERE code=? AND active=1", (code,)).fetchone()
            if not g or int(g["uses_left"]) < 1:
                await u.message.reply_text("کد نامعتبر")
                clear_st(c)
                return
            if g["expires"] and float(g["expires"]) < time.time():
                await u.message.reply_text("منقضی")
                clear_st(c)
                return
            if g["only_chat_id"]:
                await u.message.reply_text("این کد فقط در گپ خاص است.")
                clear_st(c)
                return
            if conn.execute("SELECT 1 FROM gift_uses WHERE code=? AND user_id=?", (code, user.id)).fetchone():
                await u.message.reply_text("قبلاً استفاده شده")
                clear_st(c)
                return
            conn.execute("UPDATE gift_codes SET uses_left=uses_left-1 WHERE code=?", (code,))
            conn.execute("INSERT INTO gift_uses(code,user_id) VALUES (?,?)", (code, user.id))
            conn.execute("UPDATE users SET tokens=tokens+? WHERE id=?", (int(g["amount"]), user.id))
        clear_st(c)
        await u.message.reply_text("✅ +%s توکن" % g["amount"])
        return

    if st and is_admin(user.id):
        kind = st["kind"]
        if kind == "a_cap":
            sset("caption", text)
            clear_st(c)
            await u.message.reply_text("✅ کپشن ذخیره شد", reply_markup=admin_kb())
            return
        if kind == "a_vname":
            parts = text.split(None, 1)
            if len(parts) == 2 and parts[0] in ENGINE:
                sset("voice_" + parts[0], parts[1][:32])
                clear_st(c)
                await u.message.reply_text("✅ %s = %s" % (parts[0], parts[1][:32]), reply_markup=admin_kb())
            else:
                await u.message.reply_text("فرمت: f1 اسم")
            return
        if kind == "a_help":
            sset("help_text", text)
            clear_st(c)
            await u.message.reply_text("✅ راهنما ذخیره شد", reply_markup=admin_kb())
            return
        if kind == "a_startw":
            sset("default_welcome", text)
            clear_st(c)
            await u.message.reply_text("✅ استارت ذخیره شد", reply_markup=admin_kb())
            return
        if kind == "a_weltxt":
            sset("member_welcome", text)
            clear_st(c)
            await u.message.reply_text("✅ متن خوش‌آمد ذخیره شد", reply_markup=admin_kb())
            return
        if kind == "a_welmedia":
            await u.message.reply_text("عکس یا گیف بفرست (نه متن).")
            return
        if kind == "a_set":
            m = re.match(r"(\w+)\s+(.+)", text)
            keys = (
                "daily_tokens", "token_cost", "max_chars", "spam_sec",
                "points_per_voice", "token_price_points", "ref_tokens",
                "gender_lock", "locked_gender",
            )
            if m and m.group(1) in keys:
                sset(m.group(1), m.group(2).strip())
                clear_st(c)
                await u.message.reply_text("✅ %s = %s" % (m.group(1), m.group(2).strip()), reply_markup=admin_kb())
            else:
                await u.message.reply_text("کلید نامعتبر")
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
                    conn.execute("INSERT INTO groups(chat_id,title,active) VALUES (?,?,1)", (cid, str(cid)))
                    await u.message.reply_text("اضافه شد", reply_markup=admin_kb())
            clear_st(c)
            return
        if kind == "a_gift":
            parts = text.split()
            if len(parts) >= 4:
                try:
                    code, amount, uses, days = parts[0].upper(), int(parts[1]), int(parts[2]), int(parts[3])
                    only = int(parts[4]) if len(parts) >= 5 else None
                except ValueError:
                    await u.message.reply_text("اعداد نامعتبر")
                    return
                with tx() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO gift_codes(code,amount,uses_left,expires,only_chat_id,active) VALUES (?,?,?,?,?,1)",
                        (code, amount, uses, time.time() + days * 86400, only),
                    )
                clear_st(c)
                await u.message.reply_text("کد %s ثبت شد" % code, reply_markup=admin_kb())
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
            await u.message.reply_text("تغییر کرد", reply_markup=admin_kb())
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

    body = parse_voice_cmd(text)
    if body:
        await do_voice(u, c, body)


async def on_media(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """عکس/گیف برای خوش‌آمد از ادمین."""
    if not u.message or not u.effective_user:
        return
    if not is_admin(u.effective_user.id):
        return
    if u.effective_chat.type != ChatType.PRIVATE:
        return
    st = get_st(c)
    if not st or st["kind"] != "a_welmedia":
        return
    msg = u.message
    if msg.photo:
        fid = msg.photo[-1].file_id
        sset("welcome_media_type", "photo")
        sset("welcome_media_id", fid)
        clear_st(c)
        await msg.reply_text("✅ عکس خوش‌آمد ذخیره شد", reply_markup=admin_kb())
        return
    if msg.animation:
        sset("welcome_media_type", "animation")
        sset("welcome_media_id", msg.animation.file_id)
        clear_st(c)
        await msg.reply_text("✅ گیف خوش‌آمد ذخیره شد", reply_markup=admin_kb())
        return
    if msg.document and (msg.document.mime_type or "").startswith("image/"):
        sset("welcome_media_type", "photo")
        sset("welcome_media_id", msg.document.file_id)
        clear_st(c)
        await msg.reply_text("✅ تصویر ذخیره شد", reply_markup=admin_kb())
        return
    await msg.reply_text("فقط عکس یا گیف بفرست.")


async def on_new_member(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if sget("welcome_enabled", "1") != "1":
        return
    chat = u.effective_chat
    if not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if not u.message or not u.message.new_chat_members:
        return
    tpl = sget("member_welcome", "سلام {mention} خوش آمدی")
    mtype = (sget("welcome_media_type") or "").strip()
    mid = (sget("welcome_media_id") or "").strip()
    for mem in u.message.new_chat_members:
        if mem.is_bot:
            continue
        ensure_user(mem)
        text = render_tpl(tpl, mem, chat_title=chat.title or "")
        try:
            if mtype == "photo" and mid:
                await c.bot.send_photo(chat.id, mid, caption=text, parse_mode="HTML")
            elif mtype == "animation" and mid:
                await c.bot.send_animation(chat.id, mid, caption=text, parse_mode="HTML")
            else:
                await c.bot.send_message(chat.id, text, parse_mode="HTML")
        except Exception:
            log.exception("welcome")


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
    await u.message.reply_text("گپ اضافه شد.")


async def open_admin_cmd(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.effective_user or not is_admin(u.effective_user.id):
        return
    if u.effective_chat.type != ChatType.PRIVATE:
        return
    clear_st(c)
    await u.message.reply_text("🎛 پنل ادمین", reply_markup=admin_kb())


async def post_init(app_):
    try:
        await app_.bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        log.warning("webhook: %s", e)
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

    async def safe_media(update, context):
        try:
            await on_media(update, context)
        except Exception:
            log.exception("media")

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("addgroup", cmd_addgroup))
    app.add_handler(CommandHandler("admin", open_admin_cmd))
    app.add_handler(CallbackQueryHandler(safe_cb))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_member))
    app.add_handler(MessageHandler(filters.PHOTO | filters.ANIMATION | filters.Document.IMAGE, safe_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, safe_text))
    log.info("voice bot up")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True, bootstrap_retries=10)


if __name__ == "__main__":
    main()
