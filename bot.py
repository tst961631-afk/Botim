# -*- coding: utf-8 -*-
"""
ربات قفل عضویت + رفرال + کیف MeowPoint + برداشت پله‌ای
"""
from __future__ import annotations
import re, time, logging, sqlite3, threading, secrets
from contextlib import contextmanager

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters, ChatMemberHandler,
)
from telegram.constants import ChatType, ChatMemberStatus
from telegram.request import HTTPXRequest

BOT_TOKEN = "8975007734:AAEkghW4tK0DeG9uOKw87Lgep8XUWlMiiLY"
ADMIN_ID = 7530457395
DB_PATH = "lock_ref.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lockref")
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
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            name TEXT DEFAULT '',
            wallet INTEGER DEFAULT 0,
            ref_count INTEGER DEFAULT 0,
            claimable INTEGER DEFAULT 0,
            referred_by INTEGER,
            joined_at REAL,
            last_active REAL,
            status TEXT DEFAULT 'active'
        );
        CREATE TABLE IF NOT EXISTS channels (
            chat_id INTEGER PRIMARY KEY,
            title TEXT,
            chat_type TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            card TEXT,
            ref_count INTEGER,
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
            kind TEXT NOT NULL,
            threshold INTEGER NOT NULL,
            label TEXT DEFAULT '',
            active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0
        );
        """)
        c.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (ADMIN_ID,))
        defaults = {
            "lock_enabled": "1",
            "ref_reward": "100000",
            "card_len": "12",
        }
        for k, v in defaults.items():
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (k, v))
        # default claim options only if empty
        n = c.execute("SELECT COUNT(*) c FROM claim_options").fetchone()["c"]
        if n == 0:
            for i, th in enumerate([6, 8, 12, 16, 18]):
                c.execute(
                    "INSERT INTO claim_options(kind,threshold,label,active,sort_order) VALUES (?,?,?,1,?)",
                    ("ref", th, f"{th} رفرال", i),
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
    with tx() as c:
        return bool(c.execute("SELECT 1 FROM admins WHERE user_id=?", (int(uid),)).fetchone()) or int(uid) == ADMIN_ID

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
    elif s.startswith("m") or s.startswith("م"):
        v *= 1_000_000
    return int(v)

def btn(text, data, style=None):
    kw = {"text": str(text)[:64], "callback_data": data}
    if style in ("success", "danger", "primary"):
        kw["style"] = style
    try:
        return InlineKeyboardButton(**kw)
    except TypeError:
        kw.pop("style", None)
        return InlineKeyboardButton(**kw)

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

def get_claim_options():
    with tx() as c:
        return c.execute(
            "SELECT * FROM claim_options WHERE active=1 ORDER BY sort_order, id"
        ).fetchall()

def format_need_msg(opt, uu):
    """پیام کامل وقتی شرط برداشت برقرار نیست."""
    kind = opt["kind"]
    th = int(opt["threshold"])
    if kind == "ref":
        have = int(uu["ref_count"] or 0)
        return (
            f"❌ رفرال شما کافی نیست\n\n"
            f"📌 گزینه: {opt['label'] or (str(th) + ' رفرال')}\n"
            f"👥 رفرال شما: <b>{have}</b>\n"
            f"🎯 نیاز: <b>{th}</b>\n"
            f"📉 کمبود: <b>{max(0, th - have)}</b>\n\n"
            f"با دعوت دوستان و تأیید عضویت، رفرال‌ات بیشتر می‌شود."
        )
    # points / claimable
    have = int(uu["claimable"] or 0)
    return (
        f"❌ امتیاز قابل‌برداشت شما کافی نیست\n\n"
        f"📌 گزینه: {opt['label'] or (num(th) + ' میوپوینت')}\n"
        f"💰 امتیاز شما: <b>{num(have)}</b>\n"
        f"🎯 نیاز: <b>{num(th)}</b>\n"
        f"📉 کمبود: <b>{num(max(0, th - have))}</b>\n\n"
        f"با رفرال‌های جدید امتیاز جمع می‌شود."
    )


def active_channels():
    with tx() as c:
        return c.execute("SELECT * FROM channels WHERE active=1").fetchall()

async def not_joined_channels(bot, user_id):
    missing = []
    for ch in active_channels():
        try:
            m = await bot.get_chat_member(int(ch["chat_id"]), int(user_id))
            if m.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED, "left", "kicked"):
                missing.append(ch)
            elif m.status == ChatMemberStatus.RESTRICTED and not getattr(m, "is_member", True):
                missing.append(ch)
        except Exception:
            missing.append(ch)
    return missing

def lock_on():
    return sget("lock_enabled", "1") == "1"

async def require_join(update, context) -> bool:
    """True = اجازه ادامه. False = پیام قفل فرستاده شد."""
    if not lock_on():
        return True
    user = update.effective_user
    missing = await not_joined_channels(context.bot, user.id)
    if not missing:
        return True
    rows = []
    for ch in missing:
        cid = int(ch["chat_id"])
        title = ch["title"] or str(cid)
        # deep link
        try:
            chat = await context.bot.get_chat(cid)
            if chat.username:
                url = f"https://t.me/{chat.username}"
            else:
                url = None
        except Exception:
            url = None
        if url:
            rows.append([InlineKeyboardButton(f"📢 {title}", url=url)])
        else:
            rows.append([btn(f"📢 {title} (آیدی: {cid})", "noop")])
    rows.append([btn("✅ عضو شدم — بررسی", "check_join", "success")])
    text = (
        "🔒 برای استفاده از ربات باید عضو کانال/گپ‌های زیر باشی:\n\n"
        + "\n".join(f"• {ch['title'] or ch['chat_id']}" for ch in missing)
        + "\n\nبعد از عضویت دکمه بررسی را بزن."
    )
    msg = update.effective_message
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
    elif msg:
        await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(rows))
    return False

def main_kb(uid):
    return InlineKeyboardMarkup([
        [btn("💰 موجودی", f"bal:{uid}", "primary"), btn("🔗 لینک دعوت", f"reflink:{uid}", "success")],
        [btn("👥 رفرال‌های من", f"myrefs:{uid}", "primary")],
        [btn("💸 دریافت میوپوینت", f"claim:{uid}", "success")],
        [btn("📖 راهنما", f"help:{uid}", "primary")],
    ])

def admin_kb():
    lock = "🟢 قفل روشن" if lock_on() else "🔴 قفل خاموش"
    return InlineKeyboardMarkup([
        [btn(lock, "a:toggle_lock", "primary")],
        [btn("📢 مدیریت چنل‌ها", "a:channels", "primary")],
        [btn("💵 پاداش رفرال", "a:reward", "primary")],
        [btn("⚙️ گزینه‌های برداشت", "a:copt", "primary")],
        [btn("📥 دریافتی‌ها", "a:claims", "success")],
        [btn("➕ واریز دستی", "a:add", "success"), btn("➖ کسر", "a:sub", "danger")],
        [btn("👤 ادمین‌ها", "a:admins", "primary"), btn("📊 آمار", "a:stats", "primary")],
        [btn("❌ بستن", "a:close", "danger")],
    ])

# ─── handlers ───
async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    user = u.effective_user
    ensure_user(user)
    text = u.message.text or ""
    ref_id = None
    m = re.search(r"ref[_-]?(\d+)", text)
    if m:
        ref_id = int(m.group(1))

    # save pending referrer if new
    if ref_id and ref_id != user.id:
        with tx() as conn:
            uu = conn.execute("SELECT referred_by FROM users WHERE id=?", (user.id,)).fetchone()
            if uu and not uu["referred_by"]:
                # only set referred_by if not already set; confirm later after join
                exists = conn.execute("SELECT 1 FROM referrals WHERE referred_id=?", (user.id,)).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO referrals(referrer_id, referred_id, amount, confirmed, created_at) VALUES (?,?,?,0,?)",
                        (ref_id, user.id, sint("ref_reward", 100000), time.time()),
                    )
                    conn.execute("UPDATE users SET referred_by=? WHERE id=?", (ref_id, user.id))

    if not await require_join(u, c):
        # try confirm referral after they come back via check_join
        return

    await try_confirm_referral(c.bot, user)
    await u.message.reply_text(
        f"سلام <b>{user.full_name}</b> 👋\n"
        f"به ربات خوش آمدی.\n"
        f"از منو استفاده کن یا بنویس: <code>منو</code>",
        parse_mode="HTML",
        reply_markup=main_kb(user.id),
    )

async def try_confirm_referral(bot, user):
    """اگر رفرال در انتظار بود و جوین کامل است، امتیاز بده و به معرف خبر بده."""
    with tx() as c:
        row = c.execute(
            "SELECT * FROM referrals WHERE referred_id=? AND confirmed=0", (user.id,)
        ).fetchone()
    if not row:
        return
    if lock_on():
        missing = await not_joined_channels(bot, user.id)
        if missing:
            return
    reward = int(row["amount"] or sint("ref_reward", 100000))
    referrer = int(row["referrer_id"])
    with tx() as c:
        c.execute("UPDATE referrals SET confirmed=1, amount=? WHERE referred_id=?", (reward, user.id))
        c.execute(
            "UPDATE users SET wallet=wallet+?, claimable=claimable+?, ref_count=ref_count+1 WHERE id=?",
            (reward, reward, referrer),
        )
    # notify referrer
    uname = f"@{user.username}" if user.username else user.full_name
    try:
        await bot.send_message(
            referrer,
            f"🎉 یک نفر با لینک تو آمد!\n"
            f"👤 {uname}\n"
            f"💰 +{num(reward)} میوپوینت به کیف و قابل‌برداشتت اضافه شد.",
        )
    except Exception:
        pass

async def on_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    data = q.data or ""
    user = u.effective_user
    ensure_user(user)

    if data == "noop":
        await q.answer()
        return
    if data == "a:close":
        await q.answer()
        try:
            await q.message.delete()
        except Exception:
            pass
        return

    if data == "check_join":
        await q.answer()
        missing = await not_joined_channels(c.bot, user.id) if lock_on() else []
        if missing:
            await q.answer("هنوز همه را جوین نکردی", show_alert=True)
            return
        await try_confirm_referral(c.bot, user)
        await q.edit_message_text(
            "✅ عضویت تأیید شد. از منو استفاده کن.",
            reply_markup=main_kb(user.id),
        )
        return

    # player panels ownership
    if ":" in data and data.split(":")[0] in ("bal", "reflink", "myrefs", "claim", "help", "ctier", "copt"):
        try:
            owner = int(data.split(":")[-1])
            if owner != user.id and not data.startswith("ctier:"):
                # ctier has format ctier:TIER:UID
                pass
            if data.startswith("ctier:") or data.startswith("copt:"):
                owner = int(data.split(":")[2])
            if owner != user.id:
                await q.answer("این پنل برای تو نیست", show_alert=True)
                return
        except Exception:
            pass

    # force join for non-admin player actions
    if not data.startswith("a:") and data != "check_join":
        if lock_on():
            missing = await not_joined_channels(c.bot, user.id)
            if missing:
                await q.answer()
                # rebuild lock message
                class Fake:
                    effective_user = user
                    effective_message = q.message
                    callback_query = q
                await require_join(Fake(), c)
                return

    await q.answer()

    if data.startswith("help:"):
        await q.edit_message_text(
            "📖 <b>راهنما</b>\n\n"
            "🔗 <b>لینک دعوت:</b> بفرست برای دوستات؛ با جوین (و عبور از قفل) امتیاز می‌گیری.\n"
            "💰 <b>موجودی:</b> کیف + امتیاز قابل برداشت.\n"
            "💸 <b>دریافت:</b> با رسیدن به تعداد رفرال مشخص، درخواست کارت بده.\n"
            "🔒 اگر قفل فعال باشد تا عضو چنل‌ها نشوی رفرال ثبت نمی‌شود.",
            parse_mode="HTML",
            reply_markup=main_kb(user.id),
        )
        return

    if data.startswith("bal:"):
        uu = get_user(user.id)
        await q.edit_message_text(
            f"💰 <b>کیف پول</b>\n"
            f"موجودی: <b>{num(uu['wallet'])}</b>\n"
            f"قابل برداشت (رفرال): <b>{num(uu['claimable'])}</b>\n"
            f"تعداد رفرال: <b>{num(uu['ref_count'])}</b>",
            parse_mode="HTML",
            reply_markup=main_kb(user.id),
        )
        return

    if data.startswith("reflink:"):
        me = await c.bot.get_me()
        link = f"https://t.me/{me.username}?start=ref{user.id}"
        reward = num(sint("ref_reward", 100000))
        await q.edit_message_text(
            f"🔗 <b>لینک دعوت تو</b>\n\n"
            f"<code>{link}</code>\n\n"
            f"کپی کن و برای رفیقات بفرست.\n"
            f"هر دعوت تأییدشده: <b>{reward}</b> میوپوینت",
            parse_mode="HTML",
            reply_markup=main_kb(user.id),
        )
        return

    if data.startswith("myrefs:"):
        uu = get_user(user.id)
        with tx() as conn:
            rows = conn.execute(
                "SELECT referred_id, amount, confirmed FROM referrals WHERE referrer_id=? ORDER BY created_at DESC LIMIT 30",
                (user.id,),
            ).fetchall()
        lines = [
            f"👥 <b>رفرال‌های تو</b>",
            f"تعداد تأییدشده: <b>{uu['ref_count']}</b>",
            f"قابل برداشت: <b>{num(uu['claimable'])}</b>\n",
        ]
        for r in rows:
            st = "✅" if r["confirmed"] else "⏳"
            lines.append(f"{st} <code>{r['referred_id']}</code> — {num(r['amount'])}")
        if not rows:
            lines.append("هنوز کسی با لینک تو نیامده.")
        await q.edit_message_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=main_kb(user.id),
        )
        return

    if data.startswith("claim:") and not data.startswith("copt:"):
        uu = get_user(user.id)
        with tx() as conn:
            pend = conn.execute(
                "SELECT id FROM claims WHERE user_id=? AND status='pending'", (user.id,)
            ).fetchone()
        if pend:
            await q.edit_message_text(
                "یک درخواست در انتظار تأیید ادمین داری. صبر کن.",
                reply_markup=main_kb(user.id),
            )
            return
        opts = get_claim_options()
        if not opts:
            await q.edit_message_text(
                "هنوز گزینه‌ای برای برداشت از طرف ادمین ثبت نشده.",
                reply_markup=main_kb(user.id),
            )
            return
        rows = []
        for o in opts:
            if o["kind"] == "ref":
                label = o["label"] or f"{o['threshold']} رفرال"
            else:
                label = o["label"] or f"{num(o['threshold'])} میوپوینت"
            rows.append([btn(label, f"copt:{o['id']}:{user.id}", "success")])
        rows.append([btn("🔙", f"bal:{user.id}", "primary")])
        await q.edit_message_text(
            f"💸 <b>دریافت میوپوینت</b>\n\n"
            f"👥 رفرال تأییدشده: <b>{uu['ref_count']}</b>\n"
            f"💰 امتیاز قابل برداشت: <b>{num(uu['claimable'])}</b>\n\n"
            f"یک گزینه را انتخاب کن:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if data.startswith("copt:"):
        parts = data.split(":")
        oid, owner = int(parts[1]), int(parts[2])
        if user.id != owner:
            await q.answer("برای تو نیست", show_alert=True)
            return
        with tx() as conn:
            opt = conn.execute("SELECT * FROM claim_options WHERE id=? AND active=1", (oid,)).fetchone()
        if not opt:
            await q.answer("گزینه نامعتبر", show_alert=True)
            return
        uu = get_user(user.id)
        ok = False
        if opt["kind"] == "ref":
            ok = int(uu["ref_count"] or 0) >= int(opt["threshold"])
        else:
            ok = int(uu["claimable"] or 0) >= int(opt["threshold"])
        if not ok:
            # پیام کامل داخل چت (مثل پاپ‌آپ واضح)
            await q.edit_message_text(
                format_need_msg(opt, uu),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [btn("🔙 بازگشت به گزینه‌ها", f"claim:{user.id}", "primary")],
                    [btn("🏠 منو", f"bal:{user.id}", "primary")],
                ]),
            )
            return
        if int(uu["claimable"] or 0) <= 0:
            await q.edit_message_text(
                "❌ امتیاز قابل‌برداشت شما صفر است.\nبا رفرال جدید امتیاز جمع می‌شود.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[btn("🔙", f"claim:{user.id}", "primary")]]),
            )
            return
        with tx() as conn:
            if conn.execute(
                "SELECT 1 FROM claims WHERE user_id=? AND status='pending'", (user.id,)
            ).fetchone():
                await q.answer("درخواست قبلی در انتظاره", show_alert=True)
                return
        amount = int(uu["claimable"])
        set_st(c, "card", {"opt_id": oid, "amount": amount, "kind": opt["kind"], "threshold": int(opt["threshold"])})
        await q.edit_message_text(
            f"✅ شرایط برقرار است\n\n"
            f"📌 {opt['label'] or opt['kind']}\n"
            f"💰 مبلغ قابل دریافت: <b>{num(amount)}</b>\n\n"
            f"شماره کارت میویی را بفرست (فقط عدد، ۱۲ رقم)\n"
            f"مثال: <code>109658451189</code>",
            parse_mode="HTML",
        )
        return

    # ── admin ──
    if data.startswith("a:") and not is_admin(user.id):
        await q.answer("دسترسی نداری", show_alert=True)
        return

    if data == "a:toggle_lock":
        cur = lock_on()
        sset("lock_enabled", "0" if cur else "1")
        await q.edit_message_text("تنظیم قفل عوض شد.", reply_markup=admin_kb())
        return

    if data == "a:channels":
        with tx() as conn:
            rows = conn.execute("SELECT * FROM channels").fetchall()
        lines = ["📢 چنل/گپ‌های قفل\n"]
        kb_rows = []
        for r in rows:
            st = "✅" if r["active"] else "❌"
            lines.append(f"{st} {r['title'] or r['chat_id']} — <code>{r['chat_id']}</code>")
            kb_rows.append([
                btn(("خاموش" if r["active"] else "روشن"), f"a:ch_tog:{r['chat_id']}", "primary"),
                btn("حذف", f"a:ch_del:{r['chat_id']}", "danger"),
            ])
        kb_rows.append([btn("➕ افزودن (فوروارد از چنل)", "a:ch_add", "success")])
        kb_rows.append([btn("🔙", "a:home", "primary")])
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb_rows))
        return

    if data == "a:ch_add":
        set_st(c, "a_ch_add")
        await q.edit_message_text(
            "یک پیام از چنل/گپ را <b>فوروارد</b> کن، یا آیدی عددی را بفرست (مثل <code>-100123</code>).",
            parse_mode="HTML",
        )
        return

    if data.startswith("a:ch_tog:"):
        cid = int(data.split(":")[2])
        with tx() as conn:
            cur = conn.execute("SELECT active FROM channels WHERE chat_id=?", (cid,)).fetchone()
            if cur:
                conn.execute("UPDATE channels SET active=? WHERE chat_id=?", (0 if cur["active"] else 1, cid))
        await q.answer("عوض شد")
        # refresh
        data = "a:channels"
        # fallthrough by re-calling logic - simple message
        await q.edit_message_text("انجام شد. دوباره «مدیریت چنل‌ها» را بزن.", reply_markup=admin_kb())
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
            f"پاداش فعلی هر رفرال: <b>{num(sint('ref_reward', 100000))}</b>\n"
            f"مقدار جدید را بفرست (مثال: <code>100k</code> یا <code>200000</code>)",
            parse_mode="HTML",
        )
        return

    if data == "a:copt":
        with tx() as conn:
            opts = conn.execute("SELECT * FROM claim_options ORDER BY sort_order, id").fetchall()
        lines = ["⚙️ <b>گزینه‌های برداشت</b>\n", "هر گزینه یا بر اساس رفرال است یا امتیاز.\n"]
        kb_rows = []
        for o in opts:
            st = "✅" if o["active"] else "❌"
            if o["kind"] == "ref":
                info = f"{st} رفرال ≥ {o['threshold']} | {o['label']}"
            else:
                info = f"{st} امتیاز ≥ {num(o['threshold'])} | {o['label']}"
            lines.append(info)
            kb_rows.append([
                btn(("خاموش" if o["active"] else "روشن"), f"a:copt_tog:{o['id']}", "primary"),
                btn("حذف", f"a:copt_del:{o['id']}", "danger"),
            ])
        kb_rows.append([btn("➕ افزودن گزینه", "a:copt_add", "success")])
        kb_rows.append([btn("🔙", "a:home", "primary")])
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb_rows))
        return

    if data == "a:copt_add":
        set_st(c, "a_copt_add")
        await q.edit_message_text(
            "گزینه جدید را این‌طور بفرست:\n\n"
            "<b>رفرال</b>:\n<code>رفرال 6</code>\n<code>رفرال 6 شش رفرال</code>\n\n"
            "<b>امتیاز</b>:\n<code>امتیاز 600000</code>\n<code>امتیاز 600k برداشت 600کا</code>",
            parse_mode="HTML",
        )
        return

    if data.startswith("a:copt_tog:"):
        oid = int(data.split(":")[2])
        with tx() as conn:
            cur = conn.execute("SELECT active FROM claim_options WHERE id=?", (oid,)).fetchone()
            if cur:
                conn.execute("UPDATE claim_options SET active=? WHERE id=?", (0 if cur["active"] else 1, oid))
        await q.edit_message_text("عوض شد. دوباره گزینه‌ها را باز کن.", reply_markup=admin_kb())
        return

    if data.startswith("a:copt_del:"):
        oid = int(data.split(":")[2])
        with tx() as conn:
            conn.execute("DELETE FROM claim_options WHERE id=?", (oid,))
        await q.edit_message_text("حذف شد.", reply_markup=admin_kb())
        return

    if data == "a:claims":
        with tx() as conn:
            rows = conn.execute(
                "SELECT * FROM claims WHERE status='pending' ORDER BY id ASC LIMIT 20"
            ).fetchall()
        if not rows:
            await q.edit_message_text("درخواست معلقی نیست.", reply_markup=admin_kb())
            return
        lines = ["📥 <b>دریافتی‌های در انتظار</b>\n"]
        kb_rows = []
        for r in rows:
            urow = get_user(r["user_id"])
            uname = f"@{urow['username']}" if urow and urow["username"] else str(r["user_id"])
            lines.append(
                f"#{r['id']} | {uname}\n"
                f"کارت: <code>{r['card']}</code>\n"
                f"رفرال: {r['ref_count']} | مبلغ: {num(r['amount'])}\n"
            )
            kb_rows.append([btn(f"✅ انجام شد #{r['id']}", f"a:done:{r['id']}", "success")])
        kb_rows.append([btn("🔙", "a:home", "primary")])
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb_rows))
        return

    if data.startswith("a:done:"):
        cid = int(data.split(":")[2])
        with tx() as conn:
            cl = conn.execute("SELECT * FROM claims WHERE id=? AND status='pending'", (cid,)).fetchone()
            if not cl:
                await q.answer("پیدا نشد", show_alert=True)
                return
            conn.execute(
                "UPDATE claims SET status='done', done_at=? WHERE id=?",
                (time.time(), cid),
            )
            # claimable already reduced on submit; ensure zero leftover for that claim
        try:
            await c.bot.send_message(
                int(cl["user_id"]),
                f"✅ سفارش برداشتت انجام شد.\nمبلغ: {num(cl['amount'])}",
            )
        except Exception:
            pass
        await q.edit_message_text(f"#{cid} انجام شد.", reply_markup=admin_kb())
        return

    if data == "a:add":
        set_st(c, "a_add")
        await q.edit_message_text("آیدی و مبلغ:\n<code>123456 50k</code>", parse_mode="HTML")
        return
    if data == "a:sub":
        set_st(c, "a_sub")
        await q.edit_message_text("آیدی و مبلغ کسر:\n<code>123456 10k</code>", parse_mode="HTML")
        return
    if data == "a:admins":
        set_st(c, "a_admins")
        with tx() as conn:
            ads = conn.execute("SELECT user_id FROM admins").fetchall()
        await q.edit_message_text(
            "ادمین‌ها:\n" + "\n".join(str(a["user_id"]) for a in ads)
            + "\n\n<code>ادمین + آیدی</code>\n<code>ادمین - آیدی</code>",
            parse_mode="HTML",
        )
        return
    if data == "a:stats":
        with tx() as conn:
            uc = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
            rc = conn.execute("SELECT COUNT(*) c FROM referrals WHERE confirmed=1").fetchone()["c"]
            pc = conn.execute("SELECT COUNT(*) c FROM claims WHERE status='pending'").fetchone()["c"]
            tw = conn.execute("SELECT SUM(wallet) s FROM users").fetchone()["s"] or 0
        await q.edit_message_text(
            f"👥 کاربران: {num(uc)}\n✅ رفرال‌ها: {num(rc)}\n📥 در انتظار: {num(pc)}\n💰 مجموع کیف: {num(tw)}",
            reply_markup=admin_kb(),
        )
        return
    if data == "a:home":
        await q.edit_message_text("🎛 پنل ادمین", reply_markup=admin_kb())
        return

async def on_text(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.message.text:
        return
    user = u.effective_user
    chat = u.effective_chat
    text = u.message.text.strip()
    ensure_user(user)
    st = get_st(c)
    low = re.sub(r"^/", "", text).strip()

    # card input for claim
    if st and st["kind"] == "card":
        card = re.sub(r"\D", "", text)
        need_len = sint("card_len", 12)
        if len(card) != need_len:
            await u.message.reply_text(f"شماره کارت باید دقیقاً {need_len} رقم باشد.\nمثال: 109658451189")
            return
        uu = get_user(user.id)
        amount = int(uu["claimable"] or 0)
        if amount <= 0:
            clear_st(c)
            await u.message.reply_text("امتیاز قابل برداشت صفر است.")
            return
        # re-validate option
        opt_id = st["extra"].get("opt_id")
        if opt_id:
            with tx() as conn:
                opt = conn.execute("SELECT * FROM claim_options WHERE id=? AND active=1", (opt_id,)).fetchone()
            if not opt:
                clear_st(c)
                await u.message.reply_text("گزینه دیگر فعال نیست.")
                return
            if opt["kind"] == "ref" and int(uu["ref_count"] or 0) < int(opt["threshold"]):
                clear_st(c)
                await u.message.reply_text("رفرال کافی نیست.")
                return
            if opt["kind"] == "points" and int(uu["claimable"] or 0) < int(opt["threshold"]):
                clear_st(c)
                await u.message.reply_text("امتیاز کافی نیست.")
                return
        with tx() as conn:
            pend = conn.execute(
                "SELECT 1 FROM claims WHERE user_id=? AND status='pending'", (user.id,)
            ).fetchone()
            if pend:
                clear_st(c)
                await u.message.reply_text("درخواست قبلی در انتظاره.")
                return
            conn.execute(
                "INSERT INTO claims(user_id,card,ref_count,amount,status,created_at) VALUES (?,?,?,?,'pending',?)",
                (user.id, card, int(uu["ref_count"]), amount, time.time()),
            )
            # صفر کردن claimable — رفرال می‌ماند
            conn.execute("UPDATE users SET claimable=0 WHERE id=?", (user.id,))
        clear_st(c)
        await u.message.reply_text(
            "✅ درخواستت برای ادمین ارسال شد.\nبعد از انجام، پیام می‌گیری."
        )
        # notify admins
        with tx() as conn:
            ads = [r["user_id"] for r in conn.execute("SELECT user_id FROM admins").fetchall()]
        uname = f"@{user.username}" if user.username else user.full_name
        for aid in ads:
            try:
                await c.bot.send_message(
                    int(aid),
                    f"📥 درخواست برداشت جدید\n"
                    f"کاربر: {uname} (<code>{user.id}</code>)\n"
                    f"کارت: <code>{card}</code>\n"
                    f"رفرال: {uu['ref_count']}\n"
                    f"مبلغ: {num(amount)}\n"
                    f"از پنل → دریافتی‌ها",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        return

    # admin states
    if st and is_admin(user.id) and chat.type == ChatType.PRIVATE:
        kind = st["kind"]
        if kind == "a_ch_add":
            cid = None
            title = None
            if u.message.forward_from_chat:
                cid = u.message.forward_from_chat.id
                title = u.message.forward_from_chat.title
            else:
                try:
                    cid = int(text.strip())
                except ValueError:
                    await u.message.reply_text("آیدی عددی یا فوروارد از چنل بفرست")
                    return
                try:
                    ch = await c.bot.get_chat(cid)
                    title = ch.title or str(cid)
                except Exception:
                    title = str(cid)
            with tx() as conn:
                cnt = conn.execute("SELECT COUNT(*) c FROM channels").fetchone()["c"]
                if cnt >= 10:
                    await u.message.reply_text("حداکثر ۱۰ چنل/گپ")
                    clear_st(c)
                    return
                conn.execute(
                    "INSERT OR REPLACE INTO channels(chat_id,title,chat_type,active) VALUES (?,?,?,1)",
                    (cid, title, "channel"),
                )
            clear_st(c)
            await u.message.reply_text(f"ثبت شد: {title}\n<code>{cid}</code>", parse_mode="HTML", reply_markup=admin_kb())
            return
        if kind == "a_copt_add":
            # رفرال 6 [label...]  |  امتیاز 600k [label...]
            m = re.match(r"^(رفرال|امتیاز)\s+(\S+)(?:\s+(.+))?$", text.strip(), re.I)
            if not m:
                await u.message.reply_text("فرمت: رفرال 6 یا امتیاز 600k")
                return
            kind_fa, th_raw, label = m.group(1), m.group(2), (m.group(3) or "").strip()
            kind = "ref" if kind_fa == "رفرال" else "points"
            if kind == "ref":
                try:
                    th = int(th_raw)
                except ValueError:
                    await u.message.reply_text("تعداد رفرال عدد باشد")
                    return
                if not label:
                    label = f"{th} رفرال"
            else:
                th = parse_amt(th_raw)
                if not th:
                    await u.message.reply_text("مبلغ نامعتبر")
                    return
                if not label:
                    label = f"{num(th)} میوپوینت"
            with tx() as conn:
                conn.execute(
                    "INSERT INTO claim_options(kind,threshold,label,active,sort_order) VALUES (?,?,?,1,?)",
                    (kind, th, label, 99),
                )
            clear_st(c)
            await u.message.reply_text(f"✅ ثبت شد: {label}", reply_markup=admin_kb())
            return
        if kind == "a_reward":
            amt = parse_amt(text)
            if not amt:
                await u.message.reply_text("مبلغ نامعتبر")
                return
            sset("ref_reward", str(amt))
            clear_st(c)
            await u.message.reply_text(f"پاداش رفرال: {num(amt)}", reply_markup=admin_kb())
            return
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
            amt = parse_amt(parts[1])
            if not amt:
                await u.message.reply_text("مبلغ؟")
                return
            ensure_user(type("U", (), {"id": tid, "username": "", "full_name": str(tid), "is_bot": False})())
            with tx() as conn:
                if kind == "a_add":
                    conn.execute("UPDATE users SET wallet=wallet+? WHERE id=?", (amt, tid))
                else:
                    conn.execute("UPDATE users SET wallet=MAX(0,wallet-?) WHERE id=?", (amt, tid))
            clear_st(c)
            await u.message.reply_text("OK", reply_markup=admin_kb())
            return
        if kind == "a_admins":
            m = re.match(r"ادمین\s*([+-])\s*(\d+)", text)
            if m:
                op, aid = m.group(1), int(m.group(2))
                with tx() as conn:
                    if op == "+":
                        conn.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (aid,))
                    elif int(user.id) == ADMIN_ID and aid != ADMIN_ID:
                        conn.execute("DELETE FROM admins WHERE user_id=?", (aid,))
                clear_st(c)
                await u.message.reply_text("OK", reply_markup=admin_kb())
            return

    # plain commands
    if re.fullmatch(r"(منو|menu|start)", low, re.I):
        if not await require_join(u, c):
            return
        await try_confirm_referral(c.bot, user)
        await u.message.reply_text("منو:", reply_markup=main_kb(user.id))
        return
    if re.fullmatch(r"(پنل|admin)", low, re.I):
        if chat.type == ChatType.PRIVATE and is_admin(user.id):
            await u.message.reply_text("🎛 پنل ادمین", reply_markup=admin_kb())
        return
    if re.fullmatch(r"(موجودی|بالانس|balance)", low, re.I):
        if not await require_join(u, c):
            return
        uu = get_user(user.id)
        await u.message.reply_text(
            f"💰 کیف: {num(uu['wallet'])}\n💸 قابل برداشت: {num(uu['claimable'])}\n👥 رفرال: {uu['ref_count']}"
        )
        return
    if re.fullmatch(r"(لینک|دعوت|رفرال)", low, re.I):
        if not await require_join(u, c):
            return
        me = await c.bot.get_me()
        link = f"https://t.me/{me.username}?start=ref{user.id}"
        await u.message.reply_text(f"🔗 لینک دعوت:\n<code>{link}</code>", parse_mode="HTML")
        return
    if re.fullmatch(r"(راهنما|help)", low, re.I):
        await u.message.reply_text(
            "لینک دعوت بگیر → بفرست → بعد از جوین دوستت (و عبور از قفل) امتیاز می‌گیری.\n"
            "با رسیدن به پله رفرال از «دریافت میوپوینت» درخواست بده."
        )
        return

def main():
    init_db()
    req = HTTPXRequest(connect_timeout=60.0, read_timeout=60.0, write_timeout=60.0, pool_timeout=60.0)
    get_req = HTTPXRequest(connect_timeout=60.0, read_timeout=60.0, write_timeout=60.0, pool_timeout=60.0)
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
                    await update.effective_message.reply_text("⚠️ %s" % str(e)[:150])
            except Exception:
                pass

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(safe_cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, safe_text))
    app.add_handler(MessageHandler(filters.COMMAND, safe_text))
    log.info("Lock+Ref bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES, bootstrap_retries=10, drop_pending_updates=True)

if __name__ == "__main__":
    main()
