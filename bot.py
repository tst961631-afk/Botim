# -*- coding: utf-8 -*-
"""قفل عضویت + رفرال + کیف + برداشت فقط بر اساس امتیاز"""
from __future__ import annotations
import re, time, logging, sqlite3, threading
from contextlib import contextmanager
from urllib.parse import urlparse

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters,
)
from telegram.constants import ChatType, ChatMemberStatus
from telegram.request import HTTPXRequest

BOT_TOKEN = "8975007734:AAEkghW4tK0DeG9uOKw87Lgep8XUWlMiiLY"
ADMIN_ID = 7530457395
DB_PATH = "lock_ref.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lockref")
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
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            name TEXT DEFAULT '',
            wallet INTEGER DEFAULT 0,
            ref_count INTEGER DEFAULT 0,
            referred_by INTEGER,
            joined_at REAL,
            last_active REAL,
            status TEXT DEFAULT 'active'
        );
        CREATE TABLE IF NOT EXISTS channels (
            chat_id INTEGER PRIMARY KEY,
            title TEXT,
            username TEXT DEFAULT '',
            invite_link TEXT DEFAULT '',
            button_label TEXT DEFAULT 'جوین بده',
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT
        );
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            card TEXT,
            amount INTEGER,
            status TEXT DEFAULT 'pending',
            created_at REAL,
            done_at REAL
        );
        CREATE TABLE IF NOT EXISTS referrals (
            referrer_id INTEGER,
            referred_id INTEGER PRIMARY KEY,
            amount INTEGER,
            confirmed INTEGER DEFAULT 0,
            created_at REAL
        );
        CREATE TABLE IF NOT EXISTS claim_options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            threshold INTEGER NOT NULL,
            label TEXT DEFAULT '',
            active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0
        );
        """)
        for sql in [
            "ALTER TABLE channels ADD COLUMN username TEXT DEFAULT ''",
            "ALTER TABLE channels ADD COLUMN invite_link TEXT DEFAULT ''",
            "ALTER TABLE channels ADD COLUMN button_label TEXT DEFAULT 'جوین بده'",
        ]:
            try:
                c.execute(sql)
            except Exception:
                pass
        c.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (ADMIN_ID,))
        for k, v in {"lock_enabled": "1", "ref_reward": "100000", "card_len": "12"}.items():
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (k, v))
        if c.execute("SELECT COUNT(*) c FROM claim_options").fetchone()["c"] == 0:
            for i, th in enumerate([100000, 300000, 600000, 1000000]):
                c.execute(
                    "INSERT INTO claim_options(threshold,label,active,sort_order) VALUES (?,?,1,?)",
                    (th, f"{th // 1000}کا", i),
                )


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


def num(n):
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


def parse_amt(text):
    t = (text or "").strip().replace(",", "").replace(" ", "").replace("،", "")
    t = t.replace("کا", "k").replace("ک", "k").replace("م", "m")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kmKMkaکا]*)?", t, re.I)
    if not m:
        return None
    v = float(m.group(1))
    s = (m.group(2) or "").lower().replace("کا", "k").replace("ک", "k")
    if s.startswith("k"):
        v *= 1000
    elif s.startswith("m") or "م" in s:
        v *= 1_000_000
    return int(v)


def btn(text, data, style=None):
    kw = {"text": str(text)[:64], "callback_data": str(data)[:64]}
    if style in ("success", "danger", "primary"):
        kw["style"] = style
    try:
        return InlineKeyboardButton(**kw)
    except TypeError:
        kw.pop("style", None)
        return InlineKeyboardButton(**kw)


def back_admin():
    return InlineKeyboardMarkup([[btn("🔙 بازگشت پنل ادمین", "a:home", "primary")]])


def back_main(uid):
    return InlineKeyboardMarkup([[btn("🔙 منو", f"m:home:{uid}", "primary")]])


def ensure_user(user):
    if not user or getattr(user, "is_bot", False):
        return
    now = time.time()
    with tx() as c:
        r = c.execute("SELECT id FROM users WHERE id=?", (user.id,)).fetchone()
        if not r:
            c.execute(
                "INSERT INTO users(id,username,name,joined_at,last_active) VALUES (?,?,?,?,?)",
                (user.id, user.username or "", user.full_name or str(user.id), now, now),
            )
        else:
            c.execute(
                "UPDATE users SET username=?, name=?, last_active=? WHERE id=?",
                (user.username or "", user.full_name or str(user.id), now, user.id),
            )


def get_user(uid):
    with tx() as c:
        return c.execute("SELECT * FROM users WHERE id=?", (int(uid),)).fetchone()


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


def lock_on():
    return sget("lock_enabled", "1") == "1"


def active_channels():
    with tx() as c:
        return c.execute("SELECT * FROM channels WHERE active=1").fetchall()


async def not_joined(bot, user_id):
    missing = []
    for ch in active_channels():
        try:
            m = await bot.get_chat_member(int(ch["chat_id"]), int(user_id))
            st = m.status
            if st in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED, "left", "kicked"):
                missing.append(ch)
            elif st == ChatMemberStatus.RESTRICTED and not getattr(m, "is_member", True):
                missing.append(ch)
        except Exception:
            missing.append(ch)
    return missing


def channel_url(ch):
    if ch["invite_link"]:
        return ch["invite_link"]
    if ch["username"]:
        return "https://t.me/" + str(ch["username"]).lstrip("@")
    return None


async def send_lock(update, context):
    missing = await not_joined(context.bot, update.effective_user.id)
    rows = []
    for ch in missing:
        label = ch["button_label"] or "جوین بده"
        title = ch["title"] or "کانال"
        url = channel_url(ch)
        if url:
            rows.append([InlineKeyboardButton("📢 " + label, url=url)])
        else:
            rows.append([btn("📢 " + title, "noop")])
    rows.append([btn("✅ عضو شدم — بررسی", "check_join", "success")])
    text = (
        "🔒 برای استفاده باید عضو کانال/گپ‌های زیر باشی:\n\n"
        + "\n".join("• " + str(ch["title"] or ch["chat_id"]) for ch in missing)
        + "\n\nبعد از عضویت دکمه بررسی را بزن."
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
    elif update.effective_message:
        await update.effective_message.reply_text(text, reply_markup=InlineKeyboardMarkup(rows))


async def require_join(update, context) -> bool:
    if not lock_on():
        return True
    if not await not_joined(context.bot, update.effective_user.id):
        return True
    await send_lock(update, context)
    return False


def main_kb(uid):
    return InlineKeyboardMarkup([
        [btn("💰 موجودی", f"m:bal:{uid}", "primary"), btn("🔗 لینک دعوت", f"m:link:{uid}", "success")],
        [btn("👥 رفرال‌های من", f"m:refs:{uid}", "primary")],
        [btn("💸 برداشت میوپوینت", f"m:claim:{uid}", "success")],
        [btn("📖 راهنما", f"m:help:{uid}", "primary")],
    ])


def admin_kb():
    lock = "🟢 قفل روشن" if lock_on() else "🔴 قفل خاموش"
    return InlineKeyboardMarkup([
        [btn(lock, "a:toggle_lock", "primary")],
        [btn("📢 مدیریت چنل‌ها", "a:ch", "primary")],
        [btn("💵 پاداش رفرال", "a:reward", "primary")],
        [btn("⚙️ گزینه‌های برداشت", "a:opt", "primary")],
        [btn("📥 صندوق برداشت", "a:claims", "success")],
        [btn("➕ واریز", "a:add", "success"), btn("➖ کسر", "a:sub", "danger")],
        [btn("👤 ادمین‌ها", "a:admins", "primary"), btn("📊 آمار", "a:stats", "primary")],
        [btn("❌ بستن", "a:close", "danger")],
    ])


async def try_confirm_referral(bot, user):
    with tx() as c:
        row = c.execute(
            "SELECT * FROM referrals WHERE referred_id=? AND confirmed=0", (user.id,)
        ).fetchone()
    if not row:
        return
    if lock_on() and await not_joined(bot, user.id):
        return
    reward = int(row["amount"] or sint("ref_reward", 100000))
    referrer = int(row["referrer_id"])
    with tx() as c:
        c.execute("UPDATE referrals SET confirmed=1, amount=? WHERE referred_id=?", (reward, user.id))
        c.execute(
            "UPDATE users SET wallet=wallet+?, ref_count=ref_count+1 WHERE id=?",
            (reward, referrer),
        )
    uname = ("@" + user.username) if user.username else user.full_name
    try:
        await bot.send_message(
            referrer,
            "🎉 یک نفر با لینک تو آمد!\n👤 " + uname + "\n💰 +" + num(reward) + " میوپوینت",
        )
    except Exception:
        pass


async def resolve_chat_input(bot, text, message=None):
    """لینک عمومی/خصوصی یا @یوزرنیم → chat. None اگر نامعتبر."""
    text = (text or "").strip()
    if message is not None:
        fwd = getattr(message, "forward_from_chat", None)
        if fwd is not None:
            return fwd
        origin = getattr(message, "forward_origin", None)
        if origin is not None:
            chat = getattr(origin, "chat", None)
            if chat is not None:
                return chat
    m = re.match(r"^@?([A-Za-z][A-Za-z0-9_]{3,})$", text)
    if m:
        try:
            return await bot.get_chat("@" + m.group(1))
        except Exception:
            return None
    if "t.me/" in text or "telegram.me/" in text:
        link = text if text.startswith("http") else ("https://" + text.lstrip("/"))
        try:
            path = urlparse(link).path.strip("/")
        except Exception:
            return None
        if path.startswith("+") or path.lower().startswith("joinchat/"):
            try:
                return await bot.get_chat(link)
            except Exception:
                return None
        uname = path.split("/")[0] if path else ""
        if uname:
            try:
                return await bot.get_chat("@" + uname)
            except Exception:
                return None
    try:
        cid = int(text)
        return await bot.get_chat(cid)
    except Exception:
        return None


async def bot_is_admin_in(bot, chat_id):
    try:
        me = await bot.get_me()
        m = await bot.get_chat_member(chat_id, me.id)
        return m.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
            "administrator",
            "creator",
        )
    except Exception:
        return False


async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    user = u.effective_user
    ensure_user(user)
    text = u.message.text or ""
    m = re.search(r"ref[_-]?(\d+)", text)
    if m:
        ref_id = int(m.group(1))
        if ref_id != user.id:
            with tx() as conn:
                uu = conn.execute("SELECT referred_by FROM users WHERE id=?", (user.id,)).fetchone()
                if uu and not uu["referred_by"]:
                    if not conn.execute(
                        "SELECT 1 FROM referrals WHERE referred_id=?", (user.id,)
                    ).fetchone():
                        conn.execute(
                            "INSERT INTO referrals(referrer_id,referred_id,amount,confirmed,created_at) VALUES (?,?,?,0,?)",
                            (ref_id, user.id, sint("ref_reward", 100000), time.time()),
                        )
                        conn.execute("UPDATE users SET referred_by=? WHERE id=?", (ref_id, user.id))
    if not await require_join(u, c):
        return
    await try_confirm_referral(c.bot, user)
    await u.message.reply_text(
        "سلام <b>" + user.full_name + "</b> 👋\nاز منو استفاده کن.",
        parse_mode="HTML",
        reply_markup=main_kb(user.id),
    )


async def open_admin(u: Update, c: ContextTypes.DEFAULT_TYPE):
    clear_st(c)
    user = u.effective_user
    if not is_admin(user.id):
        if u.message:
            await u.message.reply_text("دسترسی نداری.")
        return
    if u.effective_chat.type != ChatType.PRIVATE:
        if u.message:
            await u.message.reply_text("پنل فقط در پیوی ربات.")
        return
    text = "🎛 پنل ادمین"
    kb = admin_kb()
    if u.callback_query:
        await u.callback_query.edit_message_text(text, reply_markup=kb)
    else:
        await u.message.reply_text(text, reply_markup=kb)


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
        await q.answer()
        clear_st(c)
        await q.edit_message_text("🎛 پنل ادمین", reply_markup=admin_kb())
        return

    if data == "check_join":
        await q.answer()
        if lock_on() and await not_joined(c.bot, user.id):
            await q.answer("هنوز همه را جوین نکردی", show_alert=True)
            await send_lock(u, c)
            return
        await try_confirm_referral(c.bot, user)
        await q.edit_message_text("✅ عضویت تأیید شد.", reply_markup=main_kb(user.id))
        return

    if data.startswith("m:"):
        try:
            owner = int(data.split(":")[-1])
            if owner != user.id:
                await q.answer("این پنل برای تو نیست", show_alert=True)
                return
        except Exception:
            pass
        if lock_on() and await not_joined(c.bot, user.id):
            await q.answer()
            await send_lock(u, c)
            return

    await q.answer()

    if data.startswith("m:home:"):
        await q.edit_message_text("منو:", reply_markup=main_kb(user.id))
        return
    if data.startswith("m:help:"):
        await q.edit_message_text(
            "🔗 لینک دعوت بفرست → بعد از جوین دوستت امتیاز می‌گیری.\n"
            "💸 برداشت فقط با رسیدن به سقف امتیاز کیف پول.\n"
            "🔒 اگر قفل روشن باشد تا عضو نشوی کار نمی‌کند.",
            reply_markup=main_kb(user.id),
        )
        return
    if data.startswith("m:bal:"):
        uu = get_user(user.id)
        await q.edit_message_text(
            "💰 موجودی کیف: <b>" + num(uu["wallet"]) + "</b>\n"
            "👥 رفرال تأییدشده: <b>" + str(uu["ref_count"]) + "</b>",
            parse_mode="HTML",
            reply_markup=main_kb(user.id),
        )
        return
    if data.startswith("m:link:"):
        me = await c.bot.get_me()
        link = "https://t.me/" + me.username + "?start=ref" + str(user.id)
        await q.edit_message_text(
            "🔗 لینک دعوت:\n<code>" + link + "</code>\n\n"
            "هر دعوت تأییدشده: <b>" + num(sint("ref_reward", 100000)) + "</b>",
            parse_mode="HTML",
            reply_markup=main_kb(user.id),
        )
        return
    if data.startswith("m:refs:"):
        with tx() as conn:
            rows = conn.execute(
                "SELECT referred_id,amount,confirmed FROM referrals WHERE referrer_id=? ORDER BY created_at DESC LIMIT 30",
                (user.id,),
            ).fetchall()
        lines = ["👥 رفرال‌ها (" + str(get_user(user.id)["ref_count"]) + ")\n"]
        for r in rows:
            lines.append(
                ("✅" if r["confirmed"] else "⏳")
                + " <code>" + str(r["referred_id"]) + "</code> "
                + num(r["amount"])
            )
        if len(lines) == 1:
            lines.append("خالی")
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=main_kb(user.id))
        return
    if data.startswith("m:claim:"):
        with tx() as conn:
            if conn.execute(
                "SELECT 1 FROM claims WHERE user_id=? AND status='pending'", (user.id,)
            ).fetchone():
                await q.edit_message_text("یک درخواست در انتظار ادمین داری.", reply_markup=main_kb(user.id))
                return
            opts = conn.execute(
                "SELECT * FROM claim_options WHERE active=1 ORDER BY sort_order,id"
            ).fetchall()
        if not opts:
            await q.edit_message_text("گزینه برداشتی ثبت نشده.", reply_markup=main_kb(user.id))
            return
        uu = get_user(user.id)
        rows = [
            [btn(o["label"] or num(o["threshold"]), f"m:opt:{o['id']}:{user.id}", "success")]
            for o in opts
        ]
        rows.append([btn("🔙", f"m:home:{user.id}", "primary")])
        await q.edit_message_text(
            "💸 <b>برداشت میوپوینت</b>\nموجودی: <b>" + num(uu["wallet"]) + "</b>\nیک سقف را انتخاب کن:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return
    if data.startswith("m:opt:"):
        parts = data.split(":")
        oid, owner = int(parts[2]), int(parts[3])
        if user.id != owner:
            return
        with tx() as conn:
            opt = conn.execute("SELECT * FROM claim_options WHERE id=? AND active=1", (oid,)).fetchone()
        if not opt:
            await q.edit_message_text("گزینه نامعتبر.", reply_markup=back_main(user.id))
            return
        uu = get_user(user.id)
        need = int(opt["threshold"])
        have = int(uu["wallet"] or 0)
        if have < need:
            await q.edit_message_text(
                "❌ امتیاز شما کافی نیست\n\n"
                "📌 " + str(opt["label"]) + "\n"
                "💰 موجودی: <b>" + num(have) + "</b>\n"
                "🎯 نیاز: <b>" + num(need) + "</b>\n"
                "📉 کمبود: <b>" + num(need - have) + "</b>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [btn("🔙 گزینه‌ها", f"m:claim:{user.id}", "primary")],
                    [btn("🏠 منو", f"m:home:{user.id}", "primary")],
                ]),
            )
            return
        set_st(c, "card", {"opt_id": oid, "amount": need})
        await q.edit_message_text(
            "✅ قابل برداشت: <b>" + num(need) + "</b>\n\n"
            "شماره کارت میویی را بفرست (۱۲ رقم)\n"
            "مثال: <code>109658451189</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[btn("🔙 انصراف", f"m:claim:{user.id}", "danger")]]),
        )
        return

    if data.startswith("a:") and not is_admin(user.id):
        await q.answer("دسترسی نداری", show_alert=True)
        return

    if data == "a:toggle_lock":
        sset("lock_enabled", "0" if lock_on() else "1")
        await q.edit_message_text("قفل به‌روز شد.", reply_markup=admin_kb())
        return

    if data == "a:ch":
        with tx() as conn:
            rows = conn.execute("SELECT * FROM channels").fetchall()
        lines = ["📢 چنل‌های قفل\n"]
        kb = []
        for r in rows:
            st = "✅" if r["active"] else "❌"
            lines.append(st + " " + str(r["title"]) + " | دکمه: " + str(r["button_label"]))
            kb.append([
                btn("خاموش" if r["active"] else "روشن", f"a:ch_tog:{r['chat_id']}", "primary"),
                btn("حذف", f"a:ch_del:{r['chat_id']}", "danger"),
            ])
        kb.append([btn("➕ افزودن چنل", "a:ch_add", "success")])
        kb.append([btn("🔙", "a:home", "primary")])
        await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))
        return

    if data == "a:ch_add":
        set_st(c, "a_ch_link")
        await q.edit_message_text(
            "لینک عمومی یا خصوصی چنل/گپ را بفرست\n"
            "مثال:\n<code>https://t.me/mychannel</code>\n"
            "<code>https://t.me/+xxxx</code>\n<code>@channel</code>\n\n"
            "ربات باید ادمین آن چنل باشد.",
            parse_mode="HTML",
            reply_markup=back_admin(),
        )
        return

    if data.startswith("a:ch_tog:"):
        cid = int(data.split(":")[2])
        with tx() as conn:
            cur = conn.execute("SELECT active FROM channels WHERE chat_id=?", (cid,)).fetchone()
            if cur:
                conn.execute(
                    "UPDATE channels SET active=? WHERE chat_id=?",
                    (0 if cur["active"] else 1, cid),
                )
        await q.edit_message_text("عوض شد.", reply_markup=admin_kb())
        return
    if data.startswith("a:ch_del:"):
        cid = int(data.split(":")[2])
        with tx() as conn:
            conn.execute("DELETE FROM channels WHERE chat_id=?", (cid,))
        await q.edit_message_text("حذف شد.", reply_markup=admin_kb())
        return

    if data == "a:reward":
        set_st(c, "a_reward")
        await q.edit_message_text(
            "پاداش فعلی: <b>" + num(sint("ref_reward", 100000)) + "</b>\nمقدار جدید (مثل 100k):",
            parse_mode="HTML",
            reply_markup=back_admin(),
        )
        return

    if data == "a:opt":
        with tx() as conn:
            opts = conn.execute("SELECT * FROM claim_options ORDER BY sort_order,id").fetchall()
        lines = ["⚙️ گزینه‌های برداشت (فقط امتیاز کیف)\n"]
        kb = []
        for o in opts:
            st = "✅" if o["active"] else "❌"
            lines.append(st + " " + str(o["label"]) + " — نیاز " + num(o["threshold"]))
            kb.append([
                btn("خاموش" if o["active"] else "روشن", f"a:opt_tog:{o['id']}", "primary"),
                btn("حذف", f"a:opt_del:{o['id']}", "danger"),
            ])
        kb.append([btn("➕ افزودن گزینه", "a:opt_add", "success")])
        kb.append([btn("🔙", "a:home", "primary")])
        await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))
        return

    if data == "a:opt_add":
        set_st(c, "a_opt_label")
        await q.edit_message_text(
            "نام دکمه را بفرست (مثلاً: <code>۶۰۰کا</code>)",
            parse_mode="HTML",
            reply_markup=back_admin(),
        )
        return
    if data.startswith("a:opt_tog:"):
        oid = int(data.split(":")[2])
        with tx() as conn:
            cur = conn.execute("SELECT active FROM claim_options WHERE id=?", (oid,)).fetchone()
            if cur:
                conn.execute(
                    "UPDATE claim_options SET active=? WHERE id=?",
                    (0 if cur["active"] else 1, oid),
                )
        await q.edit_message_text("عوض شد.", reply_markup=admin_kb())
        return
    if data.startswith("a:opt_del:"):
        oid = int(data.split(":")[2])
        with tx() as conn:
            conn.execute("DELETE FROM claim_options WHERE id=?", (oid,))
        await q.edit_message_text("حذف شد.", reply_markup=admin_kb())
        return

    if data == "a:claims":
        with tx() as conn:
            rows = conn.execute(
                "SELECT * FROM claims WHERE status='pending' ORDER BY id ASC LIMIT 25"
            ).fetchall()
        if not rows:
            await q.edit_message_text("صندوق خالی است.", reply_markup=admin_kb())
            return
        lines = ["📥 صندوق برداشت\n"]
        kb = []
        for r in rows:
            ur = get_user(r["user_id"])
            uname = ("@" + ur["username"]) if ur and ur["username"] else "—"
            name = ur["name"] if ur else "—"
            lines.append(
                "#" + str(r["id"]) + " " + name + " (" + uname + ")\n"
                "کارت: <code>" + str(r["card"]) + "</code>\n"
                "مبلغ: " + num(r["amount"]) + "\n"
            )
            kb.append([btn("✅ انجام شد #" + str(r["id"]), f"a:done:{r['id']}", "success")])
        kb.append([btn("🔙", "a:home", "primary")])
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("a:done:"):
        cid = int(data.split(":")[2])
        with tx() as conn:
            cl = conn.execute(
                "SELECT * FROM claims WHERE id=? AND status='pending'", (cid,)
            ).fetchone()
            if not cl:
                await q.answer("نیست", show_alert=True)
                return
            conn.execute(
                "UPDATE claims SET status='done', done_at=? WHERE id=?",
                (time.time(), cid),
            )
        try:
            await c.bot.send_message(
                int(cl["user_id"]),
                "✅ برداشتت انجام شد.\nمبلغ: " + num(cl["amount"]),
            )
        except Exception:
            pass
        await q.edit_message_text("#" + str(cid) + " انجام شد.", reply_markup=admin_kb())
        return

    if data == "a:add":
        set_st(c, "a_add")
        await q.edit_message_text(
            "آیدی مبلغ:\n<code>123 50k</code>",
            parse_mode="HTML",
            reply_markup=back_admin(),
        )
        return
    if data == "a:sub":
        set_st(c, "a_sub")
        await q.edit_message_text("آیدی مبلغ کسر:", reply_markup=back_admin())
        return

    if data == "a:admins":
        with tx() as conn:
            ads = conn.execute("SELECT user_id FROM admins").fetchall()
        lines = ["👤 ادمین‌ها\n"] + ["• <code>" + str(a["user_id"]) + "</code>" for a in ads]
        await q.edit_message_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [btn("➕ افزودن ادمین", "a:adm_add", "success")],
                [btn("➖ حذف ادمین", "a:adm_del", "danger")],
                [btn("🔙", "a:home", "primary")],
            ]),
        )
        return
    if data == "a:adm_add":
        set_st(c, "a_adm_add")
        await q.edit_message_text("آیدی عددی ادمین جدید را بفرست:", reply_markup=back_admin())
        return
    if data == "a:adm_del":
        set_st(c, "a_adm_del")
        await q.edit_message_text("آیدی ادمینی که حذف شود:", reply_markup=back_admin())
        return

    if data == "a:stats":
        with tx() as conn:
            uc = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
            rc = conn.execute("SELECT COUNT(*) c FROM referrals WHERE confirmed=1").fetchone()["c"]
            pc = conn.execute("SELECT COUNT(*) c FROM claims WHERE status='pending'").fetchone()["c"]
            tw = conn.execute("SELECT SUM(wallet) s FROM users").fetchone()["s"] or 0
        await q.edit_message_text(
            "کاربران: " + num(uc) + "\nرفرال: " + num(rc)
            + "\nدر انتظار: " + num(pc) + "\nمجموع کیف: " + num(tw),
            reply_markup=admin_kb(),
        )
        return


async def on_text(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.message.text:
        return
    user = u.effective_user
    chat = u.effective_chat
    text = u.message.text.strip()
    ensure_user(user)
    low = re.sub(r"^/", "", text).strip().lower()

    if low in ("admin", "پنل", "panel"):
        await open_admin(u, c)
        return

    st = get_st(c)

    if st and st["kind"] == "card":
        card = re.sub(r"\D", "", text)
        if len(card) != sint("card_len", 12):
            await u.message.reply_text(
                "❌ شماره کارت اشتباه است.\nباید دقیقاً "
                + str(sint("card_len", 12))
                + " رقم باشد.",
                reply_markup=InlineKeyboardMarkup(
                    [[btn("🔙 انصراف", f"m:claim:{user.id}", "danger")]]
                ),
            )
            return
        amount = int(st["extra"].get("amount") or 0)
        uu = get_user(user.id)
        if int(uu["wallet"]) < amount:
            clear_st(c)
            await u.message.reply_text("موجودی کافی نیست.", reply_markup=main_kb(user.id))
            return
        with tx() as conn:
            if conn.execute(
                "SELECT 1 FROM claims WHERE user_id=? AND status='pending'", (user.id,)
            ).fetchone():
                clear_st(c)
                await u.message.reply_text("درخواست قبلی در انتظاره.")
                return
            conn.execute("UPDATE users SET wallet=wallet-? WHERE id=?", (amount, user.id))
            conn.execute(
                "INSERT INTO claims(user_id,card,amount,status,created_at) VALUES (?,?,?,'pending',?)",
                (user.id, card, amount, time.time()),
            )
        clear_st(c)
        await u.message.reply_text("✅ درخواست برای ادمین ارسال شد.")
        with tx() as conn:
            ads = [r["user_id"] for r in conn.execute("SELECT user_id FROM admins").fetchall()]
        uname = ("@" + user.username) if user.username else "—"
        for aid in ads:
            try:
                await c.bot.send_message(
                    int(aid),
                    "📥 برداشت جدید\n"
                    "اسم: " + user.full_name + "\n"
                    "یوزرنیم: " + uname + "\n"
                    "آیدی: <code>" + str(user.id) + "</code>\n"
                    "کارت: <code>" + card + "</code>\n"
                    "مبلغ: " + num(amount),
                    parse_mode="HTML",
                )
            except Exception:
                pass
        return

    if st and is_admin(user.id) and chat.type == ChatType.PRIVATE:
        kind = st["kind"]

        if kind == "a_ch_link":
            chat_obj = await resolve_chat_input(c.bot, text, u.message)
            if not chat_obj:
                await u.message.reply_text(
                    "❌ لینک/یوزرنیم اشتباه است یا ربات به آن دسترسی ندارد.",
                    reply_markup=back_admin(),
                )
                return
            if not await bot_is_admin_in(c.bot, chat_obj.id):
                await u.message.reply_text(
                    "❌ ربات ادمین این چنل/گپ نیست. اول ربات را ادمین کن.",
                    reply_markup=back_admin(),
                )
                return
            with tx() as conn:
                if conn.execute("SELECT COUNT(*) c FROM channels").fetchone()["c"] >= 10:
                    clear_st(c)
                    await u.message.reply_text("حداکثر ۱۰ چنل.", reply_markup=admin_kb())
                    return
            invite = ""
            if "t.me/+" in text or "joinchat" in text.lower():
                invite = text if text.startswith("http") else ("https://" + text.lstrip("/"))
            set_st(
                c,
                "a_ch_label",
                {
                    "chat_id": chat_obj.id,
                    "title": chat_obj.title or str(chat_obj.id),
                    "username": getattr(chat_obj, "username", None) or "",
                    "invite": invite,
                },
            )
            await u.message.reply_text(
                "✅ چنل پیدا شد: <b>" + str(chat_obj.title) + "</b>\n"
                "اسم دکمه قفل را بفرست (مثلاً: جوین بده)",
                parse_mode="HTML",
                reply_markup=back_admin(),
            )
            return

        if kind == "a_ch_label":
            label = text.strip()[:32] or "جوین بده"
            ex = st["extra"]
            with tx() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO channels
                    (chat_id,title,username,invite_link,button_label,active)
                    VALUES (?,?,?,?,?,1)""",
                    (
                        ex["chat_id"],
                        ex["title"],
                        ex.get("username") or "",
                        ex.get("invite") or "",
                        label,
                    ),
                )
            clear_st(c)
            await u.message.reply_text(
                "✅ قفل تأیید شد\nچنل: " + str(ex["title"]) + "\nدکمه: " + label,
                reply_markup=admin_kb(),
            )
            return

        if kind == "a_reward":
            amt = parse_amt(text)
            if not amt:
                await u.message.reply_text("❌ مبلغ اشتباه.", reply_markup=back_admin())
                return
            sset("ref_reward", str(amt))
            clear_st(c)
            await u.message.reply_text("پاداش رفرال: " + num(amt), reply_markup=admin_kb())
            return

        if kind == "a_opt_label":
            set_st(c, "a_opt_amount", {"label": text.strip()[:40] or "برداشت"})
            await u.message.reply_text(
                "امتیاز مورد نیاز را بفرست (مثلاً 600k):",
                reply_markup=back_admin(),
            )
            return

        if kind == "a_opt_amount":
            amt = parse_amt(text)
            if not amt:
                await u.message.reply_text("❌ مبلغ اشتباه.", reply_markup=back_admin())
                return
            label = st["extra"].get("label") or num(amt)
            with tx() as conn:
                conn.execute(
                    "INSERT INTO claim_options(threshold,label,active,sort_order) VALUES (?,?,1,99)",
                    (amt, label),
                )
            clear_st(c)
            await u.message.reply_text(
                "✅ گزینه ثبت شد: " + label + " / " + num(amt),
                reply_markup=admin_kb(),
            )
            return

        if kind in ("a_add", "a_sub"):
            parts = text.split()
            if len(parts) < 2:
                await u.message.reply_text("❌ آیدی مبلغ", reply_markup=back_admin())
                return
            try:
                tid = int(parts[0])
            except ValueError:
                await u.message.reply_text("❌ آیدی عددی باشد.", reply_markup=back_admin())
                return
            amt = parse_amt(parts[1])
            if not amt:
                await u.message.reply_text("❌ مبلغ اشتباه.", reply_markup=back_admin())
                return
            ensure_user(
                type("U", (), {"id": tid, "username": "", "full_name": str(tid), "is_bot": False})()
            )
            with tx() as conn:
                if kind == "a_add":
                    conn.execute("UPDATE users SET wallet=wallet+? WHERE id=?", (amt, tid))
                else:
                    conn.execute("UPDATE users SET wallet=MAX(0,wallet-?) WHERE id=?", (amt, tid))
            clear_st(c)
            await u.message.reply_text("✅ انجام شد.", reply_markup=admin_kb())
            return

        if kind == "a_adm_add":
            try:
                aid = int(re.sub(r"\D", "", text) or "0")
            except Exception:
                aid = 0
            if aid < 1000:
                await u.message.reply_text("❌ آیدی نامعتبر.", reply_markup=back_admin())
                return
            with tx() as conn:
                conn.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (aid,))
            clear_st(c)
            await u.message.reply_text(
                "✅ ادمین <code>" + str(aid) + "</code> اضافه شد.\n"
                "آن فرد در پیوی ربات بزند: <code>پنل</code> یا <code>/admin</code>",
                parse_mode="HTML",
                reply_markup=admin_kb(),
            )
            try:
                await c.bot.send_message(aid, "شما ادمین ربات شدی.\nدر پیوی بزن: پنل")
            except Exception:
                pass
            return

        if kind == "a_adm_del":
            try:
                aid = int(re.sub(r"\D", "", text) or "0")
            except Exception:
                aid = 0
            if aid == ADMIN_ID:
                await u.message.reply_text("ادمین اصلی حذف نمی‌شود.", reply_markup=back_admin())
                return
            if aid < 1000:
                await u.message.reply_text("❌ آیدی نامعتبر.", reply_markup=back_admin())
                return
            with tx() as conn:
                conn.execute("DELETE FROM admins WHERE user_id=?", (aid,))
            clear_st(c)
            await u.message.reply_text("حذف شد.", reply_markup=admin_kb())
            return

    if low in ("منو", "menu"):
        if not await require_join(u, c):
            return
        await try_confirm_referral(c.bot, user)
        await u.message.reply_text("منو:", reply_markup=main_kb(user.id))
        return
    if low in ("موجودی", "balance"):
        if not await require_join(u, c):
            return
        uu = get_user(user.id)
        await u.message.reply_text("💰 " + num(uu["wallet"]) + "\n👥 رفرال: " + str(uu["ref_count"]))
        return
    if low in ("لینک", "دعوت", "رفرال"):
        if not await require_join(u, c):
            return
        me = await c.bot.get_me()
        await u.message.reply_text(
            "<code>https://t.me/" + me.username + "?start=ref" + str(user.id) + "</code>",
            parse_mode="HTML",
        )
        return
    if low in ("راهنما", "help"):
        await u.message.reply_text("لینک دعوت · موجودی · برداشت · پنل (ادمین)")
        return


def main():
    init_db()
    req = HTTPXRequest(
        connect_timeout=60.0, read_timeout=60.0, write_timeout=60.0, pool_timeout=60.0
    )
    get_req = HTTPXRequest(
        connect_timeout=60.0, read_timeout=60.0, write_timeout=60.0, pool_timeout=60.0
    )
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(req)
        .get_updates_request(get_req)
        .build()
    )

    async def safe_cb(update, context):
        try:
            await on_cb(update, context)
        except Exception as e:
            log.exception("cb")
            try:
                if update.callback_query:
                    await update.callback_query.answer(str(e)[:100], show_alert=True)
            except Exception:
                pass

    async def safe_text(update, context):
        try:
            await on_text(update, context)
        except Exception as e:
            log.exception("text")
            try:
                if update.effective_message:
                    kb = None
                    if is_admin(update.effective_user.id):
                        kb = InlineKeyboardMarkup(
                            [[btn("🔙 پنل ادمین", "a:home", "primary")]]
                        )
                    await update.effective_message.reply_text(
                        "⚠️ " + str(e)[:120], reply_markup=kb
                    )
            except Exception:
                pass

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", open_admin))
    app.add_handler(CallbackQueryHandler(safe_cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, safe_text))
    app.add_handler(MessageHandler(filters.COMMAND, safe_text))
    log.info("bot up")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        bootstrap_retries=10,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
