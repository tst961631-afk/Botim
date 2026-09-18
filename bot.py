# -*- coding: utf-8 -*-
"""بات ویس کامل: چند زبان، فروشگاه، قمار، استیکر، خوش‌آمد/خداحافظی"""
from __future__ import annotations
import os, re, time, logging, sqlite3, threading, tempfile, random, io, json
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters,
)
from telegram.constants import ChatType, ParseMode
from telegram.request import HTTPXRequest

BOT_TOKEN = "8932340319:AAEEKFbUFWBo_3Bc3NSYy_r8QhrVvXBy1Uk"
ADMIN_ID = 7530457395
DB_PATH = "voice_bot.db"
TZ = timezone(timedelta(hours=3, minutes=30))

# voice_id -> (engine, default_label, lang)
ENGINE = {
    "fa_f": ("fa-IR-DilaraNeural", "فارسی زن", "fa"),
    "fa_m": ("fa-IR-FaridNeural", "فارسی مرد", "fa"),
    "en_f": ("en-US-JennyNeural", "English Female", "en"),
    "en_m": ("en-US-GuyNeural", "English Male", "en"),
    "tr_f": ("tr-TR-EmelNeural", "Türkçe Kadın", "tr"),
    "tr_m": ("tr-TR-AhmetNeural", "Türkçe Erkek", "tr"),
    "ru_f": ("ru-RU-SvetlanaNeural", "Русский Жен", "ru"),
    "ru_m": ("ru-RU-DmitryNeural", "Русский Муж", "ru"),
    "ja_f": ("ja-JP-NanamiNeural", "日本語 女性", "ja"),
    "ja_m": ("ja-JP-KeitaNeural", "日本語 男性", "ja"),
}

LANGS = {
    "fa": "🇮🇷 فارسی",
    "en": "🇬🇧 English",
    "tr": "🇹🇷 Türkçe",
    "ru": "🇷🇺 Русский",
    "ja": "🇯🇵 日本語",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("voicebot")
_lock = threading.RLock()
_sticker_job_started = False


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
            gender TEXT DEFAULT 'fa_f',
            speed TEXT DEFAULT 'normal',
            lang TEXT DEFAULT 'fa',
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
        CREATE TABLE IF NOT EXISTS shop (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            token_amount INTEGER,
            price_points INTEGER,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS gamble_opts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            multiplier REAL,
            win_chance REAL,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS stickers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT,
            chat_id INTEGER,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS human_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT,
            answer TEXT
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
            "locked_gender": "fa_f",
            "welcome_enabled": "1",
            "leave_enabled": "1",
            "caption": "🎙 این ویس از طرف {mention}",
            "caption_style": "none",
            "default_welcome": "سلام! از منو زبان و صدا را تنظیم کن.",
            "help_text": "",
            "member_welcome": "سلام {mention} خوش آمدی 👋",
            "member_leave": "خداحافظ {name} 👋",
            "welcome_media_type": "",
            "welcome_media_id": "",
            "leave_media_type": "",
            "leave_media_id": "",
            "token_emoji": "💎",
            "token_msg": "{emoji} موجودی توکن شما",
            "lang_fa": "1", "lang_en": "1", "lang_tr": "1", "lang_ru": "1", "lang_ja": "1",
            "sticker_interval": "0",
            "sticker_chat_id": "0",
            "human_enabled": "1",
        }
        for k, v in defaults.items():
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (k, v))
        for vid, (_, label, _) in ENGINE.items():
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", ("voice_" + vid, label))


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
    return sget("voice_" + vid, ENGINE.get(vid, ("", vid, ""))[1])


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


def calc_level(v):
    return max(1, int(v) // 50 + 1)


def mention_html(user):
    return '<a href="tg://user?id=%s">%s</a>' % (user.id, user.full_name or user.id)


def mono(s):
    return "<code>%s</code>" % str(s).replace("<", "").replace(">", "")


def apply_style(text, style):
    style = (style or "none").lower()
    if style == "bold":
        return "<b>%s</b>" % text
    if style == "italic":
        return "<i>%s</i>" % text
    if style == "mono":
        return "<code>%s</code>" % text
    if style == "spoiler":
        return "<tg-spoiler>%s</tg-spoiler>" % text
    if style == "quote":
        return "<blockquote>%s</blockquote>" % text
    if style == "underline":
        return "<u>%s</u>" % text
    if style == "strike":
        return "<s>%s</s>" % text
    # fake fonts via unicode
    if style == "fancy":
        return to_fancy(text)
    if style == "boldu":
        return to_bold_unicode(text)
    if style == "italicu":
        return to_italic_unicode(text)
    return text


def _map_chars(text, base_upper, base_lower):
    out = []
    for ch in text:
        if "A" <= ch <= "Z":
            out.append(chr(base_upper + ord(ch) - 65))
        elif "a" <= ch <= "z":
            out.append(chr(base_lower + ord(ch) - 97))
        else:
            out.append(ch)
    return "".join(out)


def to_bold_unicode(t):
    return _map_chars(t, 0x1D400, 0x1D41A)


def to_italic_unicode(t):
    return _map_chars(t, 0x1D434, 0x1D44E)


def to_fancy(t):
    return _map_chars(t, 0x1D4D0, 0x1D4EA)


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
        "{tokens}": str(tokens_left),
        "{emoji}": sget("token_emoji", "💎"),
        "{level}": "",
        "{points}": "",
    }
    for k, v in rep.items():
        t = t.replace(k, str(v))
    return apply_style(t, sget("caption_style", "none"))



def translate_text(text, target_lang):
    """ترجمه متن به زبان کاربر. fa نیاز به ترجمه ندارد."""
    target_lang = (target_lang or "fa").lower()
    if target_lang == "fa" or not text:
        return text
    try:
        from deep_translator import GoogleTranslator
        # deep-translator codes
        code = {"en": "en", "tr": "tr", "ru": "ru", "ja": "ja", "fa": "fa"}.get(target_lang, "en")
        out = GoogleTranslator(source="auto", target=code).translate(text)
        return out or text
    except Exception as e:
        log.warning("translate fail: %s", e)
        return text


def voice_for_lang(lang, current_gender):
    """صدای مناسب زبان؛ جنسیت را تا حد ممکن حفظ می‌کند."""
    lang = (lang or "fa").lower()
    male = str(current_gender or "").endswith("_m") or str(current_gender or "").startswith("m")
    suffix = "_m" if male else "_f"
    vid = lang + suffix
    if vid in ENGINE:
        return vid
    # fallback any voice of that lang
    for k in ENGINE:
        if k.startswith(lang + "_"):
            return k
    return current_gender if current_gender in ENGINE else "fa_f"


def rate_for_speed(speed):

    if speed == "fast":
        return "+25%"
    if speed == "slow":
        return "-20%"
    return "+0%"


def group_allowed(chat_id):
    with tx() as c:
        n = c.execute("SELECT COUNT(*) c FROM groups WHERE active=1").fetchone()["c"]
        if n == 0:
            return True
        return bool(c.execute("SELECT 1 FROM groups WHERE chat_id=? AND active=1", (int(chat_id),)).fetchone())


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


async def tts_save(text, voice_id, speed, path):
    eng = ENGINE.get(voice_id, ENGINE["fa_f"])[0]
    rate = rate_for_speed(speed)
    try:
        import edge_tts
        await edge_tts.Communicate(text, eng, rate=rate).save(path)
        return
    except Exception as e:
        log.warning("edge_tts: %s", e)
    try:
        from gtts import gTTS
        lang = ENGINE.get(voice_id, ENGINE["fa_f"])[2]
        if lang == "ja":
            lang = "ja"
        elif lang not in ("en", "tr", "ru", "fa", "ja"):
            lang = "en"
        gTTS(text=text, lang=("fa" if lang == "fa" else lang)).save(path)
    except Exception as e:
        raise RuntimeError("TTS failed: %s" % e)


def parse_voice_cmd(text: str):
    if not text:
        return None
    m = re.match(r"^-\s*س\s+(.*)$", text)
    if m:
        return m.group(1).strip() or None
    m = re.match(r"^-\s+(.*)$", text)
    if m:
        return m.group(1).strip() or None
    m = re.match(r"^-(.*)$", text)
    if m:
        body = m.group(1).strip()
        if body.startswith("س "):
            body = body[2:].strip()
        elif body == "س":
            return None
        return body or None
    return None


def enabled_langs():
    return [k for k in LANGS if sget("lang_" + k, "1") == "1"]


def pm_kb(uid):
    return InlineKeyboardMarkup([
        [btn("🌐 زبان", "pm:lang:%s" % uid, "primary"), btn("🎭 صدا", "pm:voice:%s" % uid, "primary")],
        [btn("⚡ سرعت", "pm:speed:%s" % uid, "primary"), btn("💎 توکن‌های من", "pm:tok:%s" % uid, "success")],
        [btn("🛒 فروشگاه", "pm:shop:%s" % uid, "success"), btn("🎰 قمار", "pm:gamble:%s" % uid, "danger")],
        [btn("🔗 دعوت", "pm:ref:%s" % uid, "primary"), btn("🎁 کد هدیه", "pm:gift:%s" % uid, "success")],
        [btn("📖 راهنما", "pm:help:%s" % uid, "primary")],
    ])


def admin_kb():
    return InlineKeyboardMarkup([
        [btn("⚙️ تنظیمات", "a:settings", "primary"), btn("🌐 زبان‌ها", "a:langs", "primary")],
        [btn("🛒 فروشگاه", "a:shop", "success"), btn("🎰 قمار", "a:gamble", "danger")],
        [btn("📢 گپ‌ها", "a:groups", "primary"), btn("🎁 کد هدیه", "a:gift", "success")],
        [btn("📝 کپشن", "a:caption", "primary"), btn("🎨 استایل کپشن", "a:cstyle", "primary")],
        [btn("🎭 اسم صدا", "a:vnames", "primary"), btn("💎 ایموجی توکن", "a:temoji", "success")],
        [btn("💬 قالب توکن", "a:tmsg", "primary"), btn("📦 واریز همگانی", "a:mass", "success")],
        [btn("👋 خوش‌آمد/لفت", "a:welmenu", "primary"), btn("🖼 مدیا خوش‌آمد", "a:welmedia", "primary")],
        [btn("🎫 استیکر تایمر", "a:stick", "primary"), btn("🗣 حرف انسانی", "a:human", "primary")],
        [btn("➕ واریز تکی", "a:addtok", "success"), btn("🚫 بلک‌لیست", "a:block", "danger")],
        [btn("👤 ادمین", "a:adm", "primary"), btn("📊 آمار", "a:stats", "primary")],
        [btn("❌ بستن", "a:close", "danger")],
    ])


# ── handlers ──
async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    try:
        user = u.effective_user
        if not u.message or not user:
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
                        conn.execute("UPDATE users SET tokens=tokens+? WHERE id=?", (sint("ref_tokens", 5), ref))
                try:
                    await c.bot.send_message(ref, "🎉 دعوت موفق! +%s توکن" % sint("ref_tokens", 5))
                except Exception:
                    pass
        if u.effective_chat.type != ChatType.PRIVATE:
            await u.message.reply_text("پیوی ربات را استارت کن.")
            return
        uu = get_user(user.id)
        await u.message.reply_text(
            sget("default_welcome", "سلام!")
            + "\n\n💎 %s | ⭐ %s | 📶 %s" % (uu["tokens"], uu["points"], uu["level"]),
            reply_markup=pm_kb(user.id),
        )
    except Exception:
        log.exception("start")


async def cmd_help(u, c):
    ensure_user(u.effective_user)
    h = (sget("help_text") or "").strip() or "در گپ: -متن\nپیوی: منو برای تنظیمات و فروشگاه و قمار"
    await u.message.reply_text(h)


async def do_voice(update, context, body):
    msg = update.message or update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user:
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
            await msg.reply_text("توکن کافی نیست.")
        except Exception:
            pass
        return
    spam = sint("spam_sec", 5)
    if spam > 0 and float(uu["last_voice"] or 0) + spam > time.time():
        try:
            await msg.reply_text("صبر کن %s ثانیه" % int(float(uu["last_voice"]) + spam - time.time()))
        except Exception:
            pass
        return
    body = body[: sint("max_chars", 400)]
    # بدون ترجمه — همان متن کاربر با صدای انتخابی خوانده می‌شود
    gender = uu["gender"] or "fa_f"
    if sget("gender_lock", "0") == "1":
        gender = sget("locked_gender", "fa_f")
    speed = uu["speed"] or "normal"
    target = getattr(msg, "reply_to_message", None)
    path = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
    try:
        await tts_save(body, gender, speed, path)
        with tx() as conn:
            conn.execute(
                "UPDATE users SET tokens=tokens-?, points=points+?, voices_total=voices_total+1, last_voice=?, level=? WHERE id=?",
                (cost, sint("points_per_voice", 2), time.time(), calc_level(int(uu["voices_total"]) + 1), user.id),
            )
        uu2 = get_user(user.id)
        sl = {"slow": "آرام", "fast": "سریع"}.get(speed, "عادی")
        cap = render_tpl(sget("caption", "{mention}"), user, voice_label(gender), sl, uu2["tokens"], getattr(chat, "title", "") or "")
        with open(path, "rb") as f:
            if target is not None:
                sent = await target.reply_voice(voice=f, caption=cap, parse_mode="HTML")
            else:
                sent = await msg.reply_voice(voice=f, caption=cap, parse_mode="HTML")
        try:
            await context.bot.set_message_reaction(chat.id, sent.message_id, reaction="🎙")
        except Exception:
            pass
        if chat.type != ChatType.PRIVATE and await bot_can_delete(context.bot, chat.id):
            try:
                await context.bot.delete_message(chat.id, msg.message_id)
            except Exception:
                pass
    except Exception as e:
        log.exception("voice")
        try:
            await msg.reply_text("⚠️ " + str(e)[:140])
        except Exception:
            pass
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


async def convert_to_photo(update, context, kind):
    msg = update.effective_message
    if not msg.reply_to_message:
        await msg.reply_text("روی استیکر یا گیف ریپلای کن.")
        return
    src = msg.reply_to_message
    file_id = None
    if kind == "sticker" and src.sticker:
        if src.sticker.is_animated or src.sticker.is_video:
            await msg.reply_text("استیکر متحرک پشتیبانی نمی‌شود.")
            return
        file_id = src.sticker.file_id
    elif kind == "gif":
        if src.animation:
            file_id = src.animation.file_id
        elif src.document and (src.document.mime_type or "").startswith("image/gif"):
            file_id = src.document.file_id
        else:
            await msg.reply_text("روی گیف ریپلای کن.")
            return
    else:
        await msg.reply_text("روی استیکر ریپلای کن.")
        return
    try:
        from PIL import Image
        tg_file = await context.bot.get_file(file_id)
        data = bytes(await tg_file.download_as_bytearray())
        im = Image.open(io.BytesIO(data))
        if getattr(im, "is_animated", False):
            im.seek(0)
        if im.mode in ("RGBA", "P"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            rgba = im.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        buf.seek(0)
        await msg.reply_photo(photo=InputFile(buf, filename="out.png"))
    except Exception as e:
        await msg.reply_text("خطا: " + str(e)[:120])


async def on_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    data = q.data or ""
    user = u.effective_user
    ensure_user(user)

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

    # user: tokens
    if data.startswith("pm:tok:"):
        uu = get_user(user.id)
        emoji = sget("token_emoji", "💎")
        text = (sget("token_msg", "{emoji} موجودی توکن شما")
                .replace("{emoji}", emoji)
                .replace("{tokens}", str(uu["tokens"]))
                .replace("{points}", str(uu["points"]))
                .replace("{level}", str(uu["level"])))
        await q.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [btn("%s %s" % (emoji, uu["tokens"]), "noop", "success")],
                [btn("🔙", "pm:home:%s" % user.id, "primary")],
            ]),
        )
        return

    if data.startswith("pm:lang:"):
        rows = []
        for code in enabled_langs():
            rows.append([btn(LANGS[code], "pm:setl:%s:%s" % (code, user.id), "primary")])
        rows.append([btn("🔙", "pm:home:%s" % user.id, "danger")])
        await q.edit_message_text("زبان منو:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("pm:setl:"):
        code = data.split(":")[2]
        if code in enabled_langs():
            with tx() as conn:
                conn.execute("UPDATE users SET lang=? WHERE id=?", (code, user.id))
        await q.edit_message_text("✅ زبان تنظیم شد", reply_markup=pm_kb(user.id))
        return

    if data.startswith("pm:voice:"):
        lock = sget("gender_lock", "0") == "1"
        rows = []
        if lock:
            rows.append([btn("قفل: " + voice_label(sget("locked_gender", "fa_f")), "noop", "danger")])
        else:
            for vid in ENGINE:
                rows.append([btn(voice_label(vid), "pm:setv:%s:%s" % (vid, user.id), "primary")])
        rows.append([btn("🔙", "pm:home:%s" % user.id, "danger")])
        await q.edit_message_text("صدا:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("pm:setv:"):
        vid = data.split(":")[2]
        if sget("gender_lock", "0") != "1" and vid in ENGINE:
            with tx() as conn:
                conn.execute("UPDATE users SET gender=? WHERE id=?", (vid, user.id))
        await q.edit_message_text("✅ " + voice_label(vid), reply_markup=pm_kb(user.id))
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
        with tx() as conn:
            conn.execute("UPDATE users SET speed=? WHERE id=?", (data.split(":")[2], user.id))
        await q.edit_message_text("✅ سرعت ذخیره شد", reply_markup=pm_kb(user.id))
        return

    if data.startswith("pm:shop:"):
        with tx() as conn:
            items = conn.execute("SELECT * FROM shop WHERE active=1 ORDER BY id").fetchall()
        if not items:
            await q.edit_message_text("فروشگاه خالی است.", reply_markup=InlineKeyboardMarkup([[btn("🔙", "pm:home:%s" % user.id, "danger")]]))
            return
        rows = []
        for it in items:
            rows.append([btn("%s — %s⭐ → %s💎" % (it["title"], it["price_points"], it["token_amount"]),
                             "pm:buy:%s:%s" % (it["id"], user.id), "success")])
        rows.append([btn("🔙", "pm:home:%s" % user.id, "danger")])
        uu = get_user(user.id)
        await q.edit_message_text("🛒 فروشگاه\nامتیاز شما: %s" % uu["points"], reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("pm:buy:"):
        iid = int(data.split(":")[2])
        with tx() as conn:
            it = conn.execute("SELECT * FROM shop WHERE id=? AND active=1", (iid,)).fetchone()
            if not it:
                await q.answer("نامعتبر", show_alert=True)
                return
            uu = conn.execute("SELECT points,tokens FROM users WHERE id=?", (user.id,)).fetchone()
            if int(uu["points"]) < int(it["price_points"]):
                await q.answer("امتیاز کم است", show_alert=True)
                return
            conn.execute("UPDATE users SET points=points-?, tokens=tokens+? WHERE id=?",
                         (int(it["price_points"]), int(it["token_amount"]), user.id))
        await q.answer("خرید شد", show_alert=True)
        await q.edit_message_text("✅ بسته خرید شد", reply_markup=pm_kb(user.id))
        return

    if data.startswith("pm:gamble:"):
        with tx() as conn:
            opts = conn.execute("SELECT * FROM gamble_opts WHERE active=1 ORDER BY id").fetchall()
        if not opts:
            await q.edit_message_text("قمار فعال نیست.", reply_markup=InlineKeyboardMarkup([[btn("🔙", "pm:home:%s" % user.id, "danger")]]))
            return
        rows = [[btn("%s | x%s | %s%%" % (o["title"], o["multiplier"], o["win_chance"]),
                     "pm:gopt:%s:%s" % (o["id"], user.id), "danger")] for o in opts]
        rows.append([btn("🔙", "pm:home:%s" % user.id, "primary")])
        uu = get_user(user.id)
        await q.edit_message_text("🎰 قمار توکن\nموجودی: %s\nیک ضریب را انتخاب کن سپس مبلغ را بفرست." % uu["tokens"],
                                  reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("pm:gopt:"):
        oid = int(data.split(":")[2])
        set_st(c, "gamble_bet", {"opt_id": oid})
        await q.edit_message_text("مبلغ شرط را به عدد بفرست:")
        return

    if data.startswith("pm:ref:"):
        me = await c.bot.get_me()
        await q.edit_message_text("لینک:\n" + mono("https://t.me/%s?start=ref%s" % (me.username, user.id)),
                                  parse_mode="HTML", reply_markup=pm_kb(user.id))
        return

    if data.startswith("pm:gift:"):
        set_st(c, "gift")
        await q.edit_message_text("کد هدیه را بفرست:")
        return

    if data.startswith("pm:help:"):
        h = (sget("help_text") or "").strip() or "در گپ یا پیوی: -متن"
        await q.edit_message_text(h, reply_markup=pm_kb(user.id))
        return

    if data.startswith("pm:home:"):
        await q.edit_message_text("منو:", reply_markup=pm_kb(user.id))
        return

    if data == "noop":
        return

    # admin
    if data.startswith("a:") and not is_admin(user.id):
        await q.answer("نه", show_alert=True)
        return

    if data == "a:settings":
        set_st(c, "a_set")
        keys = ("daily_tokens", "token_cost", "max_chars", "spam_sec", "points_per_voice", "ref_tokens", "gender_lock", "locked_gender")
        await q.edit_message_text("کلید مقدار بفرست\n" + "\n".join("%s=%s" % (k, sget(k)) for k in keys),
                                  reply_markup=InlineKeyboardMarkup([[btn("🔙", "a:home", "danger")]]))
        return

    if data == "a:langs":
        rows = []
        for code, label in LANGS.items():
            on = sget("lang_" + code, "1") == "1"
            rows.append([btn(("%s %s" % ("🟢" if on else "🔴", label)), "a:ltog:%s" % code, "success" if on else "danger")])
        rows.append([btn("🔙", "a:home", "primary")])
        await q.edit_message_text("قفل/آزاد زبان‌ها:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("a:ltog:"):
        code = data.split(":")[2]
        cur = sget("lang_" + code, "1")
        sset("lang_" + code, "0" if cur == "1" else "1")
        await q.edit_message_text("تغییر کرد", reply_markup=admin_kb())
        return

    if data == "a:shop":
        set_st(c, "a_shop")
        with tx() as conn:
            items = conn.execute("SELECT * FROM shop ORDER BY id").fetchall()
        lines = ["فروشگاه — برای افزودن بفرست:", mono("عنوان توکن امتیاز"), "\n"]
        for it in items:
            lines.append("#%s %s | %s💎 / %s⭐" % (it["id"], it["title"], it["token_amount"], it["price_points"]))
        lines.append("\nحذف: " + mono("del آیدی"))
        await q.edit_message_text("\n".join(lines), parse_mode="HTML",
                                  reply_markup=InlineKeyboardMarkup([[btn("🔙", "a:home", "danger")]]))
        return

    if data == "a:gamble":
        set_st(c, "a_gamble")
        with tx() as conn:
            opts = conn.execute("SELECT * FROM gamble_opts ORDER BY id").fetchall()
        lines = ["قمار — افزودن:", mono("عنوان ضریب شانس"), "مثال: " + mono("ریسک 2.5 40"), "\n"]
        for o in opts:
            lines.append("#%s %s x%s %s%%" % (o["id"], o["title"], o["multiplier"], o["win_chance"]))
        lines.append("\nحذف: " + mono("del آیدی"))
        await q.edit_message_text("\n".join(lines), parse_mode="HTML",
                                  reply_markup=InlineKeyboardMarkup([[btn("🔙", "a:home", "danger")]]))
        return

    if data == "a:mass":
        set_st(c, "a_mass")
        await q.edit_message_text("تعداد توکن همگانی را بفرست (عدد):",
                                  reply_markup=InlineKeyboardMarkup([[btn("🔙", "a:home", "danger")]]))
        return

    if data == "a:caption":
        set_st(c, "a_cap")
        await q.edit_message_text("کپشن جدید را بفرست.\nفعلی:\n" + sget("caption", ""),
                                  reply_markup=InlineKeyboardMarkup([[btn("🔙", "a:home", "danger")]]))
        return

    if data == "a:cstyle":
        styles = ["none", "bold", "italic", "mono", "spoiler", "quote", "underline", "strike", "fancy", "boldu", "italicu"]
        rows = [[btn(s, "a:cs:%s" % s, "primary")] for s in styles]
        rows.append([btn("🔙", "a:home", "danger")])
        await q.edit_message_text("استایل کپشن فعلی: %s" % sget("caption_style", "none"), reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("a:cs:"):
        sset("caption_style", data.split(":")[2])
        await q.edit_message_text("✅ استایل: " + sget("caption_style"), reply_markup=admin_kb())
        return

    if data == "a:temoji":
        set_st(c, "a_temoji")
        await q.edit_message_text("ایموجی توکن را بفرست. فعلی: " + sget("token_emoji", "💎"),
                                  reply_markup=InlineKeyboardMarkup([[btn("🔙", "a:home", "danger")]]))
        return

    if data == "a:tmsg":
        set_st(c, "a_tmsg")
        await q.edit_message_text("قالب پیام توکن ({emoji} {tokens} {points} {level})\nفعلی:\n" + sget("token_msg", ""),
                                  reply_markup=InlineKeyboardMarkup([[btn("🔙", "a:home", "danger")]]))
        return

    if data == "a:vnames":
        set_st(c, "a_vname")
        await q.edit_message_text("فرمت: fa_f اسم\n" + "\n".join("%s = %s" % (k, voice_label(k)) for k in ENGINE),
                                  reply_markup=InlineKeyboardMarkup([[btn("🔙", "a:home", "danger")]]))
        return

    if data == "a:welmenu":
        await q.edit_message_text("خوش‌آمد/لفت:", reply_markup=InlineKeyboardMarkup([
            [btn("خوش‌آمد " + ("🟢" if sget("welcome_enabled") == "1" else "🔴"), "a:wtog", "success")],
            [btn("لفت " + ("🟢" if sget("leave_enabled") == "1" else "🔴"), "a:ltog2", "success")],
            [btn("متن خوش‌آمد", "a:weltxt", "primary"), btn("متن لفت", "a:leavetxt", "primary")],
            [btn("مدیا لفت", "a:leavemedia", "primary")],
            [btn("🔙", "a:home", "danger")],
        ]))
        return

    if data == "a:wtog":
        sset("welcome_enabled", "0" if sget("welcome_enabled") == "1" else "1")
        await q.edit_message_text("OK", reply_markup=admin_kb())
        return
    if data == "a:ltog2":
        sset("leave_enabled", "0" if sget("leave_enabled") == "1" else "1")
        await q.edit_message_text("OK", reply_markup=admin_kb())
        return
    if data == "a:weltxt":
        set_st(c, "a_weltxt")
        await q.edit_message_text("متن خوش‌آمد را بفرست:\n" + VARS_HELP + "\n\nفعلی:\n" + sget("member_welcome", ""),
                                  parse_mode="HTML",
                                  reply_markup=InlineKeyboardMarkup([[btn("🔙", "a:home", "danger")]]))
        return
    if data == "a:leavetxt":
        set_st(c, "a_leavetxt")
        await q.edit_message_text("متن لفت را بفرست:\n" + VARS_HELP + "\n\nفعلی:\n" + sget("member_leave", ""),
                                  parse_mode="HTML",
                                  reply_markup=InlineKeyboardMarkup([[btn("🔙", "a:home", "danger")]]))
        return
    if data == "a:welmedia":
        set_st(c, "a_welmedia")
        await q.edit_message_text("عکس یا گیف خوش‌آمد را بفرست\n" + VARS_HELP, parse_mode="HTML",
                                  reply_markup=InlineKeyboardMarkup([[btn("🔙", "a:home", "danger")]]))
        return
    if data == "a:leavemedia":
        set_st(c, "a_leavemedia")
        await q.edit_message_text("عکس یا گیف لفت را بفرست", reply_markup=InlineKeyboardMarkup([[btn("🔙", "a:home", "danger")]]))
        return

    if data == "a:stick":
        set_st(c, "a_stick")
        await q.edit_message_text(
            "استیکر تایمر\nبازه: " + mono("10m") + " یا " + mono("1h") + " یا " + mono("0") + " خاموش\n"
            "chat_id گپ را هم می‌توانی بفرستی\n"
            "استیکر را فوروارد/بفرست تا ذخیره شود\nفعلی interval=%s chat=%s" % (sget("sticker_interval"), sget("sticker_chat_id")),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[btn("🔙", "a:home", "danger")]]),
        )
        return

    if data == "a:human":
        set_st(c, "a_human")
        on = sget("human_enabled", "1") == "1"
        await q.edit_message_text(
            "حرف انسانی: %s\nفرمت افزودن:\n%s\nحذف: del آیدی\nخاموش/روشن: on / off" % (
                "روشن" if on else "خاموش", mono("کلیدواژه|متن جواب")),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[btn("🔙", "a:home", "danger")]]),
        )
        return

    if data == "a:groups":
        set_st(c, "a_grp")
        await q.edit_message_text("آیدی گپ برای افزودن/حذف یا /addgroup در گپ",
                                  reply_markup=InlineKeyboardMarkup([[btn("🔙", "a:home", "danger")]]))
        return
    if data == "a:gift":
        set_st(c, "a_gift")
        await q.edit_message_text(mono("کد مبلغ تعداد روز"), parse_mode="HTML",
                                  reply_markup=InlineKeyboardMarkup([[btn("🔙", "a:home", "danger")]]))
        return
    if data == "a:addtok":
        set_st(c, "a_addtok")
        await q.edit_message_text(mono("آیدی تعداد"), parse_mode="HTML",
                                  reply_markup=InlineKeyboardMarkup([[btn("🔙", "a:home", "danger")]]))
        return
    if data == "a:block":
        set_st(c, "a_block")
        await q.edit_message_text("آیدی بلاک:", reply_markup=InlineKeyboardMarkup([[btn("🔙", "a:home", "danger")]]))
        return
    if data == "a:adm":
        set_st(c, "a_adm")
        await q.edit_message_text("ادمین + آیدی / ادمین - آیدی", reply_markup=InlineKeyboardMarkup([[btn("🔙", "a:home", "danger")]]))
        return
    if data == "a:stats":
        with tx() as conn:
            uc = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
            vt = conn.execute("SELECT COALESCE(SUM(voices_total),0) s FROM users").fetchone()["s"]
            tt = conn.execute("SELECT COALESCE(SUM(tokens),0) s FROM users").fetchone()["s"]
        await q.edit_message_text("کاربران: %s\nویس: %s\nتوکن کل: %s" % (uc, vt, tt), reply_markup=admin_kb())
        return


async def on_text(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.message.text:
        return
    user = u.effective_user
    chat = u.effective_chat
    text = u.message.text.strip()
    ensure_user(user)
    low = re.sub(r"^/", "", text).lower()

    # sticker/gif convert
    if low in ("استیکر به عکس", "sticker to photo"):
        await convert_to_photo(u, c, "sticker")
        return
    if low in ("گیف به عکس", "gif to photo"):
        await convert_to_photo(u, c, "gif")
        return

    if low in ("راهنما", "help"):
        await cmd_help(u, c)
        return

    # human talk: reply to bot
    if u.message.reply_to_message and u.message.reply_to_message.from_user and u.message.reply_to_message.from_user.is_bot:
        if sget("human_enabled", "1") == "1" and not text.startswith("-"):
            with tx() as conn:
                lines = conn.execute("SELECT * FROM human_lines").fetchall()
            tlow = text.lower()
            for ln in lines:
                kw = (ln["keyword"] or "").lower()
                if kw and kw in tlow:
                    await do_voice(u, c, ln["answer"])
                    return

    # groups / private voice
    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        if low in ("موجودی", "توکن", "balance", "tokens"):
            uu = get_user(user.id)
            cost = max(1, sint("token_cost", 1))
            can = int(uu["tokens"]) // cost
            emoji = sget("token_emoji", "💎")
            await u.message.reply_text(
                "%s موجودی: *%s*\n🎙 می‌توانی حدود *%s* ویس بسازی" % (emoji, uu["tokens"], can),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [btn("%s %s" % (emoji, uu["tokens"]), "noop", "success")],
                ]),
            )
            return
        if low in ("قمار", "gamble"):
            with tx() as conn:
                opts = conn.execute("SELECT * FROM gamble_opts WHERE active=1 ORDER BY id").fetchall()
            if not opts:
                await u.message.reply_text("قمار فعال نیست.")
                return
            rows = [[btn("%s | x%s | %s%%" % (o["title"], o["multiplier"], o["win_chance"]),
                         "pm:gopt:%s:%s" % (o["id"], user.id), "danger")] for o in opts]
            uu = get_user(user.id)
            await u.message.reply_text(
                "🎰 قمار — موجودی: %s\nضریب را بزن و مبلغ را بفرست." % uu["tokens"],
                reply_markup=InlineKeyboardMarkup(rows),
            )
            return
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

    # gamble amount
    if st and st["kind"] == "gamble_bet":
        try:
            amount = int(re.sub(r"\D", "", text) or "0")
        except Exception:
            amount = 0
        if amount < 1:
            await u.message.reply_text("مبلغ نامعتبر")
            return
        oid = int(st["extra"]["opt_id"])
        with tx() as conn:
            opt = conn.execute("SELECT * FROM gamble_opts WHERE id=? AND active=1", (oid,)).fetchone()
            uu = conn.execute("SELECT tokens FROM users WHERE id=?", (user.id,)).fetchone()
            if not opt:
                clear_st(c)
                await u.message.reply_text("گزینه نیست")
                return
            if int(uu["tokens"]) < amount:
                await u.message.reply_text("توکن کم است")
                return
            win = random.random() * 100 < float(opt["win_chance"])
            if win:
                gain = int(amount * float(opt["multiplier"])) - amount
                conn.execute("UPDATE users SET tokens=tokens+? WHERE id=?", (gain, user.id))
                res = "برد! +%s توکن (ضریب %s)" % (gain, opt["multiplier"])
            else:
                conn.execute("UPDATE users SET tokens=tokens-? WHERE id=?", (amount, user.id))
                res = "باخت! -%s توکن" % amount
            left = conn.execute("SELECT tokens FROM users WHERE id=?", (user.id,)).fetchone()["tokens"]
        clear_st(c)
        await u.message.reply_text("%s\nموجودی: %s" % (res, left), reply_markup=pm_kb(user.id))
        return

    if st and st["kind"] == "gift":
        code = text.strip().upper()
        with tx() as conn:
            g = conn.execute("SELECT * FROM gift_codes WHERE code=? AND active=1", (code,)).fetchone()
            if not g or int(g["uses_left"]) < 1:
                await u.message.reply_text("نامعتبر")
                clear_st(c)
                return
            if g["expires"] and float(g["expires"]) < time.time():
                await u.message.reply_text("منقضی")
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
        await u.message.reply_text("✅ +%s" % g["amount"])
        return

    if st and is_admin(user.id):
        kind = st["kind"]
        if kind == "a_set":
            m = re.match(r"(\w+)\s+(.+)", text)
            if m:
                sset(m.group(1), m.group(2).strip())
                clear_st(c)
                await u.message.reply_text("OK", reply_markup=admin_kb())
            return
        if kind == "a_shop":
            if text.lower().startswith("del "):
                with tx() as conn:
                    conn.execute("DELETE FROM shop WHERE id=?", (int(text.split()[1]),))
                clear_st(c)
                await u.message.reply_text("حذف شد", reply_markup=admin_kb())
                return
            parts = text.rsplit(None, 2)
            if len(parts) == 3:
                title, tok, price = parts[0], int(parts[1]), int(parts[2])
                with tx() as conn:
                    conn.execute("INSERT INTO shop(title,token_amount,price_points,active) VALUES (?,?,?,1)", (title, tok, price))
                clear_st(c)
                await u.message.reply_text("بسته اضافه شد", reply_markup=admin_kb())
            return
        if kind == "a_gamble":
            if text.lower().startswith("del "):
                with tx() as conn:
                    conn.execute("DELETE FROM gamble_opts WHERE id=?", (int(text.split()[1]),))
                clear_st(c)
                await u.message.reply_text("حذف", reply_markup=admin_kb())
                return
            parts = text.rsplit(None, 2)
            if len(parts) == 3:
                title, mul, chance = parts[0], float(parts[1]), float(parts[2])
                with tx() as conn:
                    conn.execute("INSERT INTO gamble_opts(title,multiplier,win_chance,active) VALUES (?,?,?,1)", (title, mul, chance))
                clear_st(c)
                await u.message.reply_text("ثبت شد", reply_markup=admin_kb())
            return
        if kind == "a_mass":
            try:
                amt = int(text.strip())
            except ValueError:
                await u.message.reply_text("عدد")
                return
            with tx() as conn:
                conn.execute("UPDATE users SET tokens=tokens+?", (amt,))
                n = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
            clear_st(c)
            await u.message.reply_text("به %s کاربر +%s توکن" % (n, amt), reply_markup=admin_kb())
            return
        if kind == "a_cap":
            sset("caption", text)
            clear_st(c)
            await u.message.reply_text("کپشن OK", reply_markup=admin_kb())
            return
        if kind == "a_temoji":
            sset("token_emoji", text.strip()[:8])
            clear_st(c)
            await u.message.reply_text("OK", reply_markup=admin_kb())
            return
        if kind == "a_tmsg":
            sset("token_msg", text)
            clear_st(c)
            await u.message.reply_text("OK", reply_markup=admin_kb())
            return
        if kind == "a_vname":
            parts = text.split(None, 1)
            if len(parts) == 2 and parts[0] in ENGINE:
                sset("voice_" + parts[0], parts[1][:40])
                clear_st(c)
                await u.message.reply_text("OK", reply_markup=admin_kb())
            return
        if kind == "a_weltxt":
            sset("member_welcome", text)
            clear_st(c)
            await u.message.reply_text("OK", reply_markup=admin_kb())
            return
        if kind == "a_leavetxt":
            sset("member_leave", text)
            clear_st(c)
            await u.message.reply_text("OK", reply_markup=admin_kb())
            return
        if kind == "a_stick":
            t = text.strip().lower()
            if re.match(r"^\d+[mh]$", t) or t == "0":
                sset("sticker_interval", t)
                clear_st(c)
                await u.message.reply_text("بازه: " + t, reply_markup=admin_kb())
                return
            try:
                cid = int(text.strip())
                sset("sticker_chat_id", str(cid))
                clear_st(c)
                await u.message.reply_text("chat_id ذخیره شد", reply_markup=admin_kb())
                return
            except ValueError:
                await u.message.reply_text("استیکر بفرست یا 10m / 1h / chat_id")
                return
        if kind == "a_human":
            if text.lower() in ("on", "off"):
                sset("human_enabled", "1" if text.lower() == "on" else "0")
                clear_st(c)
                await u.message.reply_text("OK", reply_markup=admin_kb())
                return
            if text.lower().startswith("del "):
                with tx() as conn:
                    conn.execute("DELETE FROM human_lines WHERE id=?", (int(text.split()[1]),))
                clear_st(c)
                await u.message.reply_text("حذف", reply_markup=admin_kb())
                return
            if "|" in text:
                kw, ans = text.split("|", 1)
                with tx() as conn:
                    conn.execute("INSERT INTO human_lines(keyword,answer) VALUES (?,?)", (kw.strip(), ans.strip()))
                clear_st(c)
                await u.message.reply_text("اضافه شد", reply_markup=admin_kb())
            return
        if kind == "a_grp":
            try:
                cid = int(text.strip())
            except ValueError:
                return
            with tx() as conn:
                if conn.execute("SELECT 1 FROM groups WHERE chat_id=?", (cid,)).fetchone():
                    conn.execute("DELETE FROM groups WHERE chat_id=?", (cid,))
                    await u.message.reply_text("حذف", reply_markup=admin_kb())
                else:
                    conn.execute("INSERT INTO groups(chat_id,title,active) VALUES (?,?,1)", (cid, str(cid)))
                    await u.message.reply_text("اضافه", reply_markup=admin_kb())
            clear_st(c)
            return
        if kind == "a_gift":
            parts = text.split()
            if len(parts) >= 4:
                code, amount, uses, days = parts[0].upper(), int(parts[1]), int(parts[2]), int(parts[3])
                only = int(parts[4]) if len(parts) >= 5 else None
                with tx() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO gift_codes(code,amount,uses_left,expires,only_chat_id,active) VALUES (?,?,?,?,?,1)",
                        (code, amount, uses, time.time() + days * 86400, only),
                    )
                clear_st(c)
                await u.message.reply_text("کد OK", reply_markup=admin_kb())
            return
        if kind == "a_addtok":
            parts = text.split()
            if len(parts) >= 2:
                tid, amt = int(parts[0]), int(parts[1])
                ensure_user(type("U", (), {"id": tid, "username": "", "full_name": str(tid), "is_bot": False})())
                with tx() as conn:
                    conn.execute("UPDATE users SET tokens=tokens+? WHERE id=?", (amt, tid))
                    new_bal = conn.execute("SELECT tokens FROM users WHERE id=?", (tid,)).fetchone()["tokens"]
                clear_st(c)
                await u.message.reply_text("OK", reply_markup=admin_kb())
                # پیام به کاربر
                try:
                    emoji = sget("token_emoji", "💎")
                    admin_mention = mention_html(user)
                    await c.bot.send_message(
                        tid,
                        "✅ %s توکن از طرف %s واریز شد" % (amt, admin_mention),
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([
                            [btn("%s %s" % (emoji, new_bal), "pm:tok:%s" % tid, "success")],
                        ]),
                    )
                except Exception:
                    pass
            return
        if kind == "a_block":
            tid = int(re.sub(r"\D", "", text) or "0")
            ensure_user(type("U", (), {"id": tid, "username": "", "full_name": str(tid), "is_bot": False})())
            with tx() as conn:
                cur = conn.execute("SELECT blocked FROM users WHERE id=?", (tid,)).fetchone()
                conn.execute("UPDATE users SET blocked=? WHERE id=?", (0 if cur and cur["blocked"] else 1, tid))
            clear_st(c)
            await u.message.reply_text("OK", reply_markup=admin_kb())
            return
        if kind == "a_adm":
            m = re.match(r"ادمین\s*([+-])\s*(\d+)", text)
            if m:
                with tx() as conn:
                    if m.group(1) == "+":
                        conn.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (int(m.group(2)),))
                    elif user.id == ADMIN_ID:
                        conn.execute("DELETE FROM admins WHERE user_id=?", (int(m.group(2)),))
                clear_st(c)
                await u.message.reply_text("OK", reply_markup=admin_kb())
            return

    body = parse_voice_cmd(text)
    if body:
        await do_voice(u, c, body)


async def on_media(u, c):
    if not u.message or not is_admin(u.effective_user.id):
        return
    if u.effective_chat.type != ChatType.PRIVATE:
        return
    st = get_st(c)
    if not st:
        return
    if st["kind"] in ("a_welmedia", "a_leavemedia"):
        prefix = "welcome" if st["kind"] == "a_welmedia" else "leave"
        if u.message.photo:
            sset(prefix + "_media_type", "photo")
            sset(prefix + "_media_id", u.message.photo[-1].file_id)
            clear_st(c)
            await u.message.reply_text("عکس ذخیره شد", reply_markup=admin_kb())
        elif u.message.animation:
            sset(prefix + "_media_type", "animation")
            sset(prefix + "_media_id", u.message.animation.file_id)
            clear_st(c)
            await u.message.reply_text("گیف ذخیره شد", reply_markup=admin_kb())
        return
    if st["kind"] == "a_stick" and u.message.sticker:
        with tx() as conn:
            conn.execute("INSERT INTO stickers(file_id,chat_id,active) VALUES (?,?,1)",
                         (u.message.sticker.file_id, sint("sticker_chat_id", 0)))
        await u.message.reply_text("استیکر ذخیره شد", reply_markup=admin_kb())
        clear_st(c)


async def on_new_member(u, c):
    if sget("welcome_enabled", "1") != "1":
        return
    chat = u.effective_chat
    if not u.message or not u.message.new_chat_members:
        return
    tpl = sget("member_welcome", "سلام {mention}")
    mtype, mid = sget("welcome_media_type") or "", sget("welcome_media_id") or ""
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
            pass


async def on_left_member(u, c):
    if sget("leave_enabled", "1") != "1":
        return
    if not u.message or not u.message.left_chat_member:
        return
    mem = u.message.left_chat_member
    if mem.is_bot:
        return
    text = render_tpl(sget("member_leave", "خداحافظ {name}"), mem, chat_title=u.effective_chat.title or "")
    mtype, mid = (sget("leave_media_type") or "").strip(), (sget("leave_media_id") or "").strip()
    try:
        if mtype == "photo" and mid:
            await c.bot.send_photo(u.effective_chat.id, mid, caption=text, parse_mode="HTML")
        elif mtype == "animation" and mid:
            await c.bot.send_animation(u.effective_chat.id, mid, caption=text, parse_mode="HTML")
        else:
            await c.bot.send_message(u.effective_chat.id, text, parse_mode="HTML")
    except Exception:
        pass


async def cmd_addgroup(u, c):
    if not is_admin(u.effective_user.id):
        return
    chat = u.effective_chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    with tx() as conn:
        conn.execute("INSERT OR REPLACE INTO groups(chat_id,title,active) VALUES (?,?,1)", (chat.id, chat.title or str(chat.id)))
    await u.message.reply_text("گپ اضافه شد")


async def open_admin(u, c):
    if not is_admin(u.effective_user.id) or u.effective_chat.type != ChatType.PRIVATE:
        return
    clear_st(c)
    await u.message.reply_text("🎛 پنل ادمین", reply_markup=admin_kb())


def parse_interval(s):
    s = (s or "0").strip().lower()
    if s == "0":
        return 0
    m = re.match(r"^(\d+)([mh])$", s)
    if not m:
        return 0
    n = int(m.group(1))
    return n * 60 if m.group(2) == "m" else n * 3600


async def sticker_loop(app):
    await asyncio_sleep_job(app)


async def asyncio_sleep_job(app):
    import asyncio
    while True:
        try:
            sec = parse_interval(sget("sticker_interval", "0"))
            chat_id = sint("sticker_chat_id", 0)
            if sec > 0 and chat_id:
                with tx() as conn:
                    rows = conn.execute("SELECT file_id FROM stickers WHERE active=1").fetchall()
                if rows:
                    fid = random.choice(rows)["file_id"]
                    try:
                        await app.bot.send_sticker(chat_id, fid)
                    except Exception:
                        log.exception("sticker send")
                await asyncio.sleep(sec)
            else:
                await asyncio.sleep(30)
        except Exception:
            log.exception("sticker loop")
            import asyncio
            await asyncio.sleep(30)


async def post_init(app):
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass
    me = await app.bot.get_me()
    log.info("bot @%s", me.username)
    import asyncio
    asyncio.create_task(asyncio_sleep_job(app))


def main():
    init_db()
    req = HTTPXRequest(connect_timeout=60.0, read_timeout=90.0, write_timeout=90.0, pool_timeout=60.0)
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(req)
        .get_updates_request(req)
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

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("admin", open_admin))
    app.add_handler(CommandHandler("addgroup", cmd_addgroup))
    app.add_handler(CallbackQueryHandler(safe_cb))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_member))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, on_left_member))
    app.add_handler(MessageHandler(filters.PHOTO | filters.ANIMATION | filters.Sticker.ALL, on_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, safe_text))
    log.info("up")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True, bootstrap_retries=10)


if __name__ == "__main__":
    main()
