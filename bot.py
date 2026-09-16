# -*- coding: utf-8 -*-
"""
ربات رُخ — سبک اقتصاد گروهی + رکس + قلعه + عملیات + کارگاه + بانک
نام‌ها عوض شده؛ مینی‌اپ و گیفت/NFT ندارد.
"""
from __future__ import annotations
import json, os, re, time, logging, random, asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters,
)
from telegram.constants import ChatType

# ========== تنظیمات اولیه ==========
BOT_TOKEN = "8727762178:AAGrdb5XFjhkcdoOEIFy1s8U71idRpN0DX8"
ADMIN_ID = 7530457395
DATA = "rokx_game.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("rokx")


def D():
    return {
        "admins": [ADMIN_ID],
        "users": {},          # uid -> {name, username, xp, level, reks_count, rescued, last_reks, jail_until}
        "balances": {},       # uid -> اعتبار
        "bank": {},           # uid -> موجودی بانک
        "chats": {},          # chat_id -> {title, level, xp, treasury, last_upgrade}
        "workshop": {},       # uid -> {level, busy_until, product}
        "ops": {},            # gid -> عملیات فعال
        "settings": {
            "reks_base": 1000,           # پاداش پایه رکس
            "reks_per_castle_lv": 200,   # اضافه به‌ازای هر سطح قلعه
            "reks_cd_base": 3600,        # کول‌داون پایه ثانیه
            "reks_cd_reduce_per_lv": 60, # کم شدن کول‌داون به‌ازای سطح قلعه
            "reks_cd_min": 300,          # حداقل کول‌داون
            "transfer_tax": 0.01,        # کارمزد انتقال
            "bank_daily_rate": 0.02,     # سود تقریبی (نمایشی؛ پرداخت دستی/روزانه ساده)
            "bank_cap": 5_000_000,
            "jail_seconds": 7200,
            "jail_fine": 125000,
            "xp_reks": 2,
            "xp_op_success": 8,
            "level_xp_base": 50,
            "castle_xp_per_reks": 1,
            "castle_levels": {
                # level: {need_xp, member_bonus}
            },
            "op_places": [
                {"id": "shop", "name": "فروشگاه", "min_lv": 1, "loot_min": 100000, "loot_max": 175000, "slots": 10, "fail_chance": 0.15},
                {"id": "bank", "name": "صندوق", "min_lv": 3, "loot_min": 200000, "loot_max": 350000, "slots": 8, "fail_chance": 0.22},
                {"id": "vault", "name": "مخزن", "min_lv": 5, "loot_min": 400000, "loot_max": 700000, "slots": 6, "fail_chance": 0.30},
            ],
            "op_need_players": 2,
            "workshop_base_time": 1800,
            "workshop_base_reward": 50000,
            "currency": "اعتبار",
            "cmd_reks": ["رکس", "rex", "رخ"],
        },
        "codes": {},          # code -> {amount, left}
        "known_chats": [],
    }


def load():
    if os.path.exists(DATA):
        try:
            with open(DATA, "r", encoding="utf-8") as f:
                d = json.load(f)
            base = D()
            for k, v in base.items():
                if k == "settings" and isinstance(d.get("settings"), dict):
                    for sk, sv in base["settings"].items():
                        d.setdefault("settings", {}).setdefault(sk, sv)
                else:
                    d.setdefault(k, v)
            if ADMIN_ID not in [int(x) for x in d.get("admins", [])]:
                d.setdefault("admins", []).insert(0, ADMIN_ID)
            return d
        except Exception as e:
            log.error(e)
    return D()


def save(d):
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def is_admin(uid):
    return int(uid) in {int(x) for x in load().get("admins", [ADMIN_ID])}


def is_main(uid):
    return int(uid) == ADMIN_ID


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


def btn(text, data, style=None):
    kw = {"text": str(text)[:64], "callback_data": data}
    if style in ("danger", "success", "primary"):
        kw["style"] = style
    try:
        return InlineKeyboardButton(**kw)
    except TypeError:
        kw.pop("style", None)
        return InlineKeyboardButton(**kw)


def set_st(c, kind, extra=None):
    c.user_data["st"] = {"kind": kind, "extra": extra or {}, "ts": time.time()}


def get_st(c):
    st = c.user_data.get("st")
    if not st:
        return None
    if time.time() - st.get("ts", 0) > 900:
        c.user_data.pop("st", None)
        return None
    return st


def clear_st(c):
    c.user_data.pop("st", None)


def mention(uid, name=None):
    if not name:
        name = (load().get("users") or {}).get(str(uid), {}).get("name") or str(uid)
    return f'<a href="tg://user?id={uid}">{name}</a>'


def bal(d, uid):
    return int(d.get("balances", {}).get(str(uid), 0))


def set_bal(d, uid, v):
    d.setdefault("balances", {})[str(uid)] = max(0, int(v))


def add_bal(d, uid, delta):
    set_bal(d, uid, bal(d, uid) + int(delta))
    return bal(d, uid)


def ensure_user(d, user):
    if not user or getattr(user, "is_bot", False):
        return
    u = d.setdefault("users", {}).setdefault(str(user.id), {})
    u["name"] = user.full_name or u.get("name") or str(user.id)
    u["username"] = user.username or u.get("username") or ""
    u.setdefault("xp", 0)
    u.setdefault("level", 1)
    u.setdefault("reks_count", 0)
    u.setdefault("rescued", 0)
    u.setdefault("last_reks", 0)
    u.setdefault("jail_until", 0)
    d.setdefault("balances", {}).setdefault(str(user.id), 0)
    d.setdefault("bank", {}).setdefault(str(user.id), 0)


def ensure_chat(d, chat):
    if not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return None
    c = d.setdefault("chats", {}).setdefault(str(chat.id), {})
    c["title"] = chat.title or c.get("title") or str(chat.id)
    c.setdefault("level", 1)
    c.setdefault("xp", 0)
    c.setdefault("treasury", 0)
    if int(chat.id) not in [int(x) for x in d.get("known_chats", [])]:
        d.setdefault("known_chats", []).append(int(chat.id))
    return c


def user_level_from_xp(d, xp):
    base = int(d.get("settings", {}).get("level_xp_base") or 50)
    lv = 1
    need = base
    left = int(xp)
    while left >= need:
        left -= need
        lv += 1
        need = int(base * (1.35 ** (lv - 1)))
    return lv, need - left


def add_xp(d, uid, amount):
    u = d.setdefault("users", {}).setdefault(str(uid), {})
    u["xp"] = int(u.get("xp") or 0) + int(amount)
    lv, _ = user_level_from_xp(d, u["xp"])
    u["level"] = lv


def castle_level(d, chat_id):
    return int(d.get("chats", {}).get(str(chat_id), {}).get("level") or 1)


def reks_reward(d, chat_id):
    s = d["settings"]
    clv = castle_level(d, chat_id) if chat_id else 1
    return int(s["reks_base"] + s["reks_per_castle_lv"] * (clv - 1))


def reks_cooldown(d, chat_id):
    s = d["settings"]
    clv = castle_level(d, chat_id) if chat_id else 1
    cd = int(s["reks_cd_base"] - s["reks_cd_reduce_per_lv"] * (clv - 1))
    return max(int(s["reks_cd_min"]), cd)


def jail_left(d, uid):
    until = float(d.get("users", {}).get(str(uid), {}).get("jail_until") or 0)
    return max(0, int(until - time.time()))


def fmt_time(sec):
    sec = int(max(0, sec))
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}س {m}د"
    if m:
        return f"{m}د {s}ث"
    return f"{s}ث"


# ---------- کیبوردها ----------
def admin_kb():
    return InlineKeyboardMarkup([
        [btn("➕ واریز اعتبار", "a_add", "success"), btn("➖ کسر اعتبار", "a_sub", "danger")],
        [btn("📢 همگانی اعتبار", "a_add_all", "success"), btn("📨 پیام همگانی", "a_bcast", "primary")],
        [btn("⚙️ تنظیم رکس", "a_reks", "primary"), btn("🏰 تنظیم قلعه", "a_castle", "primary")],
        [btn("🔫 مکان‌های عملیات", "a_places", "primary"), btn("🏭 کارگاه", "a_workshop", "primary")],
        [btn("🏛 بانک / مالیات", "a_economy", "primary"), btn("🎟 کد هدیه", "a_code", "success")],
        [btn("👤 ادمین‌ها", "a_admins", "primary"), btn("📊 آمار", "a_stats", "primary")],
        [btn("🗑 صفر کاربر", "a_zero_user", "danger"), btn("❌ بستن", "close", "danger")],
    ])


def profile_text(d, uid):
    u = d.get("users", {}).get(str(uid), {})
    cur = d["settings"].get("currency", "اعتبار")
    lv = int(u.get("level") or 1)
    xp = int(u.get("xp") or 0)
    _, to_next = user_level_from_xp(d, xp)
    jl = jail_left(d, uid)
    lines = [
        "👤 <b>پروفایل رُخ</b>",
        "",
        f"نام: {mention(uid, u.get('name'))}",
        f"سطح: <b>{lv}</b> | XP تا سطح بعد: {num(to_next)}",
        f"{cur}: <b>{num(bal(d, uid))}</b>",
        f"بانک: <b>{num(d.get('bank', {}).get(str(uid), 0))}</b>",
        f"تعداد رکس: <b>{num(u.get('reks_count') or 0)}</b>",
        f"نجات‌داده‌شده: <b>{num(u.get('rescued') or 0)}</b>",
    ]
    if jl:
        lines.append(f"⛔ بازداشت: {fmt_time(jl)}")
    return "\n".join(lines)


def help_text(d):
    cur = d["settings"].get("currency", "اعتبار")
    return f"""📖 <b>راهنما رُخ</b>

🪙 <b>اقتصاد</b>
• <code>رکس</code> — دریافت {cur} (وابسته به سطح قلعه گپ)
• <code>موجودی</code> — موجودی
• <code>انتقال 1k</code> + ریپلای — انتقال
• <code>بانک</code> — واریز/برداشت

👤 <b>پروفایل</b>
• <code>پروفایل</code> — سطح، رکس، نجات، {cur}

🏰 <b>قلعه (گپ)</b>
• <code>قلعه</code> — سطح و خزانه گپ
• هر رکس به قلعه XP می‌دهد

🔫 <b>عملیات</b>
• <code>عملیات</code> — سرقت تیمی (نقش و لوت)
• شکست → بازداشت

🏭 <b>کارگاه</b>
• <code>کارگاه</code> — تولید با تایمر

🎛 ادمین: <code>/admin</code> (فقط پیوی)
"""


# ---------- handlers ----------
async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    d = load()
    ensure_user(d, u.effective_user)
    save(d)
    await u.message.reply_text(
        "به ربات <b>رُخ</b> خوش آمدی.\nبرای راهنما: <code>راهنما</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [btn("👤 پروفایل", "my_profile", "primary")],
            [btn("📖 راهنما", "help", "primary")],
        ]),
    )


async def cmd_admin(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_chat.type != ChatType.PRIVATE or not is_admin(u.effective_user.id):
        return
    await u.message.reply_text("🎛 پنل ادمین رُخ", reply_markup=admin_kb())


async def on_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    await q.answer()
    d = load()
    user = u.effective_user
    ensure_user(d, user)
    data = q.data or ""

    if data == "close":
        try:
            await q.message.delete()
        except Exception:
            pass
        return
    if data == "help":
        await q.edit_message_text(help_text(d), parse_mode="HTML")
        return
    if data == "my_profile":
        await q.edit_message_text(profile_text(d, user.id), parse_mode="HTML")
        return
    if data == "a_home" and is_admin(user.id):
        await q.edit_message_text("🎛 پنل ادمین", reply_markup=admin_kb())
        return

    if not is_admin(user.id):
        return

    # --- admin actions ---
    if data == "a_add":
        set_st(c, "a_add")
        await q.edit_message_text("آیدی عددی و مبلغ را بفرست:\n<code>123456 10k</code>", parse_mode="HTML")
        return
    if data == "a_sub":
        set_st(c, "a_sub")
        await q.edit_message_text("آیدی و مبلغ کسر:\n<code>123456 5k</code>", parse_mode="HTML")
        return
    if data == "a_add_all":
        set_st(c, "a_add_all")
        await q.edit_message_text("مبلغ همگانی برای همه کاربران:")
        return
    if data == "a_bcast":
        set_st(c, "a_bcast")
        await q.edit_message_text("متن پیام همگانی (پیوی کاربران):")
        return
    if data == "a_reks":
        s = d["settings"]
        set_st(c, "a_reks_menu")
        txt = (
            "⚙️ <b>تنظیم رکس</b>\n\n"
            f"پایه: {num(s['reks_base'])}\n"
            f"به ازای سطح قلعه: {num(s['reks_per_castle_lv'])}\n"
            f"کول‌داون پایه: {fmt_time(s['reks_cd_base'])}\n"
            f"کاهش per level: {fmt_time(s['reks_cd_reduce_per_lv'])}\n"
            f"حداقل کول‌داون: {fmt_time(s['reks_cd_min'])}\n\n"
            "بفرست با فرمت:\n"
            "<code>پایه 1000</code>\n"
            "<code>اضافه 200</code>\n"
            "<code>کولداون 3600</code>\n"
            "<code>کاهش 60</code>\n"
            "<code>حداقل 300</code>"
        )
        await q.edit_message_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[btn("🔙", "a_home", "danger")]]))
        return
    if data == "a_castle":
        await q.edit_message_text(
            "🏰 سطح قلعه از XP جمع رکس گپ بالا می‌رود.\n"
            f"XP هر رکس برای قلعه: {d['settings'].get('castle_xp_per_reks', 1)}\n\n"
            "برای تغییر بفرست: <code>قلعه_اکسپی 2</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[btn("🔙", "a_home", "danger")]]),
        )
        set_st(c, "a_castle")
        return
    if data == "a_places":
        lines = ["🔫 مکان‌های عملیات\n"]
        for p in d["settings"].get("op_places", []):
            lines.append(
                f"• {p['name']} | سطح {p['min_lv']} | لوت {num(p['loot_min'])}-{num(p['loot_max'])} | شکست {int(p['fail_chance']*100)}%"
            )
        lines.append("\nویرایش پیشرفته بعداً از فایل تنظیمات / یا بفرست:\n<code>مکان فروشگاه لوت 150000 250000</code>")
        set_st(c, "a_places")
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[btn("🔙", "a_home", "danger")]]))
        return
    if data == "a_workshop":
        s = d["settings"]
        set_st(c, "a_workshop")
        await q.edit_message_text(
            f"🏭 کارگاه\nزمان پایه: {fmt_time(s['workshop_base_time'])}\nپاداش پایه: {num(s['workshop_base_reward'])}\n\n"
            "بفرست: <code>زمان 1800</code> یا <code>پاداش 50000</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[btn("🔙", "a_home", "danger")]]),
        )
        return
    if data == "a_economy":
        s = d["settings"]
        set_st(c, "a_economy")
        await q.edit_message_text(
            f"🏛 اقتصاد\nکارمزد انتقال: {s['transfer_tax']}\nسقف بانک: {num(s['bank_cap'])}\nجریمه بازداشت: {num(s['jail_fine'])}\nمدت بازداشت: {fmt_time(s['jail_seconds'])}\n\n"
            "بفرست مثلا:\n<code>مالیات 0.01</code>\n<code>سقف_بانک 5000000</code>\n<code>جریمه 125000</code>\n<code>زندان 7200</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[btn("🔙", "a_home", "danger")]]),
        )
        return
    if data == "a_code":
        set_st(c, "a_code")
        await q.edit_message_text("فرمت کد هدیه:\n<code>کد مبلغ تعداد</code>\nمثال: <code>ROKX 10k 50</code>", parse_mode="HTML")
        return
    if data == "a_admins":
        ads = d.get("admins", [])
        lines = ["👤 ادمین‌ها\n"] + [f"• <code>{a}</code>" for a in ads]
        lines.append("\nاضافه: <code>ادمین + آیدی</code>\nحذف (فقط اصلی): <code>ادمین - آیدی</code>")
        set_st(c, "a_admins")
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[btn("🔙", "a_home", "danger")]]))
        return
    if data == "a_stats":
        await q.edit_message_text(
            f"📊 کاربران: {num(len(d.get('users', {})))}\n"
            f"گپ‌ها: {num(len(d.get('chats', {})))}\n"
            f"مجموع اعتبار: {num(sum(int(v) for v in d.get('balances', {}).values()))}",
            reply_markup=InlineKeyboardMarkup([[btn("🔙", "a_home", "danger")]]),
        )
        return
    if data == "a_zero_user":
        set_st(c, "a_zero_user")
        await q.edit_message_text("آیدی کاربری که صفر شود:")
        return


async def on_text(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.message.text:
        return
    d = load()
    user = u.effective_user
    chat = u.effective_chat
    ensure_user(d, user)
    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        ensure_chat(d, chat)
    text = (u.message.text or "").strip()
    st = get_st(c)
    cur = d["settings"].get("currency", "اعتبار")

    # ----- admin states (PM) -----
    if chat.type == ChatType.PRIVATE and is_admin(user.id) and st:
        kind = st["kind"]
        if kind in ("a_add", "a_sub"):
            parts = text.split()
            if len(parts) < 2:
                await u.message.reply_text("فرمت: آیدی مبلغ")
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
            ensure_user(d, type("U", (), {"id": tid, "full_name": str(tid), "username": "", "is_bot": False})())
            if kind == "a_add":
                add_bal(d, tid, amt)
                msg = f"✅ واریز {num(amt)} به {tid}"
            else:
                set_bal(d, tid, bal(d, tid) - amt)
                msg = f"✅ کسر {num(amt)} از {tid}"
            save(d)
            clear_st(c)
            await u.message.reply_text(msg, reply_markup=admin_kb())
            try:
                await c.bot.send_message(tid, f"{num(amt)} {cur} از طرف مدیریت.")
            except Exception:
                pass
            return
        if kind == "a_add_all":
            amt = parse_amount(text)
            if not amt:
                await u.message.reply_text("مبلغ نامعتبر")
                return
            n = 0
            for uid in list(d.get("users", {}).keys()):
                add_bal(d, uid, amt)
                n += 1
            save(d)
            clear_st(c)
            await u.message.reply_text(f"همگانی {num(amt)} به {n} نفر", reply_markup=admin_kb())
            return
        if kind == "a_bcast":
            ok = fail = 0
            for uid in list(d.get("users", {}).keys()):
                try:
                    await c.bot.send_message(int(uid), text)
                    ok += 1
                except Exception:
                    fail += 1
            clear_st(c)
            await u.message.reply_text(f"ارسال شد ✅{ok} ❌{fail}", reply_markup=admin_kb())
            return
        if kind == "a_reks_menu":
            m = re.match(r"^(پایه|اضافه|کولداون|کاهش|حداقل)\s+(\d+)$", text)
            if not m:
                await u.message.reply_text("فرمت را رعایت کن")
                return
            key, val = m.group(1), int(m.group(2))
            mp = {"پایه": "reks_base", "اضافه": "reks_per_castle_lv", "کولداون": "reks_cd_base", "کاهش": "reks_cd_reduce_per_lv", "حداقل": "reks_cd_min"}
            d["settings"][mp[key]] = val
            save(d)
            await u.message.reply_text("ذخیره شد.", reply_markup=admin_kb())
            clear_st(c)
            return
        if kind == "a_castle":
            m = re.match(r"قلعه_اکسپی\s+(\d+)", text)
            if m:
                d["settings"]["castle_xp_per_reks"] = int(m.group(1))
                save(d)
                clear_st(c)
                await u.message.reply_text("OK", reply_markup=admin_kb())
            return
        if kind == "a_workshop":
            m = re.match(r"^(زمان|پاداش)\s+(\d+)$", text)
            if m:
                if m.group(1) == "زمان":
                    d["settings"]["workshop_base_time"] = int(m.group(2))
                else:
                    d["settings"]["workshop_base_reward"] = int(m.group(2))
                save(d)
                clear_st(c)
                await u.message.reply_text("OK", reply_markup=admin_kb())
            return
        if kind == "a_economy":
            m = re.match(r"^(مالیات|سقف_بانک|جریمه|زندان)\s+([\d.]+)$", text)
            if m:
                k, v = m.group(1), float(m.group(2))
                if k == "مالیات":
                    d["settings"]["transfer_tax"] = v
                elif k == "سقف_بانک":
                    d["settings"]["bank_cap"] = int(v)
                elif k == "جریمه":
                    d["settings"]["jail_fine"] = int(v)
                else:
                    d["settings"]["jail_seconds"] = int(v)
                save(d)
                clear_st(c)
                await u.message.reply_text("OK", reply_markup=admin_kb())
            return
        if kind == "a_code":
            parts = text.split()
            if len(parts) < 3:
                await u.message.reply_text("کد مبلغ تعداد")
                return
            code, amt, left = parts[0].upper(), parse_amount(parts[1]), parse_amount(parts[2])
            if not amt or not left:
                await u.message.reply_text("نامعتبر")
                return
            d.setdefault("codes", {})[code] = {"amount": amt, "left": int(left)}
            save(d)
            clear_st(c)
            await u.message.reply_text(f"کد {code} ثبت شد.", reply_markup=admin_kb())
            return
        if kind == "a_admins":
            m = re.match(r"ادمین\s*([+-])\s*(\d+)", text)
            if m:
                op, aid = m.group(1), int(m.group(2))
                ads = [int(x) for x in d.get("admins", [])]
                if op == "+":
                    if aid not in ads:
                        ads.append(aid)
                    d["admins"] = ads
                    save(d)
                    await u.message.reply_text("اضافه شد", reply_markup=admin_kb())
                elif is_main(user.id):
                    if aid != ADMIN_ID and aid in ads:
                        ads.remove(aid)
                    d["admins"] = ads
                    save(d)
                    await u.message.reply_text("حذف شد", reply_markup=admin_kb())
                clear_st(c)
            return
        if kind == "a_zero_user":
            try:
                tid = int(text.strip())
            except ValueError:
                await u.message.reply_text("آیدی عددی")
                return
            set_bal(d, tid, 0)
            d.setdefault("bank", {})[str(tid)] = 0
            save(d)
            clear_st(c)
            await u.message.reply_text("صفر شد", reply_markup=admin_kb())
            return

    low = re.sub(r"^@\w+\s+", "", text)
    low = re.sub(r"^/(\w+)@\w+", r"/\1", low)
    cmd = low.strip()

    # ----- راهنما / پروفایل / موجودی -----
    if re.fullmatch(r"/?(راهنما|help)", cmd, re.I):
        await u.message.reply_text(help_text(d), parse_mode="HTML")
        return
    if re.fullmatch(r"/?(پروفایل|profile)", cmd, re.I):
        await u.message.reply_text(profile_text(d, user.id), parse_mode="HTML")
        return
    if re.fullmatch(r"/?(موجودی|bal)", cmd, re.I):
        await u.message.reply_text(f"{cur}: <b>{num(bal(d, user.id))}</b>", parse_mode="HTML")
        return

    # ----- کد هدیه -----
    if cmd.upper() in d.get("codes", {}):
        code = cmd.upper()
        info = d["codes"][code]
        if int(info.get("left") or 0) < 1:
            await u.message.reply_text("کد تمام شده")
            return
        claimed = info.setdefault("claimed", [])
        if user.id in claimed:
            await u.message.reply_text("قبلاً گرفتی")
            return
        add_bal(d, user.id, int(info["amount"]))
        info["left"] = int(info["left"]) - 1
        claimed.append(user.id)
        save(d)
        await u.message.reply_text(f"✅ {num(info['amount'])} {cur} دریافت شد")
        return

    # ----- رکس -----
    reks_cmds = d["settings"].get("cmd_reks") or ["رکس"]
    if any(re.fullmatch(rf"/?{re.escape(x)}", cmd, re.I) for x in reks_cmds):
        if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            await u.message.reply_text("رکس فقط داخل گپ فعال است.")
            return
        jl = jail_left(d, user.id)
        if jl:
            await u.message.reply_text(f"در بازداشت هستی: {fmt_time(jl)}")
            return
        urow = d["users"][str(user.id)]
        cd = reks_cooldown(d, chat.id)
        left = int(urow.get("last_reks") or 0) + cd - time.time()
        if left > 0:
            await u.message.reply_text(f"صبر کن {fmt_time(left)}\nسطح قلعه روی زمان رکس اثر دارد.")
            return
        reward = reks_reward(d, chat.id)
        add_bal(d, user.id, reward)
        urow["last_reks"] = time.time()
        urow["reks_count"] = int(urow.get("reks_count") or 0) + 1
        # شانس کوچک «نجات»
        if random.random() < 0.08:
            urow["rescued"] = int(urow.get("rescued") or 0) + 1
        add_xp(d, user.id, int(d["settings"].get("xp_reks") or 2))
        # XP قلعه
        ch = d["chats"][str(chat.id)]
        ch["xp"] = int(ch.get("xp") or 0) + int(d["settings"].get("castle_xp_per_reks") or 1)
        # ارتقا ساده قلعه
        need = 100 * int(ch.get("level") or 1)
        if ch["xp"] >= need:
            ch["xp"] -= need
            ch["level"] = int(ch.get("level") or 1) + 1
            save(d)
            await u.message.reply_text(
                f"✨ رکس: +{num(reward)} {cur}\n🏰 قلعه گپ ارتقا یافت → سطح {ch['level']}"
            )
            return
        save(d)
        await u.message.reply_text(
            f"✨ +{num(reward)} {cur}\n"
            f"قلعه سطح {ch.get('level', 1)} | کول‌داون بعدی: {fmt_time(cd)}"
        )
        return

    # ----- قلعه -----
    if re.fullmatch(r"/?(قلعه|castle)", cmd, re.I):
        if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            await u.message.reply_text("فقط در گپ")
            return
        ch = d["chats"].get(str(chat.id), {})
        await u.message.reply_text(
            f"🏰 <b>{ch.get('title', chat.title)}</b>\n"
            f"سطح: <b>{ch.get('level', 1)}</b>\n"
            f"XP: {num(ch.get('xp', 0))}\n"
            f"خزانه: {num(ch.get('treasury', 0))}\n"
            f"پاداش رکس فعلی: {num(reks_reward(d, chat.id))}\n"
            f"کول‌داون رکس: {fmt_time(reks_cooldown(d, chat.id))}",
            parse_mode="HTML",
        )
        return

    # ----- انتقال -----
    m = re.match(r"^(?:/)?(?:انتقال|transfer)\s+(.+)$", cmd, re.I)
    if m:
        amt = parse_amount(m.group(1))
        if not amt or not u.message.reply_to_message or not u.message.reply_to_message.from_user:
            await u.message.reply_text("ریپلای کن و بنویس: انتقال 1k")
            return
        to = u.message.reply_to_message.from_user
        if to.is_bot or to.id == user.id:
            await u.message.reply_text("نامعتبر")
            return
        if bal(d, user.id) < amt:
            await u.message.reply_text("موجودی کم")
            return
        tax = int(amt * float(d["settings"].get("transfer_tax") or 0))
        send = amt - tax
        add_bal(d, user.id, -amt)
        ensure_user(d, to)
        add_bal(d, to.id, send)
        save(d)
        await u.message.reply_text(
            f"انتقال انجام شد\n{mention(user.id, user.full_name)} → {mention(to.id, to.full_name)}\n{num(send)} {cur}"
            + (f" (کارمزد {num(tax)})" if tax else ""),
            parse_mode="HTML",
        )
        return

    # ----- بانک -----
    if re.fullmatch(r"/?(بانک|bank)", cmd, re.I):
        b = int(d.get("bank", {}).get(str(user.id), 0))
        kb = InlineKeyboardMarkup([
            [btn("واریز", f"bank_in:{user.id}", "success"), btn("برداشت", f"bank_out:{user.id}", "danger")],
        ])
        await u.message.reply_text(
            f"🏛 بانک\nموجودی بانک: <b>{num(b)}</b>\nکیف: <b>{num(bal(d, user.id))}</b>\nسقف: {num(d['settings'].get('bank_cap', 0))}",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    # ----- کارگاه -----
    if re.fullmatch(r"/?(کارگاه|workshop)", cmd, re.I):
        w = d.setdefault("workshop", {}).setdefault(str(user.id), {"level": 1, "busy_until": 0})
        now = time.time()
        if float(w.get("busy_until") or 0) > now:
            await u.message.reply_text(f"در حال تولید... {fmt_time(float(w['busy_until']) - now)}")
            return
        # اگر تولید تمام شده و جمع‌نشده
        if w.get("ready"):
            reward = int(w.get("reward") or d["settings"]["workshop_base_reward"])
            add_bal(d, user.id, reward)
            w["ready"] = False
            save(d)
            await u.message.reply_text(f"🏭 تحویل تولید: +{num(reward)} {cur}")
            return
        t = int(d["settings"]["workshop_base_time"])
        reward = int(d["settings"]["workshop_base_reward"]) * int(w.get("level") or 1)
        w["busy_until"] = now + t
        w["reward"] = reward
        w["ready"] = True
        save(d)
        await u.message.reply_text(f"🏭 تولید شروع شد — {fmt_time(t)} دیگر با دوباره زدن کارگاه تحویل بگیر.")
        return

    # ----- عملیات (نسخه ساده تیمی) -----
    if re.fullmatch(r"/?(عملیات|op|سرقت)", cmd, re.I):
        if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            await u.message.reply_text("عملیات فقط در گپ")
            return
        jl = jail_left(d, user.id)
        if jl:
            await u.message.reply_text(f"بازداشت: {fmt_time(jl)}")
            return
        places = d["settings"].get("op_places") or []
        rows = []
        ulv = int(d["users"][str(user.id)].get("level") or 1)
        for p in places:
            if ulv >= int(p["min_lv"]):
                rows.append([btn(f"{p['name']} (سطح {p['min_lv']}+)", f"op_start:{p['id']}:{user.id}", "primary")])
        if not rows:
            await u.message.reply_text("سطح‌ات برای هیچ مکانی کافی نیست")
            return
        await u.message.reply_text("مکان عملیات را انتخاب کن:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if re.fullmatch(r"/?(ادمین|admin|panel)", cmd, re.I):
        if chat.type == ChatType.PRIVATE and is_admin(user.id):
            await u.message.reply_text("🎛 پنل ادمین", reply_markup=admin_kb())
        return

    save(d)


async def on_cb_game(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """کال‌بک بانک و عملیات — به on_cb وصل می‌شود با ادغام"""
    pass


# ادغام کال‌بک‌های بازی داخل on_cb
_orig_on_cb = on_cb

async def on_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):  # noqa: F811
    q = u.callback_query
    data = q.data or ""
    d = load()
    user = u.effective_user
    ensure_user(d, user)

    # بانک
    if data.startswith("bank_in:") or data.startswith("bank_out:"):
        await q.answer()
        owner = int(data.split(":")[1])
        if user.id != owner:
            await q.answer("این پنل برای تو نیست", show_alert=True)
            return
        set_st(c, "bank_in" if data.startswith("bank_in") else "bank_out")
        await q.edit_message_text("مبلغ را عددی بفرست:")
        return

    # شروع عملیات
    if data.startswith("op_start:"):
        await q.answer()
        _, pid, creator = data.split(":")
        creator = int(creator)
        if user.id != creator:
            await q.answer("فقط شروع‌کننده", show_alert=True)
            return
        place = next((p for p in d["settings"]["op_places"] if p["id"] == pid), None)
        if not place:
            return
        gid = f"op{int(time.time())}{random.randint(10,99)}"
        d.setdefault("ops", {})[gid] = {
            "place": pid,
            "creator": creator,
            "players": [creator],
            "chat_id": u.effective_chat.id,
            "ts": time.time(),
            "status": "open",
        }
        save(d)
        need = int(d["settings"].get("op_need_players") or 2)
        kb = InlineKeyboardMarkup([
            [btn(f"عضویت ({1}/{need})", f"op_join:{gid}", "success")],
            [btn("شروع", f"op_go:{gid}", "primary"), btn("لغو", f"op_cancel:{gid}", "danger")],
        ])
        await q.edit_message_text(
            f"🔫 عملیات: <b>{place['name']}</b>\nلیدر: {mention(creator, user.full_name)}\nنیاز: {need} نفر",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    if data.startswith("op_join:"):
        await q.answer()
        gid = data.split(":")[1]
        op = d.get("ops", {}).get(gid)
        if not op or op.get("status") != "open":
            await q.answer("بسته است", show_alert=True)
            return
        if jail_left(d, user.id):
            await q.answer("بازداشت هستی", show_alert=True)
            return
        if user.id in op["players"]:
            await q.answer("هستی", show_alert=True)
            return
        need = int(d["settings"].get("op_need_players") or 2)
        if len(op["players"]) >= need:
            await q.answer("پر است", show_alert=True)
            return
        op["players"].append(user.id)
        save(d)
        place = next((p for p in d["settings"]["op_places"] if p["id"] == op["place"]), {})
        kb = InlineKeyboardMarkup([
            [btn(f"عضویت ({len(op['players'])}/{need})", f"op_join:{gid}", "success")],
            [btn("شروع", f"op_go:{gid}", "primary"), btn("لغو", f"op_cancel:{gid}", "danger")],
        ])
        await q.edit_message_text(
            f"🔫 عملیات: <b>{place.get('name','?')}</b>\nاعضا: {len(op['players'])}/{need}",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    if data.startswith("op_cancel:"):
        await q.answer()
        gid = data.split(":")[1]
        op = d.get("ops", {}).get(gid)
        if not op or user.id != op.get("creator"):
            await q.answer("فقط لیدر", show_alert=True)
            return
        op["status"] = "cancel"
        save(d)
        await q.edit_message_text("عملیات لغو شد.")
        return

    if data.startswith("op_go:"):
        await q.answer()
        gid = data.split(":")[1]
        op = d.get("ops", {}).get(gid)
        if not op or user.id != op.get("creator"):
            await q.answer("فقط لیدر", show_alert=True)
            return
        need = int(d["settings"].get("op_need_players") or 2)
        if len(op["players"]) < need:
            await q.answer("هنوز کامل نیست", show_alert=True)
            return
        place = next((p for p in d["settings"]["op_places"] if p["id"] == op["place"]), None)
        await q.edit_message_text("✨ در حال انجام عملیات...")
        await asyncio.sleep(1.5)
        fail = random.random() < float(place.get("fail_chance") or 0.2)
        op["status"] = "done"
        if fail:
            sec = int(d["settings"].get("jail_seconds") or 7200)
            for pid in op["players"]:
                d.setdefault("users", {}).setdefault(str(pid), {})["jail_until"] = time.time() + sec
            save(d)
            await q.edit_message_text(f"🚓 شکست خوردید — بازداشت {fmt_time(sec)}")
            return
        loot = random.randint(int(place["loot_min"]), int(place["loot_max"]))
        share = loot // len(op["players"])
        names = []
        for pid in op["players"]:
            add_bal(d, pid, share)
            add_xp(d, pid, int(d["settings"].get("xp_op_success") or 8))
            names.append(mention(pid))
        # کمی به خزانه قلعه
        ch = d.setdefault("chats", {}).setdefault(str(op.get("chat_id")), {})
        ch["treasury"] = int(ch.get("treasury") or 0) + share // 10
        save(d)
        await q.edit_message_text(
            f"✅ موفقیت‌آمیز\nلوت کل: {num(loot)}\nسهم هر نفر: {num(share)}\n" + "\n".join(names),
            parse_mode="HTML",
        )
        return

    # بقیه به handler اصلی
    await _orig_on_cb(u, c)


async def on_text_bank_follow(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """بعد از دکمه واریز/برداشت بانک"""
    st = get_st(c)
    if not st or st.get("kind") not in ("bank_in", "bank_out"):
        return False
    d = load()
    user = u.effective_user
    amt = parse_amount(u.message.text or "")
    if not amt:
        await u.message.reply_text("مبلغ نامعتبر")
        return True
    cur = d["settings"].get("currency", "اعتبار")
    if st["kind"] == "bank_in":
        if bal(d, user.id) < amt:
            await u.message.reply_text("موجودی کم")
            return True
        cap = int(d["settings"].get("bank_cap") or 0)
        now_b = int(d.get("bank", {}).get(str(user.id), 0))
        if cap and now_b + amt > cap:
            await u.message.reply_text("سقف بانک")
            return True
        add_bal(d, user.id, -amt)
        d.setdefault("bank", {})[str(user.id)] = now_b + amt
        save(d)
        clear_st(c)
        await u.message.reply_text(f"واریز به بانک: {num(amt)} {cur}")
        return True
    # out
    now_b = int(d.get("bank", {}).get(str(user.id), 0))
    if now_b < amt:
        await u.message.reply_text("موجودی بانک کم")
        return True
    d["bank"][str(user.id)] = now_b - amt
    add_bal(d, user.id, amt)
    save(d)
    clear_st(c)
    await u.message.reply_text(f"برداشت: {num(amt)} {cur}")
    return True


_orig_on_text = on_text

async def on_text(u: Update, c: ContextTypes.DEFAULT_TYPE):  # noqa: F811
    if await on_text_bank_follow(u, c):
        return
    await _orig_on_text(u, c)


def main():
    if not BOT_TOKEN or BOT_TOKEN == "PUT_TOKEN_HERE":
        raise SystemExit("توکن را در BOT_TOKEN بگذار")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.COMMAND, on_text))
    log.info("Rokx bot starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
