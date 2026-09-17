# -*- coding: utf-8 -*-
"""ربات ردیابی کلیک لینک — چند لینک، آمار، اعلان لحظه‌ای"""
from __future__ import annotations
import re, time, logging, sqlite3, threading, secrets, string
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, InputFile,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters,
)
from telegram.constants import ChatType
from telegram.request import HTTPXRequest

BOT_TOKEN = "8948581158:AAF7KaVHQf4wu_CIJi9XxVD5cNF3LNycXU0"
ADMIN_ID = 7530457395
DB_PATH = "clicks.db"
TZ = timezone(timedelta(hours=3, minutes=30))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("clicks")
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
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT
        );
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            welcome TEXT DEFAULT '',
            active INTEGER DEFAULT 1,
            created_at REAL
        );
        CREATE TABLE IF NOT EXISTS clicks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT DEFAULT '',
            name TEXT DEFAULT '',
            bio TEXT DEFAULT '',
            photo_file_id TEXT DEFAULT '',
            click_count INTEGER DEFAULT 1,
            first_at REAL,
            last_at REAL,
            UNIQUE(link_id, user_id)
        );
        """)
        c.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (ADMIN_ID,))
        c.execute(
            "INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)",
            ("default_welcome", "سلام! به ربات خوش آمدی."),
        )

def is_admin(uid):
    uid = int(uid)
    if uid == ADMIN_ID:
        return True
    with tx() as c:
        return bool(c.execute("SELECT 1 FROM admins WHERE user_id=?", (uid,)).fetchone())

def sget(k, d=""):
    with tx() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
        return r["value"] if r else d

def sset(k, v):
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (k, str(v)))

def now_ts():
    return time.time()

def fmt_dt(ts):
    if not ts:
        return "—"
    return datetime.fromtimestamp(float(ts), TZ).strftime("%Y/%m/%d %H:%M:%S")

def gen_code(n=8):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))

def btn(text, data):
    return InlineKeyboardButton(str(text)[:64], callback_data=str(data)[:64])

def admin_reply_kb():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📊 آمار"), KeyboardButton("🔗 لینک‌ها")],
            [KeyboardButton("👆 کلیک‌ها"), KeyboardButton("⚙️ تنظیم متن")],
            [KeyboardButton("➕ لینک جدید"), KeyboardButton("👤 ادمین‌ها")],
        ],
        resize_keyboard=True,
    )

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

def user_label(username, name, user_id=None):
    if username:
        return f"@{username}"
    if name:
        return name
    return str(user_id) if user_id else "—"

def link_url(bot_username, code):
    return f"https://t.me/{bot_username}?start={code}"

async def fetch_profile(bot, user_id):
    """عکس و بیو تا حد ممکن."""
    photo_id = ""
    bio = ""
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if photos.total_count and photos.photos:
            photo_id = photos.photos[0][-1].file_id
    except Exception:
        pass
    try:
        ch = await bot.get_chat(user_id)
        bio = (getattr(ch, "bio", None) or "")[:300]
    except Exception:
        pass
    return photo_id, bio

# ─── handlers ───
async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    user = u.effective_user
    text = u.message.text or ""
    parts = text.split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""

    # ادمین بدون payload → پنل
    if is_admin(user.id) and not payload:
        await u.message.reply_text(
            "🎛 پنل ادمین آماده است.",
            reply_markup=admin_reply_kb(),
        )
        return

    # پیدا کردن لینک
    link = None
    if payload:
        with tx() as conn:
            link = conn.execute(
                "SELECT * FROM links WHERE code=? AND active=1", (payload,)
            ).fetchone()
            if not link:
                # شاید code عددی/قدیمی
                link = conn.execute(
                    "SELECT * FROM links WHERE code=?", (payload,)
                ).fetchone()

    if not link:
        # استارت عادی بدون لینک فعال
        welcome = sget("default_welcome", "سلام! به ربات خوش آمدی.")
        if is_admin(user.id):
            await u.message.reply_text(welcome, reply_markup=admin_reply_kb())
        else:
            await u.message.reply_text(welcome)
        return

    if not int(link["active"]):
        await u.message.reply_text("این لینک غیرفعال است.")
        return

    photo_id, bio = await fetch_profile(c.bot, user.id)
    ts = now_ts()
    is_new = False
    click_count = 1

    with tx() as conn:
        row = conn.execute(
            "SELECT * FROM clicks WHERE link_id=? AND user_id=?",
            (link["id"], user.id),
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE clicks SET
                    username=?, name=?, bio=?, photo_file_id=COALESCE(NULLIF(?, ''), photo_file_id),
                    click_count=click_count+1, last_at=?
                   WHERE link_id=? AND user_id=?""",
                (
                    user.username or "",
                    user.full_name or "",
                    bio or row["bio"] or "",
                    photo_id,
                    ts,
                    link["id"],
                    user.id,
                ),
            )
            click_count = int(row["click_count"]) + 1
            is_new = False
        else:
            conn.execute(
                """INSERT INTO clicks
                (link_id,user_id,username,name,bio,photo_file_id,click_count,first_at,last_at)
                VALUES (?,?,?,?,?,?,1,?,?)""",
                (
                    link["id"],
                    user.id,
                    user.username or "",
                    user.full_name or "",
                    bio,
                    photo_id,
                    ts,
                    ts,
                ),
            )
            is_new = True
            click_count = 1

    # متن خوش‌آمد (per لینک یا پیش‌فرض)
    welcome = (link["welcome"] or "").strip() or sget("default_welcome", "سلام! به ربات خوش آمدی.")
    if is_admin(user.id):
        await u.message.reply_text(welcome, reply_markup=admin_reply_kb())
    else:
        await u.message.reply_text(welcome)

    # اعلان به ادمین‌ها
    label = user_label(user.username, user.full_name, user.id)
    kind = "🆕 کلیک جدید" if is_new else "🔄 کلیک مجدد"
    msg = (
        f"{kind}\n"
        f"🔗 لینک: <b>{link['name']}</b>\n"
        f"👤 {label}\n"
        f"📛 اسم: {user.full_name or '—'}\n"
        f"🆔 <code>{user.id}</code>\n"
        f"📝 بیو: {bio or '—'}\n"
        f"👆 تعداد کلیک: <b>{click_count}</b>\n"
        f"🕐 {fmt_dt(ts)}"
    )
    with tx() as conn:
        admins = [r["user_id"] for r in conn.execute("SELECT user_id FROM admins").fetchall()]
    if ADMIN_ID not in admins:
        admins.append(ADMIN_ID)
    for aid in admins:
        try:
            if photo_id:
                await c.bot.send_photo(int(aid), photo_id, caption=msg, parse_mode="HTML")
            else:
                await c.bot.send_message(int(aid), msg, parse_mode="HTML")
        except Exception:
            try:
                await c.bot.send_message(int(aid), msg, parse_mode="HTML")
            except Exception:
                pass


async def show_stats(u, c):
    with tx() as conn:
        unique = conn.execute("SELECT COUNT(*) c FROM clicks").fetchone()["c"]
        total = conn.execute("SELECT COALESCE(SUM(click_count),0) s FROM clicks").fetchone()["s"]
        day_start = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        today = conn.execute(
            "SELECT COUNT(*) c FROM clicks WHERE last_at>=?", (day_start,)
        ).fetchone()["c"]
        links = conn.execute("SELECT * FROM links ORDER BY id DESC").fetchall()
    lines = [
        "📊 <b>آمار کلی</b>",
        f"👥 کاربران یکتا: <b>{unique}</b>",
        f"👆 مجموع کلیک: <b>{total}</b>",
        f"📅 فعال امروز: <b>{today}</b>",
        "",
        "🔗 به‌ازای هر لینک:",
    ]
    with tx() as conn:
        for lk in links:
            st = "🟢" if lk["active"] else "🔴"
            uq = conn.execute(
                "SELECT COUNT(*) c FROM clicks WHERE link_id=?", (lk["id"],)
            ).fetchone()["c"]
            sm = conn.execute(
                "SELECT COALESCE(SUM(click_count),0) s FROM clicks WHERE link_id=?",
                (lk["id"],),
            ).fetchone()["s"]
            lines.append(f"{st} <b>{lk['name']}</b> — یکتا {uq} | کلیک {sm}")
    await u.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=admin_reply_kb())


async def show_links(u, c):
    me = await c.bot.get_me()
    with tx() as conn:
        links = conn.execute("SELECT * FROM links ORDER BY id DESC").fetchall()
    if not links:
        await u.message.reply_text(
            "لینکی نیست. «➕ لینک جدید» را بزن.",
            reply_markup=admin_reply_kb(),
        )
        return
    for lk in links:
        url = link_url(me.username, lk["code"])
        st = "🟢 فعال" if lk["active"] else "🔴 خاموش"
        text = (
            f"🔗 <b>{lk['name']}</b>\n"
            f"{st}\n"
            f"کد: <code>{lk['code']}</code>\n"
            f"لینک:\n<code>{url}</code>"
        )
        kb = InlineKeyboardMarkup([
            [
                btn("📋 کپی لینک", f"l:copy:{lk['id']}"),
                btn("📤 خروجی", f"l:export:{lk['id']}"),
            ],
            [
                btn("👆 کلیک‌ها", f"l:clicks:{lk['id']}"),
                btn("🟢/🔴", f"l:tog:{lk['id']}"),
            ],
            [btn("🗑 حذف", f"l:del:{lk['id']}")],
        ])
        await u.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def show_clicks(u, c, link_id=None, page=0):
    limit = 8
    offset = page * limit
    with tx() as conn:
        if link_id:
            rows = conn.execute(
                "SELECT * FROM clicks WHERE link_id=? ORDER BY last_at DESC LIMIT ? OFFSET ?",
                (link_id, limit, offset),
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) c FROM clicks WHERE link_id=?", (link_id,)
            ).fetchone()["c"]
            lk = conn.execute("SELECT name FROM links WHERE id=?", (link_id,)).fetchone()
            title = lk["name"] if lk else "—"
        else:
            rows = conn.execute(
                "SELECT c.*, l.name AS link_name FROM clicks c LEFT JOIN links l ON l.id=c.link_id ORDER BY c.last_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) c FROM clicks").fetchone()["c"]
            title = "همه"
    if not rows:
        await u.message.reply_text("کلیکی ثبت نشده.", reply_markup=admin_reply_kb())
        return
    lines = [f"👆 کلیک‌ها — {title} (صفحه {page+1})\n"]
    for r in rows:
        label = user_label(r["username"], r["name"], r["user_id"])
        lname = r["link_name"] if "link_name" in r.keys() else ""
        extra = f" | 🔗 {lname}" if lname else ""
        lines.append(
            f"• {label}{extra}\n"
            f"  👆 {r['click_count']} بار | آخرین: {fmt_dt(r['last_at'])}\n"
            f"  اولین: {fmt_dt(r['first_at'])}"
        )
    kb_rows = []
    nav = []
    if page > 0:
        nav.append(btn("◀️ قبل", f"c:page:{link_id or 0}:{page-1}"))
    if offset + limit < total:
        nav.append(btn("بعد ▶️", f"c:page:{link_id or 0}:{page+1}"))
    if nav:
        kb_rows.append(nav)
    await u.message.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(kb_rows) if kb_rows else admin_reply_kb(),
    )


async def on_text(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.message.text:
        return
    user = u.effective_user
    text = u.message.text.strip()
    chat = u.effective_chat

    if not is_admin(user.id):
        # غیر ادمین فقط استارت
        return

    if chat.type != ChatType.PRIVATE:
        return

    st = get_st(c)

    # state machine
    if st:
        kind = st["kind"]
        if kind == "new_link_name":
            set_st(c, "new_link_welcome", {"name": text[:64]})
            await u.message.reply_text(
                "متن خوش‌آمد این لینک را بفرست (یا بفرست: - برای متن پیش‌فرض):",
                reply_markup=admin_reply_kb(),
            )
            return
        if kind == "new_link_welcome":
            name = st["extra"]["name"]
            welcome = "" if text.strip() == "-" else text[:1000]
            code = gen_code(10)
            with tx() as conn:
                # کد یکتا
                for _ in range(5):
                    try:
                        conn.execute(
                            "INSERT INTO links(code,name,welcome,active,created_at) VALUES (?,?,?,1,?)",
                            (code, name, welcome, now_ts()),
                        )
                        break
                    except sqlite3.IntegrityError:
                        code = gen_code(10)
            clear_st(c)
            me = await c.bot.get_me()
            url = link_url(me.username, code)
            await u.message.reply_text(
                f"✅ لینک ساخته شد\n"
                f"نام: <b>{name}</b>\n"
                f"<code>{url}</code>",
                parse_mode="HTML",
                reply_markup=admin_reply_kb(),
            )
            return
        if kind == "set_welcome":
            sset("default_welcome", text[:1000])
            clear_st(c)
            await u.message.reply_text("✅ متن پیش‌فرض ذخیره شد.", reply_markup=admin_reply_kb())
            return
        if kind == "add_admin":
            try:
                aid = int(re.sub(r"\D", "", text) or "0")
            except Exception:
                aid = 0
            if aid < 1000:
                await u.message.reply_text("آیدی نامعتبر.", reply_markup=admin_reply_kb())
                return
            with tx() as conn:
                conn.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (aid,))
            clear_st(c)
            await u.message.reply_text(f"✅ ادمین {aid} اضافه شد.", reply_markup=admin_reply_kb())
            return

    # menu
    if text in ("📊 آمار", "آمار"):
        await show_stats(u, c)
        return
    if text in ("🔗 لینک‌ها", "لینک‌ها", "لینک ها"):
        await show_links(u, c)
        return
    if text in ("👆 کلیک‌ها", "کلیک‌ها", "کلیک ها"):
        await show_clicks(u, c)
        return
    if text in ("➕ لینک جدید", "لینک جدید"):
        set_st(c, "new_link_name")
        await u.message.reply_text("نام لینک را بفرست (مثلاً: استوری۱):")
        return
    if text in ("⚙️ تنظیم متن", "تنظیم متن"):
        set_st(c, "set_welcome")
        await u.message.reply_text(
            f"متن فعلی:\n{sget('default_welcome')}\n\nمتن جدید را بفرست:"
        )
        return
    if text in ("👤 ادمین‌ها", "ادمین‌ها"):
        with tx() as conn:
            ads = conn.execute("SELECT user_id FROM admins").fetchall()
        await u.message.reply_text(
            "ادمین‌ها:\n" + "\n".join(str(a["user_id"]) for a in ads),
            reply_markup=InlineKeyboardMarkup([
                [btn("➕ افزودن ادمین", "adm:add")],
            ]),
        )
        return
    if text in ("پنل", "/admin", "admin"):
        await u.message.reply_text("🎛 پنل ادمین", reply_markup=admin_reply_kb())
        return


async def on_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    data = q.data or ""
    user = u.effective_user
    if not is_admin(user.id):
        await q.answer("دسترسی نداری", show_alert=True)
        return
    await q.answer()

    if data == "adm:add":
        set_st(c, "add_admin")
        await q.message.reply_text("آیدی عددی ادمین جدید:")
        return

    if data.startswith("l:copy:"):
        lid = int(data.split(":")[2])
        me = await c.bot.get_me()
        with tx() as conn:
            lk = conn.execute("SELECT * FROM links WHERE id=?", (lid,)).fetchone()
        if not lk:
            await q.answer("نیست", show_alert=True)
            return
        url = link_url(me.username, lk["code"])
        await q.message.reply_text(
            f"📋 لینک <b>{lk['name']}</b>:\n<code>{url}</code>",
            parse_mode="HTML",
        )
        return

    if data.startswith("l:tog:"):
        lid = int(data.split(":")[2])
        with tx() as conn:
            cur = conn.execute("SELECT active,name FROM links WHERE id=?", (lid,)).fetchone()
            if cur:
                conn.execute(
                    "UPDATE links SET active=? WHERE id=?",
                    (0 if cur["active"] else 1, lid),
                )
                st = "خاموش" if cur["active"] else "روشن"
                await q.message.reply_text(f"لینک «{cur['name']}» {st} شد.")
        return

    if data.startswith("l:del:"):
        lid = int(data.split(":")[2])
        with tx() as conn:
            conn.execute("DELETE FROM clicks WHERE link_id=?", (lid,))
            conn.execute("DELETE FROM links WHERE id=?", (lid,))
        await q.message.reply_text("لینک و کلیک‌هایش حذف شد.")
        return

    if data.startswith("l:export:"):
        lid = int(data.split(":")[2])
        with tx() as conn:
            lk = conn.execute("SELECT name FROM links WHERE id=?", (lid,)).fetchone()
            rows = conn.execute(
                "SELECT user_id,username,name,click_count,first_at,last_at,bio FROM clicks WHERE link_id=? ORDER BY last_at DESC",
                (lid,),
            ).fetchall()
        lines = [f"# خروجی لینک: {lk['name'] if lk else lid}", f"# تعداد: {len(rows)}", ""]
        for r in rows:
            label = user_label(r["username"], r["name"], r["user_id"])
            lines.append(
                f"{label} | id={r['user_id']} | clicks={r['click_count']} | "
                f"first={fmt_dt(r['first_at'])} | last={fmt_dt(r['last_at'])}"
            )
        text = "\n".join(lines)
        if len(text) < 3500:
            await q.message.reply_text(f"<code>{text}</code>", parse_mode="HTML")
        else:
            path = f"/tmp/export_{lid}.txt"
            open(path, "w", encoding="utf-8").write(text)
            await q.message.reply_document(document=open(path, "rb"), filename=f"export_{lid}.txt")
        return

    if data.startswith("l:clicks:"):
        lid = int(data.split(":")[2])
        # reuse message as trigger
        class Fake:
            message = q.message
        await show_clicks(Fake(), c, link_id=lid, page=0)
        return

    if data.startswith("c:page:"):
        parts = data.split(":")
        lid = int(parts[2])
        page = int(parts[3])
        class Fake:
            message = q.message
        await show_clicks(Fake(), c, link_id=lid if lid else None, page=page)
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

    async def safe_text(update, context):
        try:
            await on_text(update, context)
        except Exception as e:
            log.exception("text")
            try:
                await update.effective_message.reply_text("⚠️ " + str(e)[:150])
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
    app.add_handler(CallbackQueryHandler(safe_cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, safe_text))
    app.add_handler(MessageHandler(filters.COMMAND, safe_text))
    log.info("click tracker up")
    app.run_polling(allowed_updates=Update.ALL_TYPES, bootstrap_retries=10, drop_pending_updates=True)


if __name__ == "__main__":
    main()
