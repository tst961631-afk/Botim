# -*- coding: utf-8 -*-
"""
MeowPoint — بازی اقتصادی/مدیریتی تلگرام
ارز: MeowPoint (MP) | تعامل اصلی: میو
"""
from __future__ import annotations
import json, os, re, time, logging, random, asyncio, sqlite3, threading, secrets
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters,
)
from telegram.constants import ChatType
from telegram.request import HTTPXRequest

BOT_TOKEN = "8975007734:AAEkghW4tK0DeG9uOKw87Lgep8XUWlMiiLY"
ADMIN_ID = 7530457395
DB_PATH = "meowpoint.db"
TZ = timezone(timedelta(hours=3, minutes=30))
PROXY_URL = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("meowpoint")
_lock = threading.RLock()

# ═══════════════ DB ═══════════════
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
            bank INTEGER DEFAULT 0,
            bank_account TEXT DEFAULT '',
            bank_name TEXT DEFAULT '',
            level INTEGER DEFAULT 1,
            xp INTEGER DEFAULT 0,
            city_level INTEGER DEFAULT 1,
            city_xp INTEGER DEFAULT 0,
            city_treasury INTEGER DEFAULT 0,
            streak INTEGER DEFAULT 0,
            last_daily REAL DEFAULT 0,
            last_meow REAL DEFAULT 0,
            last_gather REAL DEFAULT 0,
            referrals INTEGER DEFAULT 0,
            referred_by INTEGER,
            missions_done INTEGER DEFAULT 0,
            total_earned INTEGER DEFAULT 0,
            total_spent INTEGER DEFAULT 0,
            total_transfers INTEGER DEFAULT 0,
            market_trades INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            joined_at REAL,
            last_active REAL,
            inv TEXT DEFAULT '{}',
            daily_transfer INTEGER DEFAULT 0,
            transfer_day TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS factories (
            user_id INTEGER PRIMARY KEY,
            level INTEGER DEFAULT 1,
            xp INTEGER DEFAULT 0,
            storage INTEGER DEFAULT 0,
            storage_cap INTEGER DEFAULT 1000,
            workers INTEGER DEFAULT 1,
            workers_max INTEGER DEFAULT 3,
            workers_level INTEGER DEFAULT 1,
            machine_level INTEGER DEFAULT 1,
            prod_time INTEGER DEFAULT 120,
            prod_amount INTEGER DEFAULT 50,
            busy_until REAL DEFAULT 0,
            ready INTEGER DEFAULT 0,
            pending INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            kind TEXT,
            amount INTEGER,
            meta TEXT,
            txid TEXT,
            ts REAL
        );
        CREATE TABLE IF NOT EXISTS gift_codes (
            code TEXT PRIMARY KEY,
            amount INTEGER,
            uses_left INTEGER,
            active INTEGER DEFAULT 1,
            expires REAL
        );
        CREATE TABLE IF NOT EXISTS gift_uses (
            code TEXT, user_id INTEGER, PRIMARY KEY(code, user_id)
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS templates (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS names (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS shop (
            id TEXT PRIMARY KEY, name TEXT, price INTEGER, effect TEXT, active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS market (
            id TEXT PRIMARY KEY, name TEXT, price INTEGER, stock INTEGER DEFAULT -1
        );
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, detail TEXT, ts REAL
        );
        """)
        c.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (ADMIN_ID,))
        defaults = {
            "currency": "MeowPoint",
            "currency_short": "MP",
            "coin": "🪙",
            "entity": "پیشی",
            "meow_min": "50",
            "meow_max": "120",
            "meow_cd": "300",
            "meow_cd_min": "120",
            "meow_cd_per_city": "12",
            "start_mp": "5000",
            "transfer_tax": "0.02",
            "transfer_max": "0",
            "transfer_daily": "0",
            "bank_interest": "3",
            "bank_interest_hour": "6",
            "bank_cap": "0",
            "gather_cd": "900",
            "city_xp_meow": "1",
        }
        for k, v in defaults.items():
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (k, v))
        names = {
            "entity": "پیشی", "currency": "MeowPoint", "bank": "بانک میویی",
            "factory": "کارخانه میویی", "city": "شهر میویی", "market": "بازار",
            "meow_cmd": "میو",
        }
        for k, v in names.items():
            c.execute("INSERT OR IGNORE INTO names(key,value) VALUES (?,?)", (k, v))
        tpls = {
            "meow_ok": "{amount} میو پوینت گرفتی 🐾\n💰 میو پوینت هات : {wallet} {coin}\n⏳ بعد از {cd} میتونی دوباره میو میو کنی",
            "meow_wait": "🐱 هنوز میوت نمیاد..\n⏳ باید {left} صبر کنی",
            "gather_wait": "🐱 ماهیا هنوز خوابن..\n⏳ باید {left} صبر کنی",
        }
        for k, v in tpls.items():
            c.execute("INSERT OR IGNORE INTO templates(key,value) VALUES (?,?)", (k, v))
        shop = [
            ("boost_meow", "شتاب میو", 25000, "cd_half:1"),
            ("food", "خوراک پیشی", 8000, "belly:12"),
            ("xp_pack", "بسته XP", 15000, "xp:30"),
        ]
        for s in shop:
            c.execute("INSERT OR IGNORE INTO shop(id,name,price,effect,active) VALUES (?,?,?,?,1)", s)
        market = [
            ("wood", "چوب", 100, -1),
            ("metal", "فلز", 250, -1),
            ("food_m", "غذا", 80, -1),
            ("energy", "انرژی", 300, -1),
        ]
        for m in market:
            c.execute("INSERT OR IGNORE INTO market(id,name,price,stock) VALUES (?,?,?,?)", m)

def sget(k, d=None):
    with tx() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
        return r["value"] if r else d

def sset(k, v):
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)", (k, str(v)))

def sint(k, d=0):
    try: return int(float(sget(k, d)))
    except: return int(d)

def sfloat(k, d=0.0):
    try: return float(sget(k, d))
    except: return float(d)

def nget(k, d=""):
    with tx() as c:
        r = c.execute("SELECT value FROM names WHERE key=?", (k,)).fetchone()
        return r["value"] if r else d

def tget(k, d=""):
    with tx() as c:
        r = c.execute("SELECT value FROM templates WHERE key=?", (k,)).fetchone()
        return r["value"] if r else d

def is_admin(uid):
    with tx() as c:
        return bool(c.execute("SELECT 1 FROM admins WHERE user_id=?", (int(uid),)).fetchone()) or int(uid)==ADMIN_ID

def num(n):
    try: return f"{int(n):,}"
    except: return str(n)

def parse_amt(text):
    t = (text or "").strip().replace(",", "").replace(" ", "").replace("،", "")
    t = t.replace("کا", "k").replace("ک", "k").replace("م", "m").replace("ب", "b")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kmbKMB])?", t, re.I)
    if not m: return None
    v = float(m.group(1))
    s = (m.group(2) or "").lower()
    mul = {"":1,"k":1_000,"m":1_000_000,"b":1_000_000_000}.get(s)
    return int(v*mul) if mul is not None else None

def to_roman(n):
    n = int(n)
    if n <= 0: return "0"
    vals = [(1000,"M"),(900,"CM"),(500,"D"),(400,"CD"),(100,"C"),(90,"XC"),(50,"L"),(40,"XL"),(10,"X"),(9,"IX"),(5,"V"),(4,"IV"),(1,"I")]
    o = []
    for v,s in vals:
        while n >= v:
            o.append(s); n -= v
    return "".join(o)

def mono(s): return f"<code>{s}</code>"

def fancy(name):
    out = []
    for ch in str(name or ""):
        o = ord(ch)
        if 65 <= o <= 90: out.append(chr(0x1D5D4+(o-65)))
        elif 97 <= o <= 122: out.append(chr(0x1D5EE+(o-97)))
        elif 48 <= o <= 57: out.append(chr(0x1D7EC+(o-48)))
        else: out.append(ch)
    return "".join(out) or "—"

def bar(cur, need, w=10):
    need = max(1, int(need)); cur = max(0, min(int(cur), need))
    f = int(w * cur / need)
    return "▰"*f + "▱"*(w-f)

def fmt_time(sec):
    sec = int(max(0, sec))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h: return f"{h}:{m:02d}:{s:02d}" if False else f"{h}س {m}د"
    return f"{m}:{s:02d}" if m else f"{s}ث"

def btn(text, data, style=None):
    kw = {"text": str(text)[:64], "callback_data": data}
    if style in ("success", "danger", "primary"):
        kw["style"] = style
    try:
        return InlineKeyboardButton(**kw)
    except TypeError:
        kw.pop("style", None)
        return InlineKeyboardButton(**kw)

def mention(uid, name=None):
    if not name:
        u = get_user(uid)
        name = u["name"] if u else str(uid)
    return f'<a href="tg://user?id={uid}">{name}</a>'

def txid():
    return "TX-" + secrets.token_hex(4).upper()

def log_ev(kind, detail):
    with tx() as c:
        c.execute("INSERT INTO logs(kind,detail,ts) VALUES (?,?,?)", (kind, str(detail)[:400], time.time()))

def add_tx(uid, kind, amount, meta=""):
    tid = txid()
    with tx() as c:
        c.execute("INSERT INTO transactions(user_id,kind,amount,meta,txid,ts) VALUES (?,?,?,?,?,?)",
                  (int(uid), kind, int(amount), str(meta)[:200], tid, time.time()))
    return tid

def ensure_user(user):
    if not user or getattr(user, "is_bot", False): return
    now = time.time()
    with tx() as c:
        r = c.execute("SELECT id FROM users WHERE id=?", (user.id,)).fetchone()
        if not r:
            start = 5000
            try:
                row = c.execute("SELECT value FROM settings WHERE key='start_mp'").fetchone()
                if row: start = int(float(row["value"]))
            except Exception: pass
            acc = str(100000000000 + (user.id % 899999999999))
            c.execute("""INSERT INTO users(id,username,name,wallet,bank_account,bank_name,joined_at,last_active,total_earned)
                         VALUES (?,?,?,?,?,?,?,?,?)""",
                      (user.id, user.username or "", user.full_name or str(user.id), start, acc,
                       user.full_name or str(user.id), now, now, start))
            c.execute("INSERT OR IGNORE INTO factories(user_id) VALUES (?)", (user.id,))
            c.execute("INSERT INTO transactions(user_id,kind,amount,meta,txid,ts) VALUES (?,?,?,?,?,?)",
                      (user.id, "start", start, "welcome", txid(), now))
        else:
            c.execute("UPDATE users SET username=?, name=?, last_active=? WHERE id=?",
                      (user.username or "", user.full_name or str(user.id), now, user.id))

def get_user(uid):
    with tx() as c:
        return c.execute("SELECT * FROM users WHERE id=?", (int(uid),)).fetchone()

def get_factory(uid):
    with tx() as c:
        f = c.execute("SELECT * FROM factories WHERE user_id=?", (int(uid),)).fetchone()
        if not f:
            c.execute("INSERT INTO factories(user_id) VALUES (?)", (int(uid),))
            f = c.execute("SELECT * FROM factories WHERE user_id=?", (int(uid),)).fetchone()
        return f

def change_wallet(uid, delta, kind, meta=""):
    with tx() as c:
        r = c.execute("SELECT wallet,status FROM users WHERE id=?", (int(uid),)).fetchone()
        if not r: raise ValueError("کاربر نیست")
        if r["status"] != "active": raise ValueError("مسدود")
        new = int(r["wallet"]) + int(delta)
        if new < 0: raise ValueError("موجودی کم")
        c.execute("UPDATE users SET wallet=? WHERE id=?", (new, int(uid)))
        if delta > 0:
            c.execute("UPDATE users SET total_earned = total_earned + ? WHERE id=?", (int(delta), int(uid)))
        else:
            c.execute("UPDATE users SET total_spent = total_spent + ? WHERE id=?", (abs(int(delta)), int(uid)))
        tid = txid()
        c.execute("INSERT INTO transactions(user_id,kind,amount,meta,txid,ts) VALUES (?,?,?,?,?,?)",
                  (int(uid), kind, int(delta), str(meta)[:200], tid, time.time()))
        return new, tid

def add_xp(uid, amount):
    with tx() as c:
        c.execute("UPDATE users SET xp = xp + ? WHERE id=?", (int(amount), int(uid)))
        u = c.execute("SELECT xp,level FROM users WHERE id=?", (int(uid),)).fetchone()
        xp, lv = int(u["xp"]), int(u["level"])
        need = 100 * lv
        while xp >= need:
            xp -= need
            lv += 1
            need = 100 * lv
        c.execute("UPDATE users SET xp=?, level=? WHERE id=?", (xp, lv, int(uid)))

def meow_cd(uid):
    cd = sint("meow_cd", 300)
    mn = sint("meow_cd_min", 120)
    u = get_user(uid)
    reduce = (int(u["city_level"]) - 1) * sint("meow_cd_per_city", 12) if u else 0
    return max(mn, cd - reduce)

def render(tpl_key, **kw):
    text = tget(tpl_key, "")
    coin = sget("coin", "🪙")
    kw.setdefault("coin", coin)
    for k, v in kw.items():
        text = text.replace("{"+k+"}", str(v))
    return text

def set_st(ctx, kind, extra=None):
    ctx.user_data["st"] = {"kind": kind, "extra": extra or {}, "ts": time.time()}

def get_st(ctx):
    st = ctx.user_data.get("st")
    if not st: return None
    if time.time() - st.get("ts", 0) > 900:
        ctx.user_data.pop("st", None); return None
    return st

def clear_st(ctx):
    ctx.user_data.pop("st", None)

# ═══════════════ PANELS ═══════════════
def profile_text(u):
    coin = sget("coin", "🪙")
    with tx() as c:
        rank = c.execute("SELECT COUNT(*)+1 AS r FROM users WHERE wallet+bank > ?",
                         (int(u["wallet"])+int(u["bank"]),)).fetchone()["r"]
        fc = c.execute("SELECT level FROM factories WHERE user_id=?", (u["id"],)).fetchone()
    flv = fc["level"] if fc else 1
    joined = datetime.fromtimestamp(float(u["joined_at"] or time.time()), TZ).strftime("%Y/%m/%d")
    lines = [
        "PROFILE // " + (u["name"] or "").upper(),
        "",
        f"NAME     : {u['name']}",
        f"LEVEL    : {u['level']}",
        f"CITY     : {to_roman(u['city_level'])}",
        f"XP       : {num(u['xp'])}",
        f"WALLET   : {num(u['wallet'])}",
        f"BANK     : {num(u['bank'])}",
        f"FACTORY  : {flv}",
        f"RANK     : #{rank}",
        f"STREAK   : {u['streak']}",
        f"REFERS   : {u['referrals']}",
        f"JOINED   : {joined}",
    ]
    return mono("\n".join(lines))

def entity_panel(u, fac):
    entity = nget("entity", "پیشی")
    coin = sget("coin", "🪙")
    inv = {}
    try: inv = json.loads(u["inv"] or "{}")
    except: pass
    belly = int(inv.get("belly", 12))
    ranks = {1:"تازه‌کار",2:"چابک",3:"ماهر",4:"ابر پیشی",5:"افسانه"}
    rl = min(5, max(1, int(u["level"])//5+1))
    prod = max(1, int(u["level"]) + int(fac["machine_level"]))
    up = 50000 * int(u["level"])
    return "\n".join([
        f"🐱 {entity} <b>{u['name']}</b> 🐈",
        "",
        f"💕 نام : {entity}",
        f"🍖 شکم : {'😻 عاشقتمیووو' if belly>=10 else '😊 سیر' if belly>=5 else '😩 گرسنه'} ({belly} / 12)",
        "",
        f"🌟 مقام : {ranks.get(rl,'پیشی')} ⚡️ ({rl})",
        f"⭐️ سطح : {u['level']} / 50",
        "",
        f"💰 میو پوینت تولید شده : {num(u['total_earned'])} {coin}",
        f"💫 تولید در ثانیه : {prod} {coin}",
        f"📦 ظرفیت کارخانه : {num(fac['storage_cap'])}",
        "",
        f"💰 هزینه ارتقا مقام : {num(up)} {coin}",
    ])

def bank_panel(u):
    coin = sget("coin", "🪙")
    pct = sget("bank_interest", "3")
    now = datetime.now(TZ)
    hour = int(sget("bank_interest_hour", "6") or 6)
    nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if nxt <= now: nxt += timedelta(days=1)
    tstr = nxt.strftime("%H:%M %Y/%m/%d")
    dep = min(int(u["bank"]), 500000) if int(u["bank"]) > 0 else 0
    return "\n".join([
        f"🐱 {nget('bank','بانک میویی')} 🏦",
        "",
        f"💳 شماره حساب : {mono(str(u['bank_account'] or u['id']))}",
        f"👤 به نام : <b>{u['bank_name'] or u['name']}</b>",
        "",
        f"💰 موجودی حساب : <b>{num(u['bank'])}</b> {coin}",
        f"💵 کیف پول : <b>{num(u['wallet'])}</b> {coin}",
        "",
        "🤑 سود بانکی",
        f"┘─ 🛍 درصد سود : {pct}%",
        f"┘─ 📥 مبلغ مشمول : {num(dep)} {coin}",
        f"┘─ ⏳ زمان واریز : {tstr}",
        "",
        "❗️ برای مدیریت حساب بانکی از گزینه‌های زیر استفاده کنید ⬇️",
    ])

def factory_panel(name, f):
    coin = sget("coin", "🪙")
    lv = int(f["level"]); xp = int(f["xp"]); need = 1000 * lv
    return "\n".join([
        f"🐱 {nget('factory','کارخانه میویی')} 🏭",
        "",
        f"💼 مدیر کارخانه : <b>{name}</b>",
        "",
        "🧳 انبار کارخانه",
        f"┘─ 🔺 ظرفیت انبار : {num(f['storage'])} / {num(f['storage_cap'])} محصول",
        f"┘─ ⭐️ سطح : {lv}",
        "",
        "🐈 کارگران کارخانه",
        f"┘─ 😺 تعداد کارگران : {f['workers']} / {f['workers_max']} پیشی",
        f"┘─ ⭐️ سطح : {f['workers_level']}",
        "",
        "🖨 دستگاه‌های تولید",
        f"┘─ ⏳ زمان تولید محصول : {f['prod_time']} ثانیه",
        f"┘─ ⭐️ سطح : {f['machine_level']}",
        "",
        f"🌟 سطح کارخانه : {mono(to_roman(lv))}",
        f"┘─ 🌡 {num(xp)} XP / {num(need)} XP {bar(xp, need)}",
        "",
        "🧮 شما در حال مدیریت کارخانه خود می‌باشید.",
    ])

def city_panel(u):
    coin = sget("coin", "🪙")
    lv = int(u["city_level"])
    need_xp = 100 * lv
    need_mp = 5000 * lv
    need_fac = max(1, lv // 2)
    with tx() as c:
        fl = c.execute("SELECT level FROM factories WHERE user_id=?", (u["id"],)).fetchone()
    flv = int(fl["level"]) if fl else 1
    return "\n".join([
        f"🐱 {nget('city','شهر میویی')} <b>{u['name']}</b> 🏰",
        f"{mono('CITY')}",
        "",
        f"🦁 شهردار : <b>{fancy(u['name'])}</b>",
        "",
        f"⭐️ سطح شهر : {mono(to_roman(lv))}",
        f"✨ XP شهر : {num(u['city_xp'])} / {num(need_xp)}",
        f"🏦 خزانه : {num(u['city_treasury'])} {coin}",
        "",
        "⏫ باف های شهر ⬇️",
        "┘─ باف فعال ثبت نشده ❌",
        "",
        "🎯 هدف ارتقا سطح بعدی ⬇️",
        f"┘─ ✨ XP : {num(u['city_xp'])} / {num(need_xp)}",
        f"┘─ 🪙 MP : {num(u['wallet'])} / {num(need_mp)}",
        f"┘─ 🏭 کارخانه سطح : {flv} / {need_fac}",
    ])

def help_text():
    e = nget("entity", "پیشی")
    return f"""📖 <b>راهنمای MeowPoint</b>
━━━━━━━━━━━━━━━━
🪙 <b>ارز:</b> MeowPoint (MP)

🐱 <b>{e}</b>
دستور: <code>{e}</code> یا <code>پیشی</code>
پنل شخصی، سطح، شکم، ارتقا

🐾 <b>میو</b>
دستور: <code>میو</code>
دریافت MP با کول‌داون (وابسته به سطح شهر)

🏙 <b>شهر</b>
دستور: <code>شهر</code>
سطح رومی، خزانه، شرایط ارتقا

🏭 <b>کارخانه</b>
دستور: <code>کارخانه</code>
تولید زمان‌دار، انبار، کارگر، ارتقا

🏦 <b>بانک</b>
دستور: <code>بانک</code>
واریز / برداشت / انتقال / تاریخچه / شماره حساب

🛒 <b>بازار</b>
دستور: <code>بازار</code>
خرید کالا و منابع

🎣 <b>جمع‌آوری</b>
دستور: <code>جمع آوری</code> یا <code>شکار</code>

🎁 <b>کد هدیه</b>
دستور: <code>کد</code>

👥 <b>دعوت</b>
دستور: <code>دعوت</code>

🏆 <b>رتبه</b>
دستور: <code>رتبه</code> یا <code>رتبه بندی</code>

👤 <b>پروفایل</b>
دستور: <code>پروفایل</code> (حالت MONO)

💸 <b>انتقال</b>
<code>انتقال 10k</code> + ریپلای

ادمین: <code>پنل</code> (فقط پیوی)
"""

def main_kb(uid):
    e = nget("entity", "پیشی")
    return InlineKeyboardMarkup([
        [btn(f"🐱 {e}", f"p:entity:{uid}", "primary"), btn("🐾 میو", f"p:meow:{uid}", "success")],
        [btn("🏦 بانک", f"p:bank:{uid}", "primary"), btn("🏭 کارخانه", f"p:fac:{uid}", "primary")],
        [btn("🏙 شهر", f"p:city:{uid}", "primary"), btn("🛒 بازار", f"p:shop:{uid}", "primary")],
        [btn("🎣 جمع‌آوری", f"p:gather:{uid}", "primary"), btn("🎁 کد", f"p:gift:{uid}", "success")],
        [btn("🏆 رتبه", f"p:lb:{uid}", "primary"), btn("👤 پروفایل", f"p:prof:{uid}", "primary")],
        [btn("📖 راهنما", f"p:help:{uid}", "primary")],
    ])

def own(data, uid):
    parts = data.split(":")
    if len(parts) >= 3:
        try:
            return int(parts[-1]) == int(uid)
        except: return False
    return True

def admin_kb():
    return InlineKeyboardMarkup([
        [btn("👥 لیست کاربران", "a:ulist", "primary")],
        [btn("➕ واریز", "a:add", "success"), btn("➖ کسر", "a:sub", "danger")],
        [btn("⚙️ تنظیم میو", "a:meow", "primary"), btn("🏦 تنظیم بانک", "a:bank", "primary")],
        [btn("📝 قالب‌ها", "a:tpl", "primary"), btn("🏷 نام‌ها", "a:names", "primary")],
        [btn("🎁 کد هدیه", "a:code", "success"), btn("📢 همگانی", "a:bcast", "primary")],
        [btn("📊 آمار", "a:stats", "primary"), btn("👤 ادمین+", "a:admins", "primary")],
        [btn("❌ بستن", "a:close", "danger")],
    ])

# ═══════════════ HANDLERS ═══════════════
async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    user = u.effective_user
    ensure_user(user)
    text = u.message.text or ""
    if "ref" in text:
        try:
            ref = int(re.search(r"ref(\d+)", text).group(1))
            if ref != user.id:
                uu = get_user(user.id)
                if uu and not uu["referred_by"]:
                    with tx() as conn:
                        conn.execute("UPDATE users SET referred_by=? WHERE id=? AND referred_by IS NULL", (ref, user.id))
                        conn.execute("UPDATE users SET referrals = referrals + 1 WHERE id=?", (ref,))
                    try:
                        change_wallet(user.id, 2000, "ref_bonus", str(ref))
                        change_wallet(ref, 5000, "ref_reward", str(user.id))
                    except Exception: pass
        except Exception: pass
    uu = get_user(user.id)
    await u.message.reply_text(
        f"🐱 به <b>MeowPoint</b> خوش آمدی!\n"
        f"موجودی: <b>{num(uu['wallet'])}</b> {sget('coin','🪙')}\n\n"
        f"بنویس <code>{nget('entity','پیشی')}</code> یا از منو استفاده کن.",
        parse_mode="HTML",
        reply_markup=main_kb(user.id) if u.effective_chat.type == ChatType.PRIVATE else None,
    )

async def do_meow(bot, chat_id, user, message=None, edit=False):
    ensure_user(user)
    u = get_user(user.id)
    if u["status"] != "active":
        return "مسدود هستی"
    cd = meow_cd(user.id)
    left = float(u["last_meow"] or 0) + cd - time.time()
    if left > 0:
        return render("meow_wait", left=fmt_time(left))
    amin, amax = sint("meow_min", 50), sint("meow_max", 120)
    amount = random.randint(min(amin, amax), max(amin, amax))
    amount += (int(u["city_level"]) - 1) * 10
    new, _ = change_wallet(user.id, amount, "meow", "")
    add_xp(user.id, 2)
    with tx() as c:
        c.execute("UPDATE users SET last_meow=?, city_xp = city_xp + ? WHERE id=?",
                  (time.time(), sint("city_xp_meow", 1), user.id))
        # city level up
        uu = c.execute("SELECT city_level, city_xp FROM users WHERE id=?", (user.id,)).fetchone()
        need = 100 * int(uu["city_level"])
        if int(uu["city_xp"]) >= need:
            c.execute("UPDATE users SET city_level = city_level + 1, city_xp = city_xp - ? WHERE id=?",
                      (need, user.id))
    return render("meow_ok", amount=num(amount), wallet=num(new), cd=fmt_time(cd))

async def on_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    data = q.data or ""
    user = u.effective_user
    ensure_user(user)
    chat = u.effective_chat

    if data == "a:close":
        await q.answer()
        try: await q.message.delete()
        except: pass
        return

    # ownership for player panels
    if data.startswith("p:") and not own(data, user.id):
        await q.answer("این پنل برای تو نیست", show_alert=True)
        return

    await q.answer()

    if data.startswith("p:help"):
        await q.edit_message_text(help_text(), parse_mode="HTML",
                                  reply_markup=InlineKeyboardMarkup([[btn("🔙", f"p:home:{user.id}", "primary")]]))
        return
    if data.startswith("p:home"):
        await q.edit_message_text("🐱 منوی MeowPoint", reply_markup=main_kb(user.id))
        return
    if data.startswith("p:meow"):
        msg = await do_meow(c.bot, chat.id, user)
        await q.edit_message_text(msg, parse_mode="HTML",
                                  reply_markup=InlineKeyboardMarkup([[btn("🔙", f"p:home:{user.id}", "primary")]]))
        return
    if data.startswith("p:entity"):
        uu = get_user(user.id); fac = get_factory(user.id)
        kb = InlineKeyboardMarkup([
            [btn("⬆️ ارتقا مقام", f"p:up:{user.id}", "success")],
            [btn("🔙", f"p:home:{user.id}", "primary")],
        ])
        await q.edit_message_text(entity_panel(uu, fac), parse_mode="HTML", reply_markup=kb)
        return
    if data.startswith("p:up:"):
        uu = get_user(user.id)
        cost = 50000 * int(uu["level"])
        try:
            change_wallet(user.id, -cost, "level_up", "")
            add_xp(user.id, 50)
            with tx() as conn:
                conn.execute("UPDATE users SET level = level + 1 WHERE id=?", (user.id,))
        except ValueError as e:
            await q.answer(str(e), show_alert=True); return
        uu = get_user(user.id); fac = get_factory(user.id)
        await q.edit_message_text("✅ ارتقا یافت!\n" + entity_panel(uu, fac), parse_mode="HTML",
                                  reply_markup=InlineKeyboardMarkup([[btn("🔙", f"p:home:{user.id}")]]))
        return
    if data.startswith("p:bank"):
        uu = get_user(user.id)
        kb = InlineKeyboardMarkup([
            [btn("📥 واریز", f"p:bin:{user.id}", "success"), btn("📤 برداشت", f"p:bout:{user.id}", "danger")],
            [btn("💸 انتقال", f"p:btr:{user.id}", "primary"), btn("📜 تاریخچه", f"p:blog:{user.id}", "primary")],
            [btn("💳 تغییر حساب", f"p:bacc:{user.id}", "primary")],
            [btn("🔙", f"p:home:{user.id}", "primary")],
        ])
        await q.edit_message_text(bank_panel(uu), parse_mode="HTML", reply_markup=kb)
        return
    if data.startswith("p:bin:"):
        set_st(c, "bank_in"); await q.edit_message_text("مبلغ واریز از کیف به بانک را بفرست:")
        return
    if data.startswith("p:bout:"):
        set_st(c, "bank_out"); await q.edit_message_text("مبلغ برداشت از بانک به کیف را بفرست:")
        return
    if data.startswith("p:btr:"):
        set_st(c, "bank_tr"); await q.edit_message_text("فرمت:\n<code>شماره_حساب مبلغ</code>", parse_mode="HTML")
        return
    if data.startswith("p:bacc:"):
        set_st(c, "bank_acc"); await q.edit_message_text("شماره حساب جدید (عدد):")
        return
    if data.startswith("p:blog:"):
        with tx() as conn:
            rows = conn.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 12", (user.id,)).fetchall()
        lines = ["📜 تاریخچه\n"] + [f"• {r['kind']}: {num(r['amount'])} | {r['txid']}" for r in rows]
        await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup([[btn("🔙", f"p:bank:{user.id}")]]))
        return
    if data.startswith("p:fac"):
        fac = dict(get_factory(user.id))
        now = time.time()
        extra = ""
        if float(fac["busy_until"] or 0) > now:
            extra = f"\n\n⏳ تولید: {fmt_time(float(fac['busy_until'])-now)}"
        elif int(fac.get("ready") or 0):
            extra = f"\n\n✅ محصول آماده: {num(fac.get('pending') or 0)}"
        kb = InlineKeyboardMarkup([
            [btn("▶️ تولید / برداشت", f"p:fgo:{user.id}", "success")],
            [btn("⬆️ ارتقا کارخانه", f"p:fup:{user.id}", "primary")],
            [btn("🔙", f"p:home:{user.id}", "primary")],
        ])
        await q.edit_message_text(factory_panel(user.full_name, fac)+extra, parse_mode="HTML", reply_markup=kb)
        return
    if data.startswith("p:fgo:"):
        fac = dict(get_factory(user.id)); now = time.time()
        if float(fac["busy_until"] or 0) > now:
            await q.answer("هنوز تولید تمام نشده", show_alert=True); return
        if int(fac.get("ready") or 0):
            pend = int(fac.get("pending") or 0)
            if int(fac["storage"]) + pend > int(fac["storage_cap"]):
                await q.answer("انبار پر است", show_alert=True); return
            with tx() as conn:
                conn.execute("UPDATE factories SET storage = storage + ?, ready=0, pending=0, xp = xp + 20 WHERE user_id=?",
                             (pend, user.id))
            try:
                gain = pend * 10
                change_wallet(user.id, gain, "factory_sell", "")
                await q.edit_message_text(f"🏭 برداشت و فروش: +{num(gain)} MP", reply_markup=InlineKeyboardMarkup([[btn("🔙", f"p:fac:{user.id}")]]))
            except Exception as e:
                await q.answer(str(e), show_alert=True)
            return
        tsec = int(fac["prod_time"]); amt = int(fac["prod_amount"]) * int(fac["workers"])
        with tx() as conn:
            conn.execute("UPDATE factories SET busy_until=?, ready=1, pending=? WHERE user_id=?",
                         (now+tsec, amt, user.id))
        await q.edit_message_text(f"🏭 تولید شروع شد — {fmt_time(tsec)}\nمحصول: {num(amt)}",
                                  reply_markup=InlineKeyboardMarkup([[btn("🔙", f"p:fac:{user.id}")]]))
        return
    if data.startswith("p:fup:"):
        fac = get_factory(user.id)
        cost = 30000 * int(fac["level"])
        try: change_wallet(user.id, -cost, "fac_up", "")
        except ValueError as e:
            await q.answer(str(e), show_alert=True); return
        with tx() as conn:
            conn.execute("""UPDATE factories SET level=level+1, storage_cap=storage_cap+500,
                workers_max=workers_max+1, machine_level=machine_level+1,
                prod_time=MAX(10,prod_time-5), prod_amount=prod_amount+10 WHERE user_id=?""", (user.id,))
        await q.edit_message_text("✅ کارخانه ارتقا یافت", reply_markup=InlineKeyboardMarkup([[btn("🔙", f"p:fac:{user.id}")]]))
        return
    if data.startswith("p:city"):
        uu = get_user(user.id)
        kb = InlineKeyboardMarkup([
            [btn("⬆️ ارتقا شهر", f"p:cup:{user.id}", "success")],
            [btn("🕊 دونیت خزانه", f"p:cdon:{user.id}", "primary")],
            [btn("🔙", f"p:home:{user.id}", "primary")],
        ])
        await q.edit_message_text(city_panel(uu), parse_mode="HTML", reply_markup=kb)
        return
    if data.startswith("p:cup:"):
        uu = get_user(user.id)
        lv = int(uu["city_level"]); need_xp = 100*lv; need_mp = 5000*lv
        fac = get_factory(user.id)
        if int(uu["city_xp"]) < need_xp or int(uu["wallet"]) < need_mp or int(fac["level"]) < max(1, lv//2):
            await q.answer("شرایط ارتقا کامل نیست", show_alert=True); return
        try: change_wallet(user.id, -need_mp, "city_up", "")
        except ValueError as e:
            await q.answer(str(e), show_alert=True); return
        with tx() as conn:
            conn.execute("UPDATE users SET city_level=city_level+1, city_xp=city_xp-? WHERE id=?", (need_xp, user.id))
        await q.edit_message_text(f"🏙 شهر ارتقا یافت → {mono(to_roman(lv+1))}", parse_mode="HTML",
                                  reply_markup=InlineKeyboardMarkup([[btn("🔙", f"p:city:{user.id}")]]))
        return
    if data.startswith("p:cdon:"):
        set_st(c, "city_don"); await q.edit_message_text("مبلغ دونیت به خزانه شهر:")
        return
    if data.startswith("p:shop"):
        with tx() as conn:
            items = conn.execute("SELECT * FROM shop WHERE active=1").fetchall()
            mkt = conn.execute("SELECT * FROM market").fetchall()
        lines = ["🛒 فروشگاه / بازار\n"]
        rows = []
        for it in items:
            lines.append(f"• {it['name']} — {num(it['price'])}")
            rows.append([btn(f"{it['name']}|{num(it['price'])}", f"p:buy:{it['id']}:{user.id}", "success")])
        for m in mkt:
            lines.append(f"• {m['name']} (بازار) — {num(m['price'])}")
            rows.append([btn(f"{m['name']}|{num(m['price'])}", f"p:mbuy:{m['id']}:{user.id}", "primary")])
        rows.append([btn("🔙", f"p:home:{user.id}", "primary")])
        await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))
        return
    if data.startswith("p:buy:"):
        parts = data.split(":")
        iid, owner = parts[2], int(parts[3])
        if user.id != owner: return
        with tx() as conn:
            it = conn.execute("SELECT * FROM shop WHERE id=?", (iid,)).fetchone()
        if not it: return
        try: change_wallet(user.id, -int(it["price"]), "shop", iid)
        except ValueError as e:
            await q.answer(str(e), show_alert=True); return
        inv = {}
        with tx() as conn:
            inv = json.loads(conn.execute("SELECT inv FROM users WHERE id=?", (user.id,)).fetchone()["inv"] or "{}")
            eff = it["effect"] or ""
            if eff.startswith("cd_half"): inv["cd_half"] = int(inv.get("cd_half") or 0)+1
            elif eff.startswith("belly"): inv["belly"] = 12
            elif eff.startswith("xp:"): add_xp(user.id, int(eff.split(":")[1]))
            conn.execute("UPDATE users SET inv=? WHERE id=?", (json.dumps(inv), user.id))
        await q.edit_message_text(f"✅ خرید {it['name']}", reply_markup=InlineKeyboardMarkup([[btn("🔙", f"p:shop:{user.id}")]]))
        return
    if data.startswith("p:mbuy:"):
        parts = data.split(":")
        mid, owner = parts[2], int(parts[3])
        if user.id != owner: return
        with tx() as conn:
            m = conn.execute("SELECT * FROM market WHERE id=?", (mid,)).fetchone()
        if not m: return
        try: change_wallet(user.id, -int(m["price"]), "market", mid)
        except ValueError as e:
            await q.answer(str(e), show_alert=True); return
        with tx() as conn:
            inv = json.loads(conn.execute("SELECT inv FROM users WHERE id=?", (user.id,)).fetchone()["inv"] or "{}")
            inv[mid] = int(inv.get(mid) or 0) + 1
            conn.execute("UPDATE users SET inv=?, market_trades=market_trades+1 WHERE id=?", (json.dumps(inv), user.id))
        await q.edit_message_text(f"✅ خرید {m['name']}", reply_markup=InlineKeyboardMarkup([[btn("🔙", f"p:shop:{user.id}")]]))
        return
    if data.startswith("p:gather"):
        u = get_user(user.id)
        cd = sint("gather_cd", 900)
        left = float(u["last_gather"] or 0) + cd - time.time()
        if left > 0:
            await q.edit_message_text(render("gather_wait", left=fmt_time(left)), parse_mode="HTML",
                                      reply_markup=InlineKeyboardMarkup([[btn("🔙", f"p:home:{user.id}")]]))
            return
        with tx() as conn:
            conn.execute("UPDATE users SET last_gather=? WHERE id=?", (time.time(), user.id))
        if random.random() < 0.3:
            await q.edit_message_text("🌫️ چیزی پیدا نشد...", reply_markup=InlineKeyboardMarkup([[btn("🔙", f"p:home:{user.id}")]]))
            return
        gain = random.randint(30, 150)
        change_wallet(user.id, gain, "gather", "")
        kb = InlineKeyboardMarkup([
            [btn("💰 فروش", f"p:home:{user.id}", "success"), btn("🍖 به پیشی بده", f"p:feed:{user.id}", "primary")],
            [btn("🔙", f"p:home:{user.id}", "primary")],
        ])
        await q.edit_message_text(f"🎣 شکار موفق!\n🪙 +{num(gain)}", reply_markup=kb)
        return
    if data.startswith("p:feed:"):
        with tx() as conn:
            inv = json.loads(conn.execute("SELECT inv FROM users WHERE id=?", (user.id,)).fetchone()["inv"] or "{}")
            inv["belly"] = 12
            conn.execute("UPDATE users SET inv=? WHERE id=?", (json.dumps(inv), user.id))
        await q.edit_message_text("🍖 پیشی سیر شد!", reply_markup=InlineKeyboardMarkup([[btn("🔙", f"p:home:{user.id}")]]))
        return
    if data.startswith("p:gift"):
        set_st(c, "gift"); await q.edit_message_text("🎁 کد را بفرست:")
        return
    if data.startswith("p:lb"):
        with tx() as conn:
            rows = conn.execute("SELECT id,name,wallet,bank,level FROM users ORDER BY wallet+bank DESC LIMIT 10").fetchall()
        lines = ["🏆 رتبه‌بندی ثروت\n"]
        for i, r in enumerate(rows, 1):
            lines.append(f"{mono(to_roman(i))} {mention(r['id'], r['name'])}")
            lines.append(f"    🪙 {num(int(r['wallet'])+int(r['bank']))} · Lv {r['level']}\n")
        await q.edit_message_text("\n".join(lines), parse_mode="HTML",
                                  reply_markup=InlineKeyboardMarkup([[btn("🔙", f"p:home:{user.id}")]]))
        return
    if data.startswith("p:prof"):
        await q.edit_message_text(profile_text(get_user(user.id)), parse_mode="HTML",
                                  reply_markup=InlineKeyboardMarkup([[btn("🔙", f"p:home:{user.id}")]]))
        return

    # Admin
    if data.startswith("a:") and not is_admin(user.id):
        await q.answer("دسترسی نداری", show_alert=True); return
    if data == "a:ulist":
        with tx() as conn:
            rows = conn.execute("SELECT id,wallet,bank,level,city_level FROM users ORDER BY wallet DESC LIMIT 150").fetchall()
        lines = ["id|wallet|bank|level|city\n"] + [f"{r['id']}|{r['wallet']}|{r['bank']}|{r['level']}|{r['city_level']}" for r in rows]
        await q.edit_message_text(mono("\n".join(lines)[:3500]), parse_mode="HTML", reply_markup=admin_kb())
        return
    if data == "a:add":
        set_st(c, "a_add"); await q.edit_message_text("آیدی مبلغ:\n<code>123 10k</code>", parse_mode="HTML"); return
    if data == "a:sub":
        set_st(c, "a_sub"); await q.edit_message_text("آیدی مبلغ کسر:", parse_mode="HTML"); return
    if data == "a:meow":
        set_st(c, "a_meow")
        await q.edit_message_text(f"min={sget('meow_min')} max={sget('meow_max')} cd={sget('meow_cd')}\n<code>min 50</code>\n<code>max 120</code>\n<code>cd 300</code>", parse_mode="HTML"); return
    if data == "a:bank":
        set_st(c, "a_bank")
        await q.edit_message_text(f"سود={sget('bank_interest')}%\n<code>سود 3</code>\n<code>مالیات 0.02</code>", parse_mode="HTML"); return
    if data == "a:tpl":
        set_st(c, "a_tpl"); await q.edit_message_text("قالب KEY متن\nکلیدها: meow_ok meow_wait gather_wait"); return
    if data == "a:names":
        set_st(c, "a_names"); await q.edit_message_text("نام KEY مقدار\nمثال: نام entity پیشی"); return
    if data == "a:code":
        set_st(c, "a_code"); await q.edit_message_text("کد مبلغ تعداد\n<code>MEOW 10k 50</code>", parse_mode="HTML"); return
    if data == "a:bcast":
        set_st(c, "a_bcast"); await q.edit_message_text("متن همگانی:"); return
    if data == "a:stats":
        with tx() as conn:
            uc = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
            tw = conn.execute("SELECT SUM(wallet)+SUM(bank) s FROM users").fetchone()["s"] or 0
        await q.edit_message_text(f"کاربران: {num(uc)}\nکل MP: {num(tw)}", reply_markup=admin_kb()); return
    if data == "a:admins":
        set_st(c, "a_admins"); await q.edit_message_text("ادمین + آیدی / ادمین - آیدی"); return

async def on_text(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.message.text: return
    user = u.effective_user
    chat = u.effective_chat
    text = u.message.text.strip()
    ensure_user(user)
    st = get_st(c)
    low = re.sub(r"^/", "", text).strip()

    # states
    if st:
        kind = st["kind"]
        if kind in ("bank_in", "bank_out") and own_check_ok(user):
            amt = parse_amt(text)
            if not amt: await u.message.reply_text("مبلغ نامعتبر"); return
            uu = get_user(user.id)
            if kind == "bank_in":
                if int(uu["wallet"]) < amt: await u.message.reply_text("کیف کم"); return
                with tx() as conn:
                    conn.execute("UPDATE users SET wallet=wallet-?, bank=bank+? WHERE id=?", (amt, amt, user.id))
                add_tx(user.id, "bank_in", -amt, "")
            else:
                if int(uu["bank"]) < amt: await u.message.reply_text("بانک کم"); return
                with tx() as conn:
                    conn.execute("UPDATE users SET wallet=wallet+?, bank=bank-? WHERE id=?", (amt, amt, user.id))
                add_tx(user.id, "bank_out", amt, "")
            clear_st(c); await u.message.reply_text("✅ انجام شد"); return
        if kind == "bank_acc":
            acc = re.sub(r"\D", "", text)
            if len(acc) < 6: await u.message.reply_text("نامعتبر"); return
            with tx() as conn:
                conn.execute("UPDATE users SET bank_account=?, bank_name=? WHERE id=?", (acc, user.full_name, user.id))
            clear_st(c); await u.message.reply_text(f"💳 {mono(acc)}", parse_mode="HTML"); return
        if kind == "bank_tr":
            parts = text.split()
            if len(parts) < 2: await u.message.reply_text("شماره مبلغ"); return
            acc, amt = parts[0], parse_amt(parts[1])
            if not amt: await u.message.reply_text("مبلغ?"); return
            with tx() as conn:
                target = conn.execute("SELECT id,name FROM users WHERE bank_account=?", (acc,)).fetchone()
            if not target: await u.message.reply_text("حساب پیدا نشد"); return
            if int(target["id"]) == user.id: await u.message.reply_text("خودت؟"); return
            uu = get_user(user.id)
            if int(uu["bank"]) < amt: await u.message.reply_text("بانک کم"); return
            tax = int(amt * sfloat("transfer_tax", 0.02)); send = amt - tax
            with tx() as conn:
                conn.execute("UPDATE users SET bank=bank-? WHERE id=?", (amt, user.id))
                conn.execute("UPDATE users SET bank=bank+? WHERE id=?", (send, int(target["id"])))
            tid = add_tx(user.id, "transfer_out", -amt, acc)
            add_tx(int(target["id"]), "transfer_in", send, str(user.id))
            clear_st(c)
            await u.message.reply_text(f"💸 انتقال {num(send)}\nبه {target['name']}\n{tid}"); return
        if kind == "city_don":
            amt = parse_amt(text)
            if not amt: await u.message.reply_text("مبلغ?"); return
            try: change_wallet(user.id, -amt, "city_don", "")
            except ValueError as e: await u.message.reply_text(str(e)); return
            with tx() as conn:
                conn.execute("UPDATE users SET city_treasury=city_treasury+? WHERE id=?", (amt, user.id))
            clear_st(c); await u.message.reply_text(f"🕊 دونیت {num(amt)}"); return
        if kind == "gift":
            code = text.strip().upper()
            with tx() as conn:
                g = conn.execute("SELECT * FROM gift_codes WHERE code=? AND active=1", (code,)).fetchone()
                if not g or int(g["uses_left"]) < 1:
                    await u.message.reply_text("کد نامعتبر"); clear_st(c); return
                if conn.execute("SELECT 1 FROM gift_uses WHERE code=? AND user_id=?", (code, user.id)).fetchone():
                    await u.message.reply_text("قبلاً زدی"); clear_st(c); return
                conn.execute("UPDATE gift_codes SET uses_left=uses_left-1 WHERE code=?", (code,))
                conn.execute("INSERT INTO gift_uses(code,user_id) VALUES (?,?)", (code, user.id))
            change_wallet(user.id, int(g["amount"]), "gift", code)
            clear_st(c); await u.message.reply_text(f"🎁 +{num(g['amount'])}"); return

        # admin states
        if is_admin(user.id) and chat.type == ChatType.PRIVATE:
            if kind in ("a_add", "a_sub"):
                parts = text.split()
                if len(parts) < 2: await u.message.reply_text("آیدی مبلغ"); return
                try: tid = int(parts[0])
                except: await u.message.reply_text("آیدی?"); return
                amt = parse_amt(parts[1])
                if not amt: await u.message.reply_text("مبلغ?"); return
                ensure_user(type("U",(),{"id":tid,"username":"","full_name":str(tid),"is_bot":False})())
                try:
                    if kind == "a_add": change_wallet(tid, amt, "admin_add", str(user.id))
                    else: change_wallet(tid, -amt, "admin_sub", str(user.id))
                except ValueError as e: await u.message.reply_text(str(e)); return
                clear_st(c); await u.message.reply_text("OK", reply_markup=admin_kb()); return
            if kind == "a_meow":
                m = re.match(r"^(min|max|cd)\s+(\d+)$", text, re.I)
                if m:
                    key = {"min":"meow_min","max":"meow_max","cd":"meow_cd"}[m.group(1).lower()]
                    sset(key, m.group(2)); clear_st(c); await u.message.reply_text("OK", reply_markup=admin_kb())
                return
            if kind == "a_bank":
                m = re.match(r"^(سود|مالیات)\s+([\d.]+)$", text)
                if m:
                    if m.group(1)=="سود": sset("bank_interest", m.group(2))
                    else: sset("transfer_tax", m.group(2))
                    clear_st(c); await u.message.reply_text("OK", reply_markup=admin_kb())
                return
            if kind == "a_tpl":
                if text.startswith("قالب "):
                    rest = text[5:].strip().split(maxsplit=1)
                    if len(rest)==2:
                        with tx() as conn:
                            conn.execute("INSERT OR REPLACE INTO templates(key,value) VALUES (?,?)", (rest[0], rest[1]))
                        clear_st(c); await u.message.reply_text("قالب OK", reply_markup=admin_kb())
                return
            if kind == "a_names":
                m = re.match(r"نام\s+(\w+)\s+(.+)", text)
                if m:
                    with tx() as conn:
                        conn.execute("INSERT OR REPLACE INTO names(key,value) VALUES (?,?)", (m.group(1), m.group(2)))
                    clear_st(c); await u.message.reply_text("نام OK", reply_markup=admin_kb())
                return
            if kind == "a_code":
                parts = text.split()
                if len(parts)>=3:
                    code, amt, uses = parts[0].upper(), parse_amt(parts[1]), parse_amt(parts[2])
                    if amt and uses:
                        with tx() as conn:
                            conn.execute("INSERT OR REPLACE INTO gift_codes(code,amount,uses_left,active,expires) VALUES (?,?,?,1,?)",
                                         (code, amt, int(uses), time.time()+30*86400))
                        clear_st(c); await u.message.reply_text(f"کد {code}", reply_markup=admin_kb())
                return
            if kind == "a_bcast":
                with tx() as conn:
                    ids = [r["id"] for r in conn.execute("SELECT id FROM users").fetchall()]
                ok=fail=0
                for i in ids:
                    try: await c.bot.send_message(int(i), text); ok+=1
                    except: fail+=1
                clear_st(c); await u.message.reply_text(f"✅{ok} ❌{fail}", reply_markup=admin_kb()); return
            if kind == "a_admins":
                m = re.match(r"ادمین\s*([+-])\s*(\d+)", text)
                if m:
                    with tx() as conn:
                        if m.group(1)=="+": conn.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (int(m.group(2)),))
                        elif int(user.id)==ADMIN_ID and int(m.group(2))!=ADMIN_ID:
                            conn.execute("DELETE FROM admins WHERE user_id=?", (int(m.group(2)),))
                    clear_st(c); await u.message.reply_text("OK", reply_markup=admin_kb())
                return

    # commands
    entity = nget("entity", "پیشی")
    if re.fullmatch(rf"(منو|menu|{re.escape(entity)}|پیشی)", low, re.I):
        if chat.type != ChatType.PRIVATE:
            await u.message.reply_text("در گپ از دستورات متنی استفاده کن.\n<code>راهنما</code>", parse_mode="HTML")
            return
        await u.message.reply_text("🐱 پنل MeowPoint", reply_markup=main_kb(user.id)); return
    if re.fullmatch(r"(راهنما|help)", low, re.I):
        await u.message.reply_text(help_text(), parse_mode="HTML"); return
    if re.fullmatch(r"(میو|meow|میو میو)", low, re.I):
        msg = await do_meow(c.bot, chat.id, user)
        await u.message.reply_text(msg, parse_mode="HTML"); return
    if re.fullmatch(r"(پروفایل|profile)", low, re.I):
        await u.message.reply_text(profile_text(get_user(user.id)), parse_mode="HTML"); return
    if re.fullmatch(r"(بانک|bank)", low, re.I):
        uu = get_user(user.id)
        kb = InlineKeyboardMarkup([
            [btn("📥 واریز", f"p:bin:{user.id}", "success"), btn("📤 برداشت", f"p:bout:{user.id}", "danger")],
            [btn("💸 انتقال", f"p:btr:{user.id}", "primary"), btn("📜 تاریخچه", f"p:blog:{user.id}", "primary")],
            [btn("💳 تغییر حساب", f"p:bacc:{user.id}", "primary")],
        ])
        await u.message.reply_text(bank_panel(uu), parse_mode="HTML", reply_markup=kb); return
    if re.fullmatch(r"(کارخانه|کارخونه|factory)", low, re.I):
        fac = dict(get_factory(user.id))
        kb = InlineKeyboardMarkup([
            [btn("▶️ تولید/برداشت", f"p:fgo:{user.id}", "success")],
            [btn("⬆️ ارتقا", f"p:fup:{user.id}", "primary")],
        ])
        await u.message.reply_text(factory_panel(user.full_name, fac), parse_mode="HTML", reply_markup=kb); return
    if re.fullmatch(r"(شهر|city)", low, re.I):
        kb = InlineKeyboardMarkup([
            [btn("⬆️ ارتقا شهر", f"p:cup:{user.id}", "success")],
            [btn("🕊 دونیت", f"p:cdon:{user.id}", "primary")],
        ])
        await u.message.reply_text(city_panel(get_user(user.id)), parse_mode="HTML", reply_markup=kb); return
    if re.fullmatch(r"(بازار|فروشگاه|shop|market)", low, re.I):
        # reuse callback style by sending keyboard
        with tx() as conn:
            items = conn.execute("SELECT * FROM shop WHERE active=1").fetchall()
        rows = [[btn(f"{it['name']}|{num(it['price'])}", f"p:buy:{it['id']}:{user.id}", "success")] for it in items]
        await u.message.reply_text("🛒 بازار:", reply_markup=InlineKeyboardMarkup(rows)); return
    if re.fullmatch(r"(رتبه|رتبه‌بندی|رتبه بندی|لیدربرد)", low, re.I):
        with tx() as conn:
            rows = conn.execute("SELECT id,name,wallet,bank,level FROM users ORDER BY wallet+bank DESC LIMIT 10").fetchall()
        lines = ["🏆 رتبه\n"]
        for i,r in enumerate(rows,1):
            lines.append(f"{mono(to_roman(i))} {mention(r['id'], r['name'])} — {num(int(r['wallet'])+int(r['bank']))}")
        await u.message.reply_text("\n".join(lines), parse_mode="HTML"); return
    if re.fullmatch(r"(جمع.?آوری|شکار|gather)", low, re.I):
        urow = get_user(user.id)
        cd = sint("gather_cd", 900)
        left = float(urow["last_gather"] or 0)+cd-time.time()
        if left > 0:
            await u.message.reply_text(render("gather_wait", left=fmt_time(left)), parse_mode="HTML"); return
        with tx() as conn:
            conn.execute("UPDATE users SET last_gather=? WHERE id=?", (time.time(), user.id))
        gain = random.randint(30, 150)
        change_wallet(user.id, gain, "gather", "")
        await u.message.reply_text(f"🎣 +{num(gain)} MP"); return
    if re.fullmatch(r"(کد|کد هدیه|gift)", low, re.I):
        set_st(c, "gift"); await u.message.reply_text("🎁 کد را بفرست:"); return
    if re.fullmatch(r"(دعوت|رفرال|friends)", low, re.I):
        me = await c.bot.get_me()
        link = f"https://t.me/{me.username}?start=ref{user.id}"
        uu = get_user(user.id)
        await u.message.reply_text(f"👥 لینک دعوت:\n{link}\nدعوت‌ها: {uu['referrals']}"); return
    if re.fullmatch(r"(پنل|admin)", low, re.I):
        if chat.type == ChatType.PRIVATE and is_admin(user.id):
            await u.message.reply_text("🎛 پنل مدیریت MeowPoint", reply_markup=admin_kb())
        return
    m = re.match(r"^(?:انتقال)\s+(.+)$", low, re.I)
    if m:
        amt = parse_amt(m.group(1))
        if not amt or not u.message.reply_to_message or not u.message.reply_to_message.from_user:
            await u.message.reply_text("ریپلای + انتقال 10k"); return
        to = u.message.reply_to_message.from_user
        if to.id == user.id or to.is_bot: await u.message.reply_text("نامعتبر"); return
        tax = int(amt * sfloat("transfer_tax", 0.02)); send = amt - tax
        try:
            change_wallet(user.id, -amt, "transfer_out", str(to.id))
            ensure_user(to)
            change_wallet(to.id, send, "transfer_in", str(user.id))
        except ValueError as e:
            await u.message.reply_text(str(e)); return
        await u.message.reply_text(f"✅ {num(send)} MP\n{mention(user.id,user.full_name)} → {mention(to.id,to.full_name)}", parse_mode="HTML")
        return

def own_check_ok(user):
    return True

def main():
    init_db()
    req_kw = dict(connection_pool_size=8, connect_timeout=60.0, read_timeout=60.0, write_timeout=60.0, pool_timeout=60.0)
    if PROXY_URL: req_kw["proxy"] = PROXY_URL
    req = HTTPXRequest(**req_kw)
    get_req = HTTPXRequest(**req_kw)
    app = Application.builder().token(BOT_TOKEN).request(req).get_updates_request(get_req).build()

    async def safe_text(update, context):
        try: await on_text(update, context)
        except Exception as e:
            log.exception("text")
            try:
                if update.effective_message:
                    await update.effective_message.reply_text("⚠️ %s" % str(e)[:150])
            except: pass

    async def safe_cb(update, context):
        try: await on_cb(update, context)
        except Exception as e:
            log.exception("cb")
            try:
                if update.callback_query:
                    await update.callback_query.answer(str(e)[:100], show_alert=True)
            except: pass

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", lambda u,c: u.message.reply_text(help_text(), parse_mode="HTML")))
    app.add_handler(CallbackQueryHandler(safe_cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, safe_text))
    app.add_handler(MessageHandler(filters.COMMAND, safe_text))
    log.info("MeowPoint started")
    app.run_polling(allowed_updates=Update.ALL_TYPES, bootstrap_retries=10, drop_pending_updates=True)

if __name__ == "__main__":
    main()
