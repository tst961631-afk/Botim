# -*- coding: utf-8 -*-
"""
ربات بازی بقا / زامبی — گپ‌محور
توکن و ادمین از تنظیمات قبلی
"""
from __future__ import annotations
import json, os, re, time, random, logging, asyncio
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ChatMemberHandler, ContextTypes, filters,
)
from telegram.constants import ChatType, ChatMemberStatus

BOT_TOKEN = "8975007734:AAFGsTyR56CLHJnr7ZFgz8DMAs2INlg1Qfc"
ADMIN_ID = 7530457395
DATA = "zombie_data.json"
TZ = ZoneInfo("Asia/Tehran")
STATE_TTL = 120

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("zombie")

# ---------------- storage ----------------
def D():
    return {
        "users": {},          # uid -> player
        "shop_sections": {},  # sid -> {name, items:[]}
        "boss_templates": [], # admin defined
        "regions": [],
        "profile_imgs": [],   # [{min,max,file_id}]
        "clans": {},          # gid -> [clans]
        "active_bosses": {},  # mid -> boss instance
        "casinos": {},
        "herd": None,
        "settings": {
            "night_hour": 0, "night_minute": 30,
            "bank_hour": 0, "bank_minute": 0,
            "zombie_base_cd": 300,
            "zombie_reward_min": 100, "zombie_reward_max": 200,
        },
    }

def load():
    if os.path.exists(DATA):
        try:
            with open(DATA, "r", encoding="utf-8") as f:
                d = json.load(f)
            b = D()
            for k, v in b.items():
                d.setdefault(k, v if not isinstance(v, dict) else {**v, **d.get(k, {})} if k == "settings" else v)
            d.setdefault("settings", b["settings"])
            for sk, sv in b["settings"].items():
                d["settings"].setdefault(sk, sv)
            return d
        except Exception as e:
            log.error(e)
    return D()

def save(d):
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

def is_admin(uid): return int(uid) == ADMIN_ID

def uid_s(u): return str(u)

def now_ts(): return time.time()

# ---------------- player ----------------
def new_player(user):
    return {
        "id": user.id,
        "name": user.full_name or str(user.id),
        "username": (user.username or "").lower(),
        "wallet": 500,
        "bank": 0,
        "level": 1,
        "xp": 0,
        "hp": 100, "hp_max": 100,
        "energy": 100, "hunger": 50, "thirst": 50,
        "backpack": {"guns": [], "ammo": 0, "water": 0, "food": 0, "medkits": [], "grenades": 0, "vest": None},
        "storage": {"guns": [], "ammo": 0, "water": 0, "food": 0, "medkits": [], "grenades": 0},
        "active_gun": None,
        "shelter": {"name": "پناهگاه کوچک", "capacity": 20},
        "region": None,
        "clan": None,
        "last_zombie": 0,
        "mission": {"kill": 0, "need": 3, "done": False, "day": ""},
        "last_bank_interest_day": "",
    }

def eu(d, user):
    u = uid_s(user.id)
    if u not in d["users"]:
        d["users"][u] = new_player(user)
    else:
        d["users"][u]["name"] = user.full_name or d["users"][u].get("name")
        if user.username:
            d["users"][u]["username"] = user.username.lower()
    return d["users"][u]

def find_user(d, query):
    q = query.lstrip("@").lower()
    for u in d["users"].values():
        if u.get("username") == q or str(u.get("id")) == q:
            return u
    return None

# ---------------- helpers ----------------
def parse_amount(text: str):
    t = text.strip().lower().replace(",", "").replace(" ", "")
    t = t.replace("کا", "k").replace("ک", "k").replace("م", "m")
    m = re.match(r"^([\d.]+)([km])?$", t)
    if not m:
        try:
            return int(float(t))
        except Exception:
            return None
    val = float(m.group(1))
    suf = m.group(2)
    if suf == "k":
        val *= 1000
    elif suf == "m":
        val *= 1_000_000
    return int(val)

def btn(text, data, style=None):
    kw = {"text": text, "callback_data": data}
    if style in ("danger", "success", "primary"):
        kw["style"] = style
    try:
        return InlineKeyboardButton(**kw)
    except TypeError:
        kw.pop("style", None)
        return InlineKeyboardButton(**kw)

def own(action, owner_id, *parts):
    base = f"{action}:{owner_id}"
    if parts:
        base += ":" + ":".join(str(p) for p in parts)
    return base[:64]

def parse_cb(data: str):
    p = data.split(":")
    return p[0], p[1] if len(p) > 1 else "", p[2:]

def set_st(c, kind, extra=None):
    c.user_data["st"] = {"kind": kind, "ts": now_ts(), "extra": extra or {}}

def get_st(c):
    st = c.user_data.get("st")
    if not st:
        return None
    if now_ts() - st.get("ts", 0) > STATE_TTL:
        c.user_data.pop("st", None)
        return None
    return st

def clear_st(c):
    c.user_data.pop("st", None)

def zombie_cd(p, settings):
    base = int(settings.get("zombie_base_cd", 300))
    # هر لول ۱۰ ثانیه کم، حداقل ۶۰
    return max(60, base - (int(p.get("level", 1)) - 1) * 10)

def zombie_reward(p, settings, gun_dmg=10):
    mn = int(settings.get("zombie_reward_min", 100))
    mx = int(settings.get("zombie_reward_max", 200))
    base = random.randint(mn, mx)
    bonus = int(gun_dmg * 0.5) + int(p.get("level", 1)) * 5
    return base + bonus

def add_xp(p, amount):
    p["xp"] = p.get("xp", 0) + amount
    while p["xp"] >= p.get("level", 1) * 100:
        p["xp"] -= p["level"] * 100
        p["level"] = p.get("level", 1) + 1
        p["hp_max"] = 100 + (p["level"] - 1) * 10
        if p.get("vest"):
            p["hp_max"] += int(p["vest"].get("hp_bonus", 0))
        p["hp"] = min(p["hp"], p["hp_max"])

def gun_dmg(p):
    g = p.get("active_gun")
    if not g:
        # چاقو پیش‌فرض
        return 8
    return int(g.get("damage", 10))

def profile_photo(d, level):
    for it in sorted(d.get("profile_imgs", []), key=lambda x: x.get("min", 0)):
        if it.get("min", 0) <= level <= it.get("max", 9999):
            return it.get("file_id")
    return None

def rank_of(d, uid):
    arr = sorted(d["users"].values(), key=lambda x: (x.get("level", 1), x.get("wallet", 0) + x.get("bank", 0)), reverse=True)
    for i, u in enumerate(arr, 1):
        if str(u.get("id")) == str(uid):
            return i
    return len(arr)

HELP = (
    "📖 <b>راهنمای بازی بقا</b>\n\n"
    "• <b>پروفایل</b> — وضعیت شما\n"
    "• <b>کیف پول</b> — پول آزاد\n"
    "• <b>بانک</b> — واریز/برداشت + سود ۵٪ روزانه\n"
    "• <b>انبار</b> / <b>کوله</b> — جابه‌جایی وسایل\n"
    "• <b>فروشگاه</b> یا <b>شاپ</b> — خرید آیتم\n"
    "• <b>کشتن زامبی</b> — درآمد با کول‌داون\n"
    "• <b>عوض کردن تفنگ</b>\n"
    "• <b>آب بخور</b> / <b>غذا بخور</b>\n"
    "• <b>هیل کردن</b> — با کیت\n"
    "• <b>منطقه</b> — انتخاب منطقه\n"
    "• <b>انجمن</b> — کلن (حداکثر ۳ در گپ، ۱۲ عضو)\n"
    "• <b>قمار</b> — ۱/۲/۳ نفره\n"
    "• ریپلای + <code>انتقال پول 50k</code>\n"
    "• ریپلای روی باس + <b>شلیک</b>\n"
    "• <b>پرتاب نارنجک</b> / <b>رفتن به پناهگاه</b>\n"
    "• <b>مأموریت</b> — روزانه\n"
    "• ادمین: <code>/admin</code> فقط پیوی\n"
)

# ---------------- keyboards ----------------
def kb_bank(oid):
    return InlineKeyboardMarkup([
        [btn("🟢 واریز", own("bank_dep", oid), "success"),
         btn("🔴 برداشت", own("bank_wd", oid), "danger")],
        [btn("🔙 بستن", own("close", oid), "primary")],
    ])

def kb_shop_sections(d, oid):
    rows = []
    for sid, sec in d.get("shop_sections", {}).items():
        rows.append([btn(sec.get("name", sid), own("shop_sec", oid, sid), "primary")])
    if not rows:
        rows = [[btn("خالی — ادمین آیتم اضافه کند", own("close", oid), "danger")]]
    rows.append([btn("🔙 بستن", own("close", oid), "danger")])
    return InlineKeyboardMarkup(rows)

def kb_shop_items(sec, oid, sid):
    rows = []
    for it in sec.get("items", []):
        rows.append([btn(f"{it.get('name')} — {it.get('price')}", own("shop_item", oid, sid, it.get("id")), "primary")])
    rows.append([btn("🔙 بخش‌ها", own("shop", oid), "primary")])
    return InlineKeyboardMarkup(rows)

def kb_item_buy(oid, sid, iid):
    return InlineKeyboardMarkup([
        [btn("✅ خرید", own("shop_buy", oid, sid, iid), "success")],
        [btn("🔙", own("shop_sec", oid, sid), "primary")],
    ])

# ---------------- commands ----------------
async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    d = load(); eu(d, u.effective_user); save(d)
    if u.effective_chat.type == ChatType.PRIVATE:
        if is_admin(u.effective_user.id):
            await u.message.reply_text("ادمین هستی. /admin برای پنل\n/help راهنما")
        else:
            await u.message.reply_text(HELP, parse_mode="HTML")
    else:
        await u.message.reply_text("بازی بقا فعال است. /help")

async def cmd_help(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(HELP, parse_mode="HTML")

async def cmd_admin(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_chat.type != ChatType.PRIVATE:
        return
    if not is_admin(u.effective_user.id):
        return
    kb = InlineKeyboardMarkup([
        [btn("🛒 مدیریت فروشگاه", "a_shop", "success")],
        [btn("👹 قالب باس", "a_boss", "danger")],
        [btn("🗺️ مناطق", "a_region", "primary")],
        [btn("🖼 عکس پروفایل لول", "a_pfp", "primary")],
        [btn("💰 واریز پوینت به کاربر", "a_give", "success")],
        [btn("🐺 گله زامبی (ارسال به گپ‌ها دستی)", "a_herd", "danger")],
        [btn("👹 اسپاون باس در گپ (آیدی گپ)", "a_spawn", "danger")],
    ])
    await u.message.reply_text("🎛 پنل ادمین (فقط پیوی)", reply_markup=kb)

# ---------------- bank interest & night jobs ----------------
async def job_bank_interest(context: ContextTypes.DEFAULT_TYPE):
    d = load()
    day = datetime.now(TZ).strftime("%Y-%m-%d")
    for p in d["users"].values():
        if p.get("last_bank_interest_day") == day:
            continue
        bal = int(p.get("bank", 0))
        if bal > 0:
            p["bank"] = bal + int(bal * 0.05)
        p["last_bank_interest_day"] = day
    save(d)
    log.info("bank interest applied %s", day)

async def job_night(context: ContextTypes.DEFAULT_TYPE):
    d = load()
    for p in d["users"].values():
        # مصرف شب
        p["hunger"] = max(0, p.get("hunger", 50) - 15)
        p["thirst"] = max(0, p.get("thirst", 50) - 15)
        p["energy"] = max(0, p.get("energy", 100) - 10)
        if p["hunger"] < 20 or p["thirst"] < 20:
            p["hp"] = max(1, p.get("hp", 100) - 10)
    save(d)
    log.info("night tick")

async def job_boss_timeout(context: ContextTypes.DEFAULT_TYPE):
    d = load()
    now = now_ts()
    dead = []
    for mid, b in list(d.get("active_bosses", {}).items()):
        if now - b.get("last_hit", b.get("spawned", now)) >= 600:
            dead.append(mid)
            chat_id = b.get("chat_id")
            try:
                await context.bot.send_message(
                    chat_id,
                    "👹 باس رفت چون کسی را ندید.",
                    reply_to_message_id=int(b.get("message_id", 0)) or None,
                )
            except Exception:
                try:
                    await context.bot.send_message(chat_id, "👹 باس رفت چون کسی را ندید.")
                except Exception:
                    pass
    for mid in dead:
        d["active_bosses"].pop(mid, None)
    if dead:
        save(d)

# ---------------- text router ----------------
async def on_text(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.message.text:
        return
    user = u.effective_user
    chat = u.effective_chat
    text = u.message.text.strip()
    low = text.lower()
    d = load()
    p = eu(d, user)

    # admin states in private
    if is_admin(user.id) and chat.type == ChatType.PRIVATE:
        st = get_st(c)
        if st:
            await admin_state(u, c, d, text, st)
            return
        if low in ("/admin", "admin"):
            await cmd_admin(u, c)
            return

    # help
    if low in ("/help", "help", "راهنما"):
        await u.message.reply_text(HELP, parse_mode="HTML")
        return

    # transfer reply
    if u.message.reply_to_message and (low.startswith("انتقال پول") or low.startswith("انتقال ")):
        m = re.search(r"([\d.,]+)\s*([kkmکاامم])?", text, re.I)
        # use parse on last token
        parts = text.split()
        amt = None
        for part in reversed(parts):
            amt = parse_amount(part)
            if amt is not None:
                break
        if amt is None or amt <= 0:
            await u.message.reply_text("مبلغ نامعتبر. مثال: انتقال پول 50k")
            return
        target_user = u.message.reply_to_message.from_user
        if not target_user or target_user.is_bot:
            await u.message.reply_text("روی کاربر ریپلای کن")
            return
        if target_user.id == user.id:
            await u.message.reply_text("به خودت؟")
            return
        if p.get("wallet", 0) < amt:
            await u.message.reply_text(f"موجودی کافی نیست. کیف: {p.get('wallet',0)}")
            return
        tp = eu(d, target_user)
        p["wallet"] -= amt
        tp["wallet"] = tp.get("wallet", 0) + amt
        save(d)
        await u.message.reply_text(f"✅ {amt} به {tp.get('name')} منتقل شد.")
        return

    # boss shoot
    if low in ("شلیک", "/shoot") and u.message.reply_to_message:
        await handle_shoot(u, c, d, p)
        return

    # grenade
    if low in ("پرتاب نارنجک", "نارنجک"):
        await handle_grenade(u, c, d, p)
        return

    # shelter run
    if low in ("رفتن به پناهگاه", "پناهگاه"):
        p["in_shelter"] = True
        p["shelter_until"] = now_ts() + 600
        save(d)
        await u.message.reply_text("🏠 رفتی پناهگاه. فعلاً امنی.")
        return

    # eat drink heal
    if low in ("آب بخور", "آب خوردن"):
        if p["backpack"].get("water", 0) <= 0:
            await u.message.reply_text("آب نداری.")
            return
        p["backpack"]["water"] -= 1
        p["thirst"] = min(100, p.get("thirst", 0) + 30)
        p["energy"] = min(100, p.get("energy", 0) + 5)
        save(d)
        await u.message.reply_text(f"نوشیدی. تشنگی: {p['thirst']}")
        return
    if low in ("غذا بخور", "غذا خوردن"):
        if p["backpack"].get("food", 0) <= 0:
            await u.message.reply_text("غذا نداری.")
            return
        p["backpack"]["food"] -= 1
        p["hunger"] = min(100, p.get("hunger", 0) + 30)
        p["energy"] = min(100, p.get("energy", 0) + 10)
        save(d)
        await u.message.reply_text(f"خوردی. گرسنگی: {p['hunger']}")
        return
    if low in ("هیل کردن", "هیل"):
        kits = p["backpack"].get("medkits") or []
        if not kits:
            await u.message.reply_text("کیت نداری.")
            return
        kit = kits.pop(0)
        p["backpack"]["medkits"] = kits
        pct = float(kit.get("heal_pct", 50))
        heal = int(p.get("hp_max", 100) * pct / 100)
        p["hp"] = min(p["hp_max"], p.get("hp", 0) + heal)
        save(d)
        await u.message.reply_text(f"هیل شدی +{heal} | HP {p['hp']}/{p['hp_max']} | کیت باقی: {len(kits)}")
        return

    # simple commands
    if low in ("پروفایل", "profile"):
        await show_profile(u, c, d, p)
        return
    if low in ("کیف پول", "کیف", "wallet"):
        await u.message.reply_text(f"💼 کیف پول: <b>{p.get('wallet',0)}</b>", parse_mode="HTML")
        return
    if low in ("بانک", "bank"):
        await u.message.reply_text(
            f"🏦 بانک\nموجودی: <b>{p.get('bank',0)}</b>\nسود روزانه: ۵٪\nکیف: {p.get('wallet',0)}",
            parse_mode="HTML",
            reply_markup=kb_bank(user.id),
        )
        return
    if low in ("فروشگاه", "شاپ", "shop"):
        await u.message.reply_text("🛒 فروشگاه — بخش را انتخاب کن:", reply_markup=kb_shop_sections(d, user.id))
        return
    if low in ("انبار", "storage"):
        await show_storage(u, c, d, p, user.id)
        return
    if low in ("کوله", "کوله پشتی", "backpack"):
        await show_backpack(u, c, d, p)
        return
    if low in ("کشتن زامبی", "زامبی"):
        await do_zombie(u, c, d, p)
        return
    if low in ("عوض کردن تفنگ", "تعویض تفنگ"):
        await swap_gun_menu(u, c, d, p, user.id)
        return
    if low in ("انجمن", "کلن", "clan"):
        await clan_panel(u, c, d, p)
        return
    if low in ("قمار", "کازینو", "casino"):
        await casino_menu(u, c, d, p, user.id)
        return
    if low in ("منطقه", "نقشه", "regions"):
        await regions_menu(u, c, d, p, user.id)
        return
    if low in ("مأموریت", "ماموریت", "mission"):
        await mission_status(u, c, d, p)
        return

    # admin give points state already handled
    # storage deposit via reply to bot prompt
    st = get_st(c)
    if st and st.get("kind") in ("bank_dep", "bank_wd", "casino_amt", "cas_join_wait", "store_in", "store_out", "a_give_amt", "a_give_user"):
        if is_admin(user.id) or st["kind"].startswith("bank") or st["kind"].startswith("casino") or st["kind"].startswith("store") or st["kind"].startswith("cas"):
            await player_state(u, c, d, p, text, st)
            return

    save(d)

async def player_state(u, c, d, p, text, st):
    kind = st["kind"]
    oid = u.effective_user.id
    if kind == "bank_dep":
        amt = parse_amount(text)
        clear_st(c)
        if not amt or amt <= 0:
            await u.message.reply_text("مبلغ نامعتبر")
            return
        if p.get("wallet", 0) < amt:
            await u.message.reply_text(f"موجودی کافی نیست. کیف: {p.get('wallet',0)}")
            return
        p["wallet"] -= amt
        p["bank"] = p.get("bank", 0) + amt
        save(d)
        await u.message.reply_text(f"✅ واریز {amt}\nبانک: {p['bank']} | کیف: {p['wallet']}")
        return
    if kind == "bank_wd":
        amt = parse_amount(text)
        clear_st(c)
        if not amt or amt <= 0:
            await u.message.reply_text("مبلغ نامعتبر")
            return
        if p.get("bank", 0) < amt:
            await u.message.reply_text(f"موجودی کافی نیست. بانک: {p.get('bank',0)}")
            return
        p["bank"] -= amt
        p["wallet"] = p.get("wallet", 0) + amt
        save(d)
        await u.message.reply_text(f"✅ برداشت {amt}\nبانک: {p['bank']} | کیف: {p['wallet']}")
        return
    if kind == "casino_amt":
        extra = st.get("extra") or {}
        mode = int(extra.get("mode", 1))
        amt = parse_amount(text)
        clear_st(c)
        if not amt or amt <= 0:
            await u.message.reply_text("مبلغ نامعتبر")
            return
        if p.get("wallet", 0) < amt:
            await u.message.reply_text(f"پوینت کافی ندارید. موجودی: {p.get('wallet',0)}")
            return
        if mode == 1:
            await casino_solo(u, c, d, p, amt)
        else:
            await casino_multi_create(u, c, d, p, amt, mode)
        return
    if kind == "store_in":
        clear_st(c)
        await storage_move(u, d, p, text, to_storage=True)
        return
    if kind == "store_out":
        clear_st(c)
        await storage_move(u, d, p, text, to_storage=False)
        return
    if kind == "a_give_user":
        set_st(c, "a_give_amt", {"user": text})
        await u.message.reply_text("مقدار پوینت:")
        return
    if kind == "a_give_amt":
        un = (st.get("extra") or {}).get("user", "")
        amt = parse_amount(text)
        clear_st(c)
        tu = find_user(d, un)
        if not tu:
            # create placeholder by username only if known
            await u.message.reply_text("کاربر پیدا نشد (باید حداقل یکبار استارت زده باشد)")
            return
        if not amt:
            await u.message.reply_text("مبلغ نامعتبر")
            return
        tu["wallet"] = tu.get("wallet", 0) + amt
        save(d)
        await u.message.reply_text(f"✅ {amt} به {tu.get('name')} (@{tu.get('username')})")
        return

async def storage_move(u, d, p, text, to_storage=True):
    # parse like: 7 لیتر آب | 100 تیر | 2 غذا
    t = text.strip()
    bp, st = p["backpack"], p["storage"]
    moved = []
    # ammo
    m = re.search(r"(\d+)\s*تیر", t)
    if m:
        n = int(m.group(1))
        if to_storage:
            n = min(n, bp.get("ammo", 0)); bp["ammo"] -= n; st["ammo"] = st.get("ammo", 0) + n
        else:
            n = min(n, st.get("ammo", 0)); st["ammo"] -= n; bp["ammo"] = bp.get("ammo", 0) + n
        moved.append(f"{n} تیر")
    m = re.search(r"(\d+)\s*(لیتر\s*)?آب", t)
    if m:
        n = int(m.group(1))
        if to_storage:
            n = min(n, bp.get("water", 0)); bp["water"] -= n; st["water"] = st.get("water", 0) + n
        else:
            n = min(n, st.get("water", 0)); st["water"] -= n; bp["water"] = bp.get("water", 0) + n
        moved.append(f"{n} آب")
    m = re.search(r"(\d+)\s*(کیلو\s*)?غذا", t)
    if m:
        n = int(m.group(1))
        if to_storage:
            n = min(n, bp.get("food", 0)); bp["food"] -= n; st["food"] = st.get("food", 0) + n
        else:
            n = min(n, st.get("food", 0)); st["food"] -= n; bp["food"] = bp.get("food", 0) + n
        moved.append(f"{n} غذا")
    m = re.search(r"(\d+)\s*نارنجک", t)
    if m:
        n = int(m.group(1))
        if to_storage:
            n = min(n, bp.get("grenades", 0)); bp["grenades"] -= n; st["grenades"] = st.get("grenades", 0) + n
        else:
            n = min(n, st.get("grenades", 0)); st["grenades"] -= n; bp["grenades"] = bp.get("grenades", 0) + n
        moved.append(f"{n} نارنجک")
    save(d)
    if not moved:
        await u.message.reply_text("فرمت: 100 تیر | 7 لیتر آب | 2 غذا")
        return
    await u.message.reply_text("✅ " + "، ".join(moved))

# ---------------- features ----------------
async def show_profile(u, c, d, p):
    oid = u.effective_user.id
    rank = rank_of(d, oid)
    g = p.get("active_gun") or {"name": "چاقو"}
    txt = (
        f"👤 <b>{p.get('name')}</b>\n"
        f"🎖 لول {p.get('level',1)} | رنک #{rank}\n"
        f"❤️ {p.get('hp')}/{p.get('hp_max')}\n"
        f"💼 کیف: {p.get('wallet',0)} (بانک جدا)\n"
        f"🔫 {g.get('name')}\n"
        f"⚡ انرژی {p.get('energy')} | 🍖 {p.get('hunger')} | 💧 {p.get('thirst')}\n"
        f"📍 {p.get('region') or 'نامشخص'}\n"
        f"🏠 {p.get('shelter',{}).get('name')}"
    )
    kb = InlineKeyboardMarkup([
        [btn("🗺️ نقشه منطقه", own("map", oid), "primary")],
        [btn("🔙 بستن", own("close", oid), "danger")],
    ])
    ph = profile_photo(d, p.get("level", 1))
    if ph:
        await u.message.reply_photo(ph, caption=txt, parse_mode="HTML", reply_markup=kb)
    else:
        await u.message.reply_text(txt, parse_mode="HTML", reply_markup=kb)

async def show_storage(u, c, d, p, oid):
    st = p.get("storage", {})
    txt = (
        f"📦 انبار (ظرفیت پناهگاه: {p.get('shelter',{}).get('capacity',20)})\n"
        f"💧 آب: {st.get('water',0)}\n"
        f"🍖 غذا: {st.get('food',0)}\n"
        f"🔫 تیر: {st.get('ammo',0)}\n"
        f"💣 نارنجک: {st.get('grenades',0)}\n"
        f"تفنگ‌ها: {len(st.get('guns') or [])}"
    )
    kb = InlineKeyboardMarkup([
        [btn("🟢 انبار کردن وسایل", own("st_in", oid), "success")],
        [btn("🔵 برداشتن از انبار", own("st_out", oid), "primary")],
        [btn("بستن", own("close", oid), "danger")],
    ])
    await u.message.reply_text(txt, reply_markup=kb)

async def show_backpack(u, c, d, p):
    bp = p.get("backpack", {})
    guns = ", ".join(g.get("name") for g in (bp.get("guns") or [])) or "-"
    await u.message.reply_text(
        f"🎒 کوله\n"
        f"تفنگ‌ها: {guns}\n"
        f"فعال: {(p.get('active_gun') or {}).get('name','چاقو')}\n"
        f"تیر: {bp.get('ammo',0)} | آب: {bp.get('water',0)} | غذا: {bp.get('food',0)}\n"
        f"نارنجک: {bp.get('grenades',0)} | کیت: {len(bp.get('medkits') or [])}\n"
        f"جلیقه: {(p.get('backpack',{}).get('vest') or p.get('vest') or {}).get('name','-')}"
    )

async def do_zombie(u, c, d, p):
    settings = d["settings"]
    cd = zombie_cd(p, settings)
    left = int(p.get("last_zombie", 0) + cd - now_ts())
    if left > 0:
        m, s = divmod(left, 60)
        await u.message.reply_text(f"⏳ تا کشتن بعدی: {m}م {s}ث")
        return
    if p.get("in_shelter") and p.get("shelter_until", 0) > now_ts():
        await u.message.reply_text("داخل پناهگاهی.")
        return
    dmg = gun_dmg(p)
    # need ammo if gun not knife
    if p.get("active_gun") and p["backpack"].get("ammo", 0) <= 0:
        await u.message.reply_text("تیر نداری.")
        return
    if p.get("active_gun"):
        p["backpack"]["ammo"] = p["backpack"].get("ammo", 0) - 1
    reward = zombie_reward(p, settings, dmg)
    p["wallet"] = p.get("wallet", 0) + reward
    p["last_zombie"] = now_ts()
    add_xp(p, 10)
    # mission
    day = datetime.now(TZ).strftime("%Y-%m-%d")
    if p.get("mission", {}).get("day") != day:
        p["mission"] = {"kill": 0, "need": 3, "done": False, "day": day}
    p["mission"]["kill"] = p["mission"].get("kill", 0) + 1
    if not p["mission"].get("done") and p["mission"]["kill"] >= p["mission"]["need"]:
        p["mission"]["done"] = True
        p["wallet"] += 300
        await u.message.reply_text(f"🧟 +{reward} | مأموریت روزانه تمام شد +300")
    else:
        await u.message.reply_text(f"🧟 زامبی کشته شد! +{reward}\nکیف: {p['wallet']} | لول {p['level']}")
    # random ambush
    if random.random() < 0.12:
        n = random.randint(7, 30)
        await u.message.reply_text(
            f"⚠️ {n} زامبی محاصره‌ات کردند!\n"
            f"اگر نارنجک داری: <b>پرتاب نارنجک</b>\n"
            f"یا: <b>رفتن به پناهگاه</b>",
            parse_mode="HTML",
        )
        p["ambush"] = n
    save(d)

async def handle_grenade(u, c, d, p):
    if p["backpack"].get("grenades", 0) <= 0:
        await u.message.reply_text("نارنجک نداری. برو پناهگاه یا از شاپ بخر.")
        return
    p["backpack"]["grenades"] -= 1
    bonus = random.randint(80, 250)
    p["wallet"] = p.get("wallet", 0) + bonus
    p.pop("ambush", None)
    save(d)
    await u.message.reply_text(f"💥 نارنجک پرتاب شد! +{bonus}")

async def handle_shoot(u, c, d, p):
    rp = u.message.reply_to_message
    mid = str(rp.message_id)
    b = d.get("active_bosses", {}).get(mid)
    if not b:
        # try find by chat
        for k, v in d.get("active_bosses", {}).items():
            if v.get("chat_id") == u.effective_chat.id and v.get("message_id") == rp.message_id:
                b = v; mid = k; break
    if not b:
        await u.message.reply_text("این پیام باس فعال نیست.")
        return
    if p.get("active_gun") and p["backpack"].get("ammo", 0) <= 0:
        await u.message.reply_text("تیر نداری.")
        return
    if p.get("active_gun"):
        p["backpack"]["ammo"] -= 1
    dmg = gun_dmg(p)
    b["hp"] = max(0, b.get("hp", 0) - dmg)
    b["last_hit"] = now_ts()
    b.setdefault("dmg", {})
    uid = uid_s(u.effective_user.id)
    b["dmg"][uid] = b["dmg"].get(uid, 0) + dmg
    # boss hits player
    hit = random.randint(int(b.get("power", 15)), int(b.get("power", 15)) + 50)
    p["hp"] = max(0, p.get("hp", 100) - hit)
    await u.message.reply_text(
        f"شما با {(p.get('active_gun') or {}).get('name','چاقو')} {dmg} دمیج زدید.\n"
        f"باس: {b['hp']}/{b['hp_max']}\n"
        f"دمیج باس به شما: -{hit} | HP {p['hp']}/{p['hp_max']}"
    )
    try:
        await c.bot.edit_message_text(
            chat_id=b["chat_id"],
            message_id=b["message_id"],
            text=f"👹 <b>{b.get('name')}</b>\n{b.get('desc','')}\n❤️ {b['hp']}/{b['hp_max']}\nریپلای + شلیک",
            parse_mode="HTML",
        )
    except Exception:
        pass
    if b["hp"] <= 0:
        await finish_boss(c, d, mid, b)
    if p["hp"] <= 0:
        await player_death(u, d, p)
    save(d)

async def finish_boss(c, d, mid, b):
    total = sum(b.get("dmg", {}).values()) or 1
    lines = [f"🏆 باس {b.get('name')} شکست!\n"]
    for uid, dmg in sorted(b.get("dmg", {}).items(), key=lambda x: -x[1]):
        share = dmg / total
        reward = int(500 * share) + int(dmg)
        water = int(5 * share)
        food = int(5 * share)
        u = d["users"].get(uid)
        if u:
            u["wallet"] = u.get("wallet", 0) + reward
            u["backpack"]["water"] = u["backpack"].get("water", 0) + water
            u["backpack"]["food"] = u["backpack"].get("food", 0) + food
            lines.append(f"• {u.get('name')}: +{reward} | آب{water} غذا{food}")
    d["active_bosses"].pop(mid, None)
    save(d)
    try:
        await c.bot.send_message(b["chat_id"], "\n".join(lines))
    except Exception:
        pass

async def player_death(u, d, p):
    p["wallet"] = 0
    p["backpack"] = {"guns": [], "ammo": 0, "water": 0, "food": 0, "medkits": [], "grenades": 0, "vest": None}
    p["active_gun"] = None
    p["hp"] = p.get("hp_max", 100)
    # keep bank, storage, level, clan, shelter
    save(d)
    await u.message.reply_text("💀 مردی! کیف و کوله صفر شد. بانک و انبار ماند.")

async def swap_gun_menu(u, c, d, p, oid):
    guns = p.get("backpack", {}).get("guns") or []
    if not guns:
        await u.message.reply_text("تفنگی در کوله نیست.")
        return
    rows = [[btn(g.get("name", "gun"), own("gun_set", oid, i), "primary")] for i, g in enumerate(guns)]
    rows.append([btn("بستن", own("close", oid), "danger")])
    await u.message.reply_text("تفنگ فعال را انتخاب کن:", reply_markup=InlineKeyboardMarkup(rows))

async def mission_status(u, c, d, p):
    day = datetime.now(TZ).strftime("%Y-%m-%d")
    if p.get("mission", {}).get("day") != day:
        p["mission"] = {"kill": 0, "need": 3, "done": False, "day": day}
        save(d)
    m = p["mission"]
    await u.message.reply_text(
        f"🎯 مأموریت روزانه\nکشتن زامبی: {m.get('kill',0)}/{m.get('need',3)}\n"
        + ("✅ انجام شد" if m.get("done") else "در حال انجام")
    )

# ----- casino -----
async def casino_menu(u, c, d, p, oid):
    kb = InlineKeyboardMarkup([
        [btn("🎲 یک نفره", own("cas1", oid), "primary")],
        [btn("👥 دو نفره", own("cas2", oid), "success")],
        [btn("👨‍👦‍👦 سه نفره", own("cas3", oid), "success")],
        [btn("بستن", own("close", oid), "danger")],
    ])
    await u.message.reply_text("قمار — فقط از کیف پول:", reply_markup=kb)

async def casino_solo(u, c, d, p, amt):
    p["wallet"] -= amt
    mults = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]
    mult = random.choice(mults)
    win = int(amt * mult)
    p["wallet"] += win
    save(d)
    await u.message.reply_text(
        f"🎲 تاس انداخته شد...\nضریب: <b>{mult}x</b>\n"
        f"گذاشتی: {amt} | بردی: {win}\nکیف: {p['wallet']}",
        parse_mode="HTML",
    )

async def casino_multi_create(u, c, d, p, amt, mode):
    p["wallet"] -= amt
    cid = f"c{int(now_ts())}{random.randint(10,99)}"
    d.setdefault("casinos", {})[cid] = {
        "mode": mode,
        "amount": amt,
        "players": {uid_s(u.effective_user.id): u.effective_user.full_name},
        "chat_id": u.effective_chat.id,
        "expires": now_ts() + 60,
        "owner": uid_s(u.effective_user.id),
    }
    save(d)
    kb = InlineKeyboardMarkup([
        [btn("✅ شرکت", own("cas_join", u.effective_user.id, cid), "success")],
        [btn("بستن", own("close", u.effective_user.id), "danger")],
    ])
    await u.message.reply_text(
        f"🎰 قمار {mode} نفره | مبلغ: {amt}\n"
        f"شرکت‌کننده‌ها: 1/{mode}\nمهلت ۶۰ ثانیه",
        reply_markup=kb,
    )

# ----- clan -----
async def clan_panel(u, c, d, p):
    if u.effective_chat.type == ChatType.PRIVATE:
        await u.message.reply_text("انجمن فقط در گپ.")
        return
    gid = str(u.effective_chat.id)
    clans = d.setdefault("clans", {}).setdefault(gid, [])
    my = None
    for cl in clans:
        if uid_s(u.effective_user.id) in cl.get("members", []) or uid_s(u.effective_user.id) == cl.get("leader"):
            my = cl
            break
    if not my:
        kb = InlineKeyboardMarkup([
            [btn("➕ ساخت انجمن", own("clan_create", u.effective_user.id), "success")],
            [btn("بستن", own("close", u.effective_user.id), "danger")],
        ])
        await u.message.reply_text(f"عضو انجمنی نیستی.\nکلن‌های گپ: {len(clans)}/3", reply_markup=kb)
        return
    role = "لیدر" if uid_s(u.effective_user.id) == my.get("leader") else "عضو"
    txt = f"🏛 انجمن: {my.get('name')}\nنقش تو: {role}\nاعضا: {len(my.get('members',[]))}/12"
    rows = []
    if role == "لیدر":
        rows.append([btn("📩 دعوت (ریپلای+دعوت)", own("clan_inv_hint", u.effective_user.id), "primary")])
        rows.append([btn("🚫 اخراج (ریپلای+اخراج)", own("clan_kick_hint", u.effective_user.id), "danger")])
    rows.append([btn("🚪 ترک انجمن", own("clan_leave", u.effective_user.id), "danger")])
    await u.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(rows))

# ----- regions -----
async def regions_menu(u, c, d, p, oid):
    regs = d.get("regions") or []
    if not regs:
        await u.message.reply_text("منطقه‌ای تعریف نشده.")
        return
    rows = [[btn(r.get("name", "r"), own("reg", oid, i), "primary")] for i, r in enumerate(regs)]
    rows.append([btn("بستن", own("close", oid), "danger")])
    await u.message.reply_text("منطقه را انتخاب کن:", reply_markup=InlineKeyboardMarkup(rows))

# ---------------- callbacks ----------------
async def on_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    data = q.data or ""
    user = q.from_user
    d = load()
    p = eu(d, user)

    # admin callbacks without owner
    if data.startswith("a_") and is_admin(user.id):
        await admin_cb(q, c, d, data)
        return

    action, owner, rest = parse_cb(data)
    if owner and str(owner) != str(user.id):
        await q.answer("این پنل مال تو نیست ❌", show_alert=True)
        return
    await q.answer()

    if action == "close":
        try:
            await q.edit_message_text("بسته شد.")
        except Exception:
            pass
        return

    if action == "bank_dep":
        set_st(c, "bank_dep")
        await q.edit_message_text("مقدار واریز را بفرست (مثلاً 10k یا 1m):")
        return
    if action == "bank_wd":
        set_st(c, "bank_wd")
        await q.edit_message_text("مقدار برداشت را بفرست:")
        return

    if action == "shop":
        await q.edit_message_text("🛒 بخش‌ها:", reply_markup=kb_shop_sections(d, user.id))
        return
    if action == "shop_sec":
        sid = rest[0] if rest else ""
        sec = d.get("shop_sections", {}).get(sid)
        if not sec:
            await q.answer("نیست", show_alert=True); return
        await q.edit_message_text(f"📁 {sec.get('name')}", reply_markup=kb_shop_items(sec, user.id, sid))
        return
    if action == "shop_item":
        sid, iid = (rest + ["", ""])[:2]
        sec = d.get("shop_sections", {}).get(sid, {})
        it = next((x for x in sec.get("items", []) if x.get("id") == iid), None)
        if not it:
            await q.answer("نیست", show_alert=True); return
        cap = (
            f"🛍 <b>{it.get('name')}</b>\n{it.get('desc','')}\n"
            f"نوع: {it.get('type')}\nقیمت: {it.get('price')}\n"
            f"اثر: {it.get('effect_label','-')}\nکیف تو: {p.get('wallet',0)}"
        )
        if it.get("file_id"):
            try:
                await q.message.reply_photo(it["file_id"], caption=cap, parse_mode="HTML", reply_markup=kb_item_buy(user.id, sid, iid))
                return
            except Exception:
                pass
        await q.edit_message_text(cap, parse_mode="HTML", reply_markup=kb_item_buy(user.id, sid, iid))
        return
    if action == "shop_buy":
        sid, iid = (rest + ["", ""])[:2]
        sec = d.get("shop_sections", {}).get(sid, {})
        it = next((x for x in sec.get("items", []) if x.get("id") == iid), None)
        if not it:
            await q.answer("نیست", show_alert=True); return
        price = int(it.get("price", 0))
        if p.get("wallet", 0) < price:
            await q.answer(f"پوینت کافی ندارید. موجودی: {p.get('wallet',0)}", show_alert=True)
            return
        p["wallet"] -= price
        apply_item(p, it)
        save(d)
        try:
            await q.edit_message_caption(caption=f"✅ خرید شد: {it.get('name')}\nکیف: {p['wallet']}")
        except Exception:
            await q.edit_message_text(f"✅ خرید شد: {it.get('name')}\nکیف: {p['wallet']}")
        return

    if action == "st_in":
        set_st(c, "store_in")
        bp = p["backpack"]
        await q.edit_message_text(
            f"کوله: تیر {bp.get('ammo',0)} | آب {bp.get('water',0)} | غذا {bp.get('food',0)}\n"
            f"در جواب بنویس مثلاً: 100 تیر یا 7 لیتر آب"
        )
        return
    if action == "st_out":
        set_st(c, "store_out")
        st = p["storage"]
        await q.edit_message_text(
            f"انبار: تیر {st.get('ammo',0)} | آب {st.get('water',0)} | غذا {st.get('food',0)}\n"
            f"چه برداری؟ مثلاً 50 تیر"
        )
        return

    if action == "gun_set":
        idx = int(rest[0]) if rest else -1
        guns = p.get("backpack", {}).get("guns") or []
        if 0 <= idx < len(guns):
            p["active_gun"] = guns[idx]
            save(d)
            await q.edit_message_text(f"✅ تفنگ فعال: {guns[idx].get('name')}")
        return

    if action == "cas1":
        set_st(c, "casino_amt", {"mode": 1})
        await q.edit_message_text("مبلغ قمار یک‌نفره را بفرست:")
        return
    if action == "cas2":
        set_st(c, "casino_amt", {"mode": 2})
        await q.edit_message_text("مبلغ قمار دو‌نفره را بفرست:")
        return
    if action == "cas3":
        set_st(c, "casino_amt", {"mode": 3})
        await q.edit_message_text("مبلغ قمار سه‌نفره را بفرست:")
        return
    if action == "cas_join":
        cid = rest[0] if rest else ""
        game = d.get("casinos", {}).get(cid)
        if not game or now_ts() > game.get("expires", 0):
            await q.answer("منقضی شده", show_alert=True); return
        if uid_s(user.id) in game["players"]:
            await q.answer("قبلاً هستی", show_alert=True); return
        if len(game["players"]) >= game["mode"]:
            await q.answer("پر است", show_alert=True); return
        amt = game["amount"]
        if p.get("wallet", 0) < amt:
            await q.answer(f"پوینت کافی ندارید. موجودی: {p.get('wallet',0)}", show_alert=True)
            return
        p["wallet"] -= amt
        game["players"][uid_s(user.id)] = user.full_name
        save(d)
        n = len(game["players"])
        if n >= game["mode"]:
            winner = random.choice(list(game["players"].keys()))
            pot = amt * game["mode"]
            if winner in d["users"]:
                d["users"][winner]["wallet"] = d["users"][winner].get("wallet", 0) + pot
            names = ", ".join(game["players"].values())
            d["casinos"].pop(cid, None)
            save(d)
            await q.edit_message_text(
                f"🎲 تاس... برنده: {game['players'][winner]}\n"
                f"پات: {pot}\nشرکت‌کننده‌ها: {names}"
            )
        else:
            await q.edit_message_text(
                f"🎰 {n}/{game['mode']} | مبلغ {amt}",
                reply_markup=InlineKeyboardMarkup([[btn("✅ شرکت", own("cas_join", user.id, cid), "success")]]),
            )
        return

    if action == "clan_create":
        if u.effective_chat.type == ChatType.PRIVATE:
            await q.answer("در گپ", show_alert=True); return
        # handled via message - set state
        set_st(c, "clan_name")
        await q.edit_message_text("اسم انجمن را بفرست:")
        return
    if action == "clan_leave":
        gid = str(q.message.chat_id)
        for cl in d.get("clans", {}).get(gid, []):
            if uid_s(user.id) in cl.get("members", []):
                cl["members"] = [m for m in cl["members"] if m != uid_s(user.id)]
            if cl.get("leader") == uid_s(user.id):
                cl["leader"] = (cl.get("members") or [None])[0]
        p["clan"] = None
        save(d)
        await q.edit_message_text("از انجمن خارج شدی.")
        return
    if action == "clan_inv_hint":
        await q.answer("روی فرد ریپلای کن و بنویس: دعوت", show_alert=True); return
    if action == "clan_kick_hint":
        await q.answer("روی فرد ریپلای کن و بنویس: اخراج", show_alert=True); return
    if action == "clan_accept":
        # rest: gid, clan_idx, invitee already owner check is invitee
        await q.edit_message_text("عضویت ثبت شد (اگر جا بود).")
        # simplified accept in text invite flow
        return

    if action == "reg":
        idx = int(rest[0]) if rest else -1
        regs = d.get("regions") or []
        if 0 <= idx < len(regs):
            r = regs[idx]
            p["region"] = r.get("name")
            save(d)
            cap = f"📍 {r.get('name')}\n{r.get('desc','')}\nخطر: {r.get('danger','-')}\nپاداش: {r.get('reward','-')}"
            if r.get("file_id"):
                try:
                    await q.message.reply_photo(r["file_id"], caption=cap)
                    return
                except Exception:
                    pass
            await q.edit_message_text(cap)
        return

    if action == "map":
        reg = p.get("region")
        r = next((x for x in d.get("regions", []) if x.get("name") == reg), None)
        if r and r.get("file_id"):
            await q.message.reply_photo(r["file_id"], caption=f"نقشه: {reg}")
        else:
            await q.answer("عکس منطقه نیست", show_alert=True)
        return

def apply_item(p, it):
    t = it.get("type")
    bp = p.setdefault("backpack", {})
    if t == "gun":
        g = {"name": it.get("name"), "damage": int(it.get("damage", 10)), "id": it.get("id")}
        bp.setdefault("guns", []).append(g)
        if not p.get("active_gun"):
            p["active_gun"] = g
    elif t == "ammo":
        bp["ammo"] = bp.get("ammo", 0) + int(it.get("amount", 10))
    elif t == "water":
        bp["water"] = bp.get("water", 0) + int(it.get("amount", 1))
    elif t == "food":
        bp["food"] = bp.get("food", 0) + int(it.get("amount", 1))
    elif t == "medkit":
        bp.setdefault("medkits", []).append({"heal_pct": float(it.get("heal_pct", 50)), "name": it.get("name")})
    elif t == "grenade":
        bp["grenades"] = bp.get("grenades", 0) + int(it.get("amount", 1))
    elif t == "vest":
        bonus = int(it.get("hp_bonus", 20))
        p["vest"] = {"name": it.get("name"), "hp_bonus": bonus}
        bp["vest"] = p["vest"]
        p["hp_max"] = 100 + (p.get("level", 1) - 1) * 10 + bonus
    elif t == "shelter":
        p["shelter"] = {"name": it.get("name"), "capacity": int(it.get("capacity", 30))}

# ---------------- admin ----------------
async def admin_cb(q, c, d, data):
    await q.answer()
    if data == "a_shop":
        kb = InlineKeyboardMarkup([
            [btn("➕ بخش جدید", "a_sec_add", "success")],
            [btn("➕ آیتم در بخش", "a_item_add", "primary")],
            [btn("📋 لیست بخش‌ها", "a_sec_list", "primary")],
        ])
        await q.edit_message_text("فروشگاه ادمین:", reply_markup=kb)
        return
    if data == "a_sec_add":
        set_st(c, "a_sec_name")
        await q.edit_message_text("نام بخش (مثلاً گان):")
        return
    if data == "a_item_add":
        set_st(c, "a_item_sec")
        secs = ", ".join(d.get("shop_sections", {}).keys()) or "-"
        await q.edit_message_text(f"آیدی بخش را بفرست:\n{secs}")
        return
    if data == "a_sec_list":
        lines = ["بخش‌ها:"]
        for sid, sec in d.get("shop_sections", {}).items():
            lines.append(f"• {sid}: {sec.get('name')} ({len(sec.get('items',[]))} آیتم)")
        await q.edit_message_text("\n".join(lines) or "خالی")
        return
    if data == "a_boss":
        set_st(c, "a_boss_name")
        await q.edit_message_text("اسم باس:")
        return
    if data == "a_region":
        set_st(c, "a_reg_name")
        await q.edit_message_text("اسم منطقه:")
        return
    if data == "a_pfp":
        set_st(c, "a_pfp_range")
        await q.edit_message_text("بازه لول مثل 0-15 بعد عکس بفرست")
        return
    if data == "a_give":
        set_st(c, "a_give_user")
        await q.edit_message_text("یوزرنیم کاربر (بدون @):")
        return
    if data == "a_herd":
        set_st(c, "a_herd_chat")
        await q.edit_message_text("آیدی عددی گپ برای گله:")
        return
    if data == "a_spawn":
        set_st(c, "a_spawn_chat")
        await q.edit_message_text("آیدی گپ برای اسپاون باس (اول قالب باس بساز):")
        return

async def admin_state(u, c, d, text, st):
    kind = st["kind"]
    extra = st.get("extra") or {}
    if kind == "a_sec_name":
        sid = re.sub(r"\W+", "", text.lower())[:12] or f"s{random.randint(100,999)}"
        d.setdefault("shop_sections", {})[sid] = {"name": text.strip(), "items": []}
        save(d); clear_st(c)
        await u.message.reply_text(f"✅ بخش {text} با آیدی `{sid}`", parse_mode="Markdown")
        return
    if kind == "a_item_sec":
        sid = text.strip()
        if sid not in d.get("shop_sections", {}):
            await u.message.reply_text("بخش نیست")
            return
        set_st(c, "a_item_type", {"sid": sid})
        await u.message.reply_text("نوع: gun | ammo | water | food | medkit | grenade | vest | shelter")
        return
    if kind == "a_item_type":
        t = text.strip().lower()
        if t not in ("gun", "ammo", "water", "food", "medkit", "grenade", "vest", "shelter"):
            await u.message.reply_text("نوع نامعتبر")
            return
        extra["type"] = t
        set_st(c, "a_item_name", extra)
        await u.message.reply_text("اسم آیتم:")
        return
    if kind == "a_item_name":
        extra["name"] = text
        set_st(c, "a_item_desc", extra)
        await u.message.reply_text("توضیحات:")
        return
    if kind == "a_item_desc":
        extra["desc"] = text
        set_st(c, "a_item_price", extra)
        await u.message.reply_text("قیمت:")
        return
    if kind == "a_item_price":
        try:
            extra["price"] = int(text)
        except ValueError:
            await u.message.reply_text("عدد")
            return
        t = extra.get("type")
        if t == "gun":
            set_st(c, "a_item_dmg", extra); await u.message.reply_text("دمیج:"); return
        if t == "vest":
            set_st(c, "a_item_hp", extra); await u.message.reply_text("افزایش HP:"); return
        if t == "shelter":
            set_st(c, "a_item_cap", extra); await u.message.reply_text("ظرفیت انبار:"); return
        if t == "medkit":
            set_st(c, "a_item_heal", extra); await u.message.reply_text("درصد هیل (15 یا 50 یا 100):"); return
        if t in ("ammo", "water", "food", "grenade"):
            set_st(c, "a_item_amt", extra); await u.message.reply_text("مقدار در هر خرید:"); return
        set_st(c, "a_item_photo", extra); await u.message.reply_text("عکس یا - :"); return
    if kind == "a_item_dmg":
        extra["damage"] = int(text); extra["effect_label"] = f"دمیج {text}"
        set_st(c, "a_item_photo", extra); await u.message.reply_text("عکس یا -:"); return
    if kind == "a_item_hp":
        extra["hp_bonus"] = int(text); extra["effect_label"] = f"+{text} HP"
        set_st(c, "a_item_photo", extra); await u.message.reply_text("عکس یا -:"); return
    if kind == "a_item_cap":
        extra["capacity"] = int(text); extra["effect_label"] = f"ظرفیت {text}"
        set_st(c, "a_item_photo", extra); await u.message.reply_text("عکس یا -:"); return
    if kind == "a_item_heal":
        extra["heal_pct"] = float(text); extra["effect_label"] = f"هیل {text}%"
        set_st(c, "a_item_photo", extra); await u.message.reply_text("عکس یا -:"); return
    if kind == "a_item_amt":
        extra["amount"] = int(text); extra["effect_label"] = f"×{text}"
        set_st(c, "a_item_photo", extra); await u.message.reply_text("عکس یا -:"); return
    if kind == "a_item_photo":
        fid = None
        if u.message.photo:
            fid = u.message.photo[-1].file_id
        elif text.strip() != "-":
            await u.message.reply_text("عکس بفرست یا -")
            return
        sid = extra["sid"]
        item = {
            "id": f"i{random.randint(1000,9999)}",
            "name": extra.get("name"),
            "desc": extra.get("desc", ""),
            "price": extra.get("price", 0),
            "type": extra.get("type"),
            "damage": extra.get("damage"),
            "hp_bonus": extra.get("hp_bonus"),
            "capacity": extra.get("capacity"),
            "heal_pct": extra.get("heal_pct"),
            "amount": extra.get("amount", 1),
            "effect_label": extra.get("effect_label", ""),
            "file_id": fid,
        }
        d["shop_sections"][sid].setdefault("items", []).append(item)
        save(d); clear_st(c)
        await u.message.reply_text(f"✅ آیتم {item['name']} اضافه شد.")
        return
    if kind == "a_boss_name":
        set_st(c, "a_boss_desc", {"name": text}); await u.message.reply_text("توضیح:"); return
    if kind == "a_boss_desc":
        extra["desc"] = text; set_st(c, "a_boss_hp", extra); await u.message.reply_text("HP:"); return
    if kind == "a_boss_hp":
        extra["hp"] = int(text); set_st(c, "a_boss_pow", extra); await u.message.reply_text("قدرت دمیج باس:"); return
    if kind == "a_boss_pow":
        extra["power"] = int(text); set_st(c, "a_boss_photo", extra); await u.message.reply_text("عکس یا -:"); return
    if kind == "a_boss_photo":
        fid = u.message.photo[-1].file_id if u.message.photo else None
        d.setdefault("boss_templates", []).append({
            "name": extra.get("name"), "desc": extra.get("desc"), "hp": extra.get("hp", 1000),
            "power": extra.get("power", 20), "file_id": fid,
        })
        save(d); clear_st(c)
        await u.message.reply_text("✅ قالب باس ذخیره شد.")
        return
    if kind == "a_reg_name":
        set_st(c, "a_reg_desc", {"name": text}); await u.message.reply_text("توضیح:"); return
    if kind == "a_reg_desc":
        extra["desc"] = text; set_st(c, "a_reg_danger", extra); await u.message.reply_text("سطح خطر:"); return
    if kind == "a_reg_danger":
        extra["danger"] = text; set_st(c, "a_reg_reward", extra); await u.message.reply_text("پاداش توضیح:"); return
    if kind == "a_reg_reward":
        extra["reward"] = text; set_st(c, "a_reg_photo", extra); await u.message.reply_text("عکس یا -:"); return
    if kind == "a_reg_photo":
        fid = u.message.photo[-1].file_id if u.message.photo else None
        d.setdefault("regions", []).append({**extra, "file_id": fid})
        save(d); clear_st(c)
        await u.message.reply_text("✅ منطقه اضافه شد.")
        return
    if kind == "a_pfp_range":
        m = re.match(r"(\d+)\s*-\s*(\d+)", text)
        if not m:
            await u.message.reply_text("مثل 0-15"); return
        set_st(c, "a_pfp_photo", {"min": int(m.group(1)), "max": int(m.group(2))})
        await u.message.reply_text("عکس پروفایل این بازه:")
        return
    if kind == "a_pfp_photo":
        if not u.message.photo:
            await u.message.reply_text("عکس بفرست"); return
        d.setdefault("profile_imgs", []).append({
            "min": extra["min"], "max": extra["max"], "file_id": u.message.photo[-1].file_id,
        })
        save(d); clear_st(c)
        await u.message.reply_text("✅ عکس پروفایل ثبت شد.")
        return
    if kind == "a_herd_chat":
        try:
            chat_id = int(text)
        except ValueError:
            await u.message.reply_text("آیدی عددی"); return
        set_st(c, "a_herd_photo", {"chat_id": chat_id})
        await u.message.reply_text("عکس گله یا - :")
        return
    if kind == "a_herd_photo":
        chat_id = extra["chat_id"]
        fid = u.message.photo[-1].file_id if u.message.photo else None
        clear_st(c)
        kb = InlineKeyboardMarkup([[btn("🟢 رفتن به پناهگاه", "herd_shelter", "success")]])
        try:
            if fid:
                await c.bot.send_photo(chat_id, fid, caption="🐺 گله زامبی حمله کرد! سریع پناهگاه.", reply_markup=kb)
            else:
                await c.bot.send_message(chat_id, "🐺 گله زامبی حمله کرد! سریع پناهگاه.", reply_markup=kb)
            await u.message.reply_text("ارسال شد. بعد از ۱۰ دقیقه خودتان اعلام رد شدن کنید یا بعداً جاب زمان‌بند اضافه شود.")
        except Exception as e:
            await u.message.reply_text(f"خطا: {e}")
        return
    if kind == "a_spawn_chat":
        try:
            chat_id = int(text)
        except ValueError:
            await u.message.reply_text("عدد"); return
        tpls = d.get("boss_templates") or []
        if not tpls:
            await u.message.reply_text("قالب باس نیست"); clear_st(c); return
        tpl = random.choice(tpls)
        clear_st(c)
        body = f"👹 <b>{tpl['name']}</b>\n{tpl.get('desc','')}\n❤️ {tpl['hp']}/{tpl['hp']}\nریپلای + شلیک"
        try:
            if tpl.get("file_id"):
                msg = await c.bot.send_photo(chat_id, tpl["file_id"], caption=body, parse_mode="HTML")
            else:
                msg = await c.bot.send_message(chat_id, body, parse_mode="HTML")
            mid = str(msg.message_id)
            d.setdefault("active_bosses", {})[mid] = {
                "name": tpl["name"], "desc": tpl.get("desc"), "hp": tpl["hp"], "hp_max": tpl["hp"],
                "power": tpl.get("power", 20), "chat_id": chat_id, "message_id": msg.message_id,
                "spawned": now_ts(), "last_hit": now_ts(), "dmg": {},
            }
            save(d)
            await u.message.reply_text("باس اسپاون شد.")
        except Exception as e:
            await u.message.reply_text(f"خطا: {e}")
        return
    if kind == "clan_name":
        gid = str(u.effective_chat.id)
        clans = d.setdefault("clans", {}).setdefault(gid, [])
        if len(clans) >= 3:
            await u.message.reply_text("حداکثر ۳ کلن در گپ")
            clear_st(c); return
        cl = {"name": text[:32], "leader": uid_s(u.effective_user.id), "members": [uid_s(u.effective_user.id)]}
        clans.append(cl)
        p = eu(d, u.effective_user)
        p["clan"] = text[:32]
        save(d); clear_st(c)
        await u.message.reply_text(f"✅ انجمن {text} ساخته شد.")
        return

# herd shelter button (no owner — anyone)
async def on_cb_public(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    if q.data != "herd_shelter":
        return False
    d = load()
    p = eu(d, q.from_user)
    p["in_shelter"] = True
    p["shelter_until"] = now_ts() + 600
    save(d)
    await q.answer("رفتی پناهگاه", show_alert=True)
    return True

async def on_cb_router(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if await on_cb_public(u, c):
        return
    await on_cb(u, c)

async def on_invite_kick(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.message.text:
        return
    text = u.message.text.strip()
    if text not in ("دعوت", "اخراج"):
        return
    if not u.message.reply_to_message:
        return
    d = load()
    user = u.effective_user
    gid = str(u.effective_chat.id)
    clans = d.get("clans", {}).get(gid, [])
    my = next((cl for cl in clans if cl.get("leader") == uid_s(user.id)), None)
    if not my:
        await u.message.reply_text("فقط لیدر")
        return
    target = u.message.reply_to_message.from_user
    if text == "دعوت":
        if len(my.get("members", [])) >= 12:
            await u.message.reply_text("ظرفیت ۱۲ پر است")
            return
        kb = InlineKeyboardMarkup([
            [btn("✅ عضویت", f"cacc:{target.id}:{gid}:{my['name']}", "success"),
             btn("❌ رد", f"crej:{target.id}", "danger")],
        ])
        await u.message.reply_text(
            f"{target.full_name} دعوت به {my['name']}",
            reply_markup=kb,
        )
        return
    if text == "اخراج":
        tid = uid_s(target.id)
        my["members"] = [m for m in my.get("members", []) if m != tid]
        if tid in d["users"]:
            d["users"][tid]["clan"] = None
        save(d)
        await u.message.reply_text("اخراج شد.")

async def on_clan_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    data = q.data or ""
    if data.startswith("cacc:"):
        _, tid, gid, name = (data.split(":") + ["", "", "", ""])[:4]
        if str(q.from_user.id) != tid:
            await q.answer("این دعوت برای تو نیست", show_alert=True); return
        d = load()
        clans = d.get("clans", {}).get(gid, [])
        cl = next((x for x in clans if x.get("name") == name), None)
        if not cl or len(cl.get("members", [])) >= 12:
            await q.answer("جا نیست", show_alert=True); return
        uid = uid_s(q.from_user.id)
        if uid not in cl["members"]:
            cl["members"].append(uid)
        p = eu(d, q.from_user)
        p["clan"] = name
        save(d)
        await q.edit_message_text(f"✅ عضو {name} شدی.")
        return
    if data.startswith("crej:"):
        tid = data.split(":")[1]
        if str(q.from_user.id) != tid:
            await q.answer("نه", show_alert=True); return
        await q.edit_message_text("رد شد.")
        return
    await on_cb_router(u, c)


async def on_photo(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.message.photo:
        return
    if not is_admin(u.effective_user.id):
        return
    if u.effective_chat.type != ChatType.PRIVATE:
        return
    st = get_st(c)
    if not st:
        return
    d = load()
    # reuse admin_state with dummy text "-" for photo steps
    await admin_state(u, c, d, "-", st)


async def on_added(u: Update, c: ContextTypes.DEFAULT_TYPE):
    r = u.my_chat_member
    if r.new_chat_member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
        if r.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            try:
                await c.bot.send_message(r.chat.id, "بازی بقا فعال شد.\n" + HELP, parse_mode="HTML")
            except Exception:
                pass

async def post_init(app: Application):
    if app.job_queue:
        app.job_queue.run_daily(job_bank_interest, time=dtime(hour=0, minute=0, tzinfo=TZ))
        app.job_queue.run_daily(job_night, time=dtime(hour=0, minute=30, tzinfo=TZ))
        app.job_queue.run_repeating(job_boss_timeout, interval=60, first=30)
    else:
        log.warning("job-queue not installed")

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CallbackQueryHandler(on_clan_cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.TEXT, on_invite_kick))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(ChatMemberHandler(on_added, ChatMemberHandler.MY_CHAT_MEMBER))
    log.info("zombie bot up")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
