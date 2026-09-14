# -*- coding: utf-8 -*-
"""ربات بازی الماس + پنل سلف + قرعه‌ کشی"""
from __future__ import annotations
import json, os, re, time, logging, random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters,
)
from telegram.constants import ChatType, MessageEntityType

BOT_TOKEN = "8727762178:AAGrdb5XFjhkcdoOEIFy1s8U71idRpN0DX8"
ADMIN_ID = 7530457395
DATA = "diamond_game.json"  # امتیازات اینجا می‌ماند — با آپدیت کد ریست نمی‌شود
TAX = 0.01
GAME_TTL = 600

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("dgame")


def D():
    return {
        "admins": [ADMIN_ID],
        "balances": {},
        "users": {},
        "emoji": "💎",
        "premium_emoji_id": None,
        "premium_pool": [],
        "games": {},
        "self_regs": {},
        "self_photo": None,
        "self_caption": "اکانت شما | Your Account",
        "hourly_use": 1,
        "lottery": None,  # {prize, capacity, winners, joined:[], status}
        "templates": {
            "balance": "💰 موجودی {mention}\n{emoji} {balance}",
            "game_open": "🎮 بازی {emoji} {amount}\nسازنده: {creator}\nنفر دوم روی شرکت بزند.",
            "game_result": "🎮 نتیجه\n🏆 {winner}\n💰 {emoji} {win_amount}\n💀 {loser}",
            "transfer_ok": "✅ انتقال\n{from} → {to}\n{emoji} {sent}",
        },
    }


def load():
    if os.path.exists(DATA):
        try:
            with open(DATA, "r", encoding="utf-8") as f:
                d = json.load(f)
            b = D()
            for k, v in b.items():
                if k == "templates" and isinstance(d.get("templates"), dict):
                    for tk, tv in b["templates"].items():
                        d.setdefault("templates", {}).setdefault(tk, tv)
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


def touch_user(d, user):
    if not user or getattr(user, "is_bot", False):
        return
    prev = d.get("users", {}).get(str(user.id), {})
    d.setdefault("users", {})[str(user.id)] = {
        "name": user.full_name or prev.get("name") or str(user.id),
        "username": user.username or prev.get("username") or "",
        "seen": time.time(),
    }
    d.setdefault("balances", {}).setdefault(str(user.id), bal(d, user.id))


def bal(d, uid):
    return int(d.get("balances", {}).get(str(uid), 0))


def set_bal(d, uid, val):
    d.setdefault("balances", {})[str(uid)] = max(0, int(val))


def add_bal(d, uid, delta):
    set_bal(d, uid, bal(d, uid) + int(delta))
    return bal(d, uid)


def all_user_ids(d):
    ids = set(str(k) for k in d.get("balances", {}))
    ids |= set(str(k) for k in d.get("users", {}))
    for g in (d.get("games") or {}).values():
        for p in g.get("players") or []:
            ids.add(str(p))
        if g.get("creator"):
            ids.add(str(g["creator"]))
    return ids


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


def em(d):
    pid = d.get("premium_emoji_id")
    fb = d.get("emoji") or "💎"
    if pid:
        return '<tg-emoji emoji-id="%s">%s</tg-emoji>' % (pid, fb)
    return fb


def em_plain(d):
    return d.get("emoji") or "💎"


def mention_user(user):
    return '<a href="tg://user?id=%s">%s</a>' % (user.id, user.full_name or user.id)


def mention_id(uid, name=None):
    if not name:
        name = (load().get("users") or {}).get(str(uid), {}).get("name") or str(uid)
    return '<a href="tg://user?id=%s">%s</a>' % (uid, name)


def tpl(d, key, **kw):
    t = (d.get("templates") or {}).get(key) or D()["templates"].get(key, "")
    kw.setdefault("emoji", em(d))
    for k, v in list(kw.items()):
        t = t.replace("{%s}" % k, str(v))
    return t


def expiry_parts(d, uid):
    h = max(1, int(d.get("hourly_use") or 1))
    hours = bal(d, uid) // h
    return hours // 24, hours % 24


def user_info(d, uid):
    u = (d.get("users") or {}).get(str(uid), {})
    return {
        "name": u.get("name") or str(uid),
        "username": u.get("username") or "—",
        "balance": bal(d, uid),
    }


# ---------- keyboards ----------
def start_kb():
    return InlineKeyboardMarkup([
        [btn("📝 ثبت سلف", "self_reg", "success")],
        [btn("👤 پنل سلف", "self_panel", "primary")],
        [btn("💰 موجودی", "my_bal", "primary")],
        [btn("🏆 لیدربرد", "lb", "primary")],
    ])


def admin_kb():
    return InlineKeyboardMarkup([
        [btn("➕ واریز (آیدی)", "a_add", "success"), btn("➕ واریز (یوزرنیم)", "a_add_un", "success")],
        [btn("➖ کم کردن (آیدی)", "a_sub", "danger"), btn("➖ کم کردن (یوزرنیم)", "a_sub_un", "danger")],
        [btn("📢 واریز همگانی", "a_add_all", "success")],
        [btn("📨 پیام همگانی", "a_bcast", "primary")],
        [btn("🗑 صفر کردن همه", "a_zero", "danger")],
        [btn("😀 ایموجی الماس", "a_emoji", "primary"), btn("✨ استخر پرمیوم", "a_prem", "primary")],
        [btn("🖼 عکس پنل سلف", "a_self_photo", "primary")],
        [btn("📝 کپشن پنل سلف", "a_self_cap", "primary")],
        [btn("⏱ مصرف ساعتی", "a_hourly", "primary")],
        [btn("🎰 قرعه‌کشی", "a_lot", "success")],
        [btn("📄 قالب پیام", "a_tpl", "primary")],
        [btn("👤 ادمین‌ها", "a_admins", "primary")],
        [btn("❌ بستن", "close", "danger")],
    ])


def self_panel_kb(d, uid):
    """دکمه‌های نمایشی مثل اسکرین — فقط صاحب پنل"""
    info = user_info(d, uid)
    days, rem_h = expiry_parts(d, uid)
    un = info["username"]
    if un and un != "—" and not str(un).startswith("@"):
        un = "@" + un
    o = "sp:%s" % uid
    # ردیف: مقدار (سبز) | برچسب (بنفش/primary)
    return InlineKeyboardMarkup([
        [btn(info["name"][:28], o, "success"), btn("اسم", o, "primary")],
        [btn(str(uid), o, "success"), btn("آیدی عددی", o, "primary")],
        [btn(un[:28], o, "success"), btn("یوزرنیم", o, "primary")],
        [btn(num(info["balance"]), o, "success"), btn("موجودی الماس", o, "primary")],
        [btn("%s روز و %s ساعت" % (num(days), num(rem_h)), o, "success"), btn("انقضا", o, "primary")],
        [btn("بازگشت", "sp_back:%s" % uid, "danger")],
    ])


# ---------- send self panel ----------
async def send_self_panel(bot, chat_id, uid, d, message=None):
    kb = self_panel_kb(d, uid)
    info = user_info(d, uid)
    cap = d.get("self_caption") or "اکانت شما"
    # اسم بدون تگ در کپشن
    text = "%s\n\n%s" % (cap, info["name"])
    photo = d.get("self_photo")
    if message:
        try:
            if photo and message.photo:
                await message.edit_caption(caption=text, reply_markup=kb)
                return
            if not message.photo:
                await message.edit_text(text, reply_markup=kb)
                return
        except Exception:
            pass
    if photo:
        try:
            await bot.send_photo(chat_id, photo=photo, caption=text, reply_markup=kb)
            return
        except Exception as e:
            log.error(e)
    await bot.send_message(chat_id, text, reply_markup=kb)


async def expire_games(d):
    now = time.time()
    ch = False
    for g in (d.get("games") or {}).values():
        if g.get("status") == "open" and now - g.get("ts", now) >= GAME_TTL:
            amount = int(g["amount"])
            for pid in g.get("players") or []:
                add_bal(d, pid, amount)
            g["status"] = "expired"
            ch = True
    if ch:
        save(d)


# ---------- handlers ----------
async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    clear_st(c)
    d = load()
    touch_user(d, u.effective_user)
    save(d)
    if u.effective_chat.type != ChatType.PRIVATE:
        await u.message.reply_text("گپ: بازی | موجودی | انتقال | لیدربرد | سلف")
        return
    await u.message.reply_text("سلام 👋", reply_markup=start_kb())


async def cmd_admin(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_chat.type != ChatType.PRIVATE or not is_admin(u.effective_user.id):
        return
    clear_st(c)
    await u.message.reply_text("🎛 پنل ادمین", reply_markup=admin_kb())


async def on_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    data = q.data or ""
    d = load()
    user = q.from_user
    touch_user(d, user)
    save(d)

    # مالکیت پنل / بازی / انتقال
    if data.startswith("sp:"):
        owner = int(data.split(":")[1])
        if user.id != owner:
            await q.answer("پنل برای تو نیست", show_alert=True)
            return
        await q.answer()
        return

    if data.startswith("sp_back:"):
        owner = int(data.split(":")[1])
        if user.id != owner:
            await q.answer("پنل برای تو نیست", show_alert=True)
            return
        await q.answer()
        if u.effective_chat.type == ChatType.PRIVATE:
            try:
                await q.edit_message_text("منو:", reply_markup=start_kb())
            except Exception:
                await q.edit_message_caption(caption="منو", reply_markup=start_kb())
        else:
            try:
                await q.message.delete()
            except Exception:
                await q.answer("بسته شد")
        return

    await q.answer()

    if data == "close":
        await q.edit_message_text("بسته شد.")
        return
    if data == "noop":
        return

    if data == "my_bal":
        await q.edit_message_text(
            tpl(d, "balance", mention=mention_user(user), balance=num(bal(d, user.id))),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [btn("%s %s" % (em_plain(d), num(bal(d, user.id))), "noop", "primary")]
            ]),
        )
        return

    if data == "lb":
        await q.edit_message_text(leaderboard_text(d), parse_mode="HTML", reply_markup=start_kb())
        return

    if data == "self_panel":
        await send_self_panel(c.bot, q.message.chat_id, user.id, d, message=q.message)
        return

    if data == "self_reg":
        set_st(c, "self_phone")
        await q.edit_message_text("شماره موبایل را بفرست:")
        return

    if data.startswith("self_code:"):
        if not is_admin(user.id):
            return
        uid = data.split(":")[1]
        reg = d.get("self_regs", {}).get(uid)
        if not reg:
            return
        reg["status"] = "wait_code"
        save(d)
        try:
            await c.bot.send_message(int(uid), "🔐 کد تأیید را بفرست:")
        except Exception:
            pass
        await q.edit_message_text("درخواست کد ارسال شد.")
        return

    # ---- بازی ----
    if data.startswith("join:"):
        await expire_games(d)
        d = load()
        gid = data.split(":")[1]
        game = d.get("games", {}).get(gid)
        if not game or game.get("status") != "open":
            await q.answer("بسته است", show_alert=True)
            return
        if user.id == game["creator"]:
            await q.answer("سازنده‌ای", show_alert=True)
            return
        if user.id in game.get("players", []):
            await q.answer("قبلاً پیوستی", show_alert=True)
            return
        amount = int(game["amount"])
        if bal(d, user.id) < amount:
            await q.answer("موجودی کم", show_alert=True)
            return
        add_bal(d, user.id, -amount)
        game["players"].append(user.id)
        game["names"][str(user.id)] = user.full_name
        if len(game["players"]) >= 2:
            game["status"] = "done"
            p1, p2 = game["players"][0], game["players"][1]
            winner = random.choice([p1, p2])
            loser = p2 if winner == p1 else p1
            win_amount = int(amount * 2 * (1 - TAX))
            add_bal(d, winner, win_amount)
            save(d)
            wname = game["names"].get(str(winner), str(winner))
            lname = game["names"].get(str(loser), str(loser))
            text = tpl(
                d, "game_result",
                amount=num(amount),
                winner=mention_id(winner, wname),
                loser=mention_id(loser, lname),
                win_amount=num(win_amount),
            )
            kb = InlineKeyboardMarkup([
                [btn("✅ %s | %s" % (wname[:14], num(bal(d, winner))), "noop", "success")],
                [btn("❌ %s | %s" % (lname[:14], num(bal(d, loser))), "noop", "danger")],
            ])
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
            return
        save(d)
        kb = InlineKeyboardMarkup([
            [btn("✅ شرکت", "join:%s" % gid, "success")],
            [btn("🚫 لغو", "cancel:%s" % gid, "danger")],
        ])
        await q.edit_message_text(
            tpl(d, "game_open", amount=num(amount), creator=mention_id(game["creator"], game["names"].get(str(game["creator"])))),
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    if data.startswith("cancel:"):
        gid = data.split(":")[1]
        game = d.get("games", {}).get(gid)
        if not game or game.get("status") != "open":
            await q.answer("قابل لغو نیست", show_alert=True)
            return
        if user.id != game["creator"]:
            await q.answer("پنل برای تو نیست", show_alert=True)
            return
        amount = int(game["amount"])
        for pid in game.get("players", []):
            add_bal(d, pid, amount)
        game["status"] = "cancelled"
        save(d)
        await q.edit_message_text("🚫 لغو شد — الماس برگشت.")
        return

    if data.startswith("tr_ok:"):
        parts = data.split(":")
        frm, to, amount = int(parts[1]), int(parts[2]), int(parts[3])
        if user.id != frm:
            await q.answer("پنل برای تو نیست", show_alert=True)
            return
        if bal(d, frm) < amount:
            await q.edit_message_text("موجودی کافی نیست.")
            return
        send_amt = amount - int(amount * TAX)
        if send_amt < 1:
            await q.edit_message_text("مقدار کم است.")
            return
        add_bal(d, frm, -amount)
        add_bal(d, to, send_amt)
        save(d)
        await q.edit_message_text(
            tpl(d, "transfer_ok", **{"from": mention_id(frm), "to": mention_id(to), "sent": num(send_amt)}),
            parse_mode="HTML",
        )
        try:
            await c.bot.send_message(to, "دریافت: %s %s" % (em(d), num(send_amt)), parse_mode="HTML")
        except Exception:
            pass
        return

    if data.startswith("tr_no:"):
        frm = int(data.split(":")[1]) if ":" in data and data.split(":")[1].isdigit() else user.id
        if user.id != frm and not data.endswith(":1"):
            # tr_no:uid
            try:
                if user.id != int(data.split(":")[1]):
                    await q.answer("پنل برای تو نیست", show_alert=True)
                    return
            except Exception:
                pass
        await q.edit_message_text("لغو شد.")
        return

    # قرعه‌کشی عضویت
    if data == "lot_join":
        lot = d.get("lottery")
        if not lot or lot.get("status") != "open":
            await q.answer("قرعه‌کشی فعال نیست", show_alert=True)
            return
        joined = lot.setdefault("joined", [])
        if user.id in joined:
            await q.answer("قبلاً شرکت کردی", show_alert=True)
            return
        if len(joined) >= int(lot.get("capacity", 0)):
            await q.answer("ظرفیت پر است", show_alert=True)
            return
        joined.append(user.id)
        touch_user(d, user)
        save(d)
        await q.answer("ثبت شد ✅")
        try:
            await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([
                [btn("🎰 شرکت (%s/%s)" % (len(joined), lot["capacity"]), "lot_join", "success")]
            ]))
        except Exception:
            pass
        return

    if not is_admin(user.id):
        return

    if data == "a_home":
        clear_st(c)
        await q.edit_message_text("🎛 پنل", reply_markup=admin_kb())
        return
    if data == "a_add":
        set_st(c, "a_add_id")
        await q.edit_message_text("آیدی عددی کاربر:")
        return
    if data == "a_add_un":
        set_st(c, "a_add_un")
        await q.edit_message_text("یوزرنیم بدون @:")
        return
    if data == "a_sub":
        set_st(c, "a_sub_id")
        await q.edit_message_text("آیدی برای کم کردن:")
        return
    if data == "a_sub_un":
        set_st(c, "a_sub_un")
        await q.edit_message_text("یوزرنیم برای کم کردن:")
        return
    if data == "a_add_all":
        set_st(c, "a_add_all")
        await q.edit_message_text("مقدار همگانی:\n(%s کاربر)" % num(len(all_user_ids(d))))
        return
    if data == "a_bcast":
        set_st(c, "a_bcast_where")
        await q.edit_message_text(
            "پیام همگانی کجا؟\nبفرست: پیوی\nیا: گپ",
        )
        return
    if data == "a_zero":
        set_st(c, "a_zero_confirm")
        await q.edit_message_text("برای تأیید بنویس: تأیید صفر")
        return
    if data == "a_emoji":
        set_st(c, "a_emoji")
        await q.edit_message_text("ایموجی پرمیوم یا متنی الماس را بفرست:")
        return
    if data == "a_prem":
        set_st(c, "a_prem")
        await q.edit_message_text("پرمیوم بفرست (تا ۱۰). الان: %s" % len(d.get("premium_pool") or []))
        return
    if data == "a_self_photo":
        set_st(c, "a_self_photo")
        await q.edit_message_text("عکس پنل سلف را بفرست:")
        return
    if data == "a_self_cap":
        set_st(c, "a_self_cap")
        await q.edit_message_text("کپشن پنل سلف را بفرست:\nفعلی:\n%s" % (d.get("self_caption") or ""))
        return
    if data == "a_hourly":
        set_st(c, "a_hourly")
        await q.edit_message_text("مصرف ساعتی (عدد):")
        return
    if data == "a_lot":
        set_st(c, "a_lot_prize")
        await q.edit_message_text("جایزه هر برنده (الماس):")
        return
    if data == "a_tpl":
        set_st(c, "a_tpl_pick")
        await q.edit_message_text("کلید قالب: balance | game_open | game_result | transfer_ok")
        return
    if data == "a_admins":
        lines = ["👤 ادمین‌ها\n"]
        rows = []
        for a in d.get("admins", []):
            lines.append("• <code>%s</code>%s" % (a, " (اصلی)" if int(a) == ADMIN_ID else ""))
            if int(a) != ADMIN_ID and is_main(user.id):
                rows.append([btn("🗑 %s" % a, "a_adel:%s" % a, "danger")])
        if is_main(user.id):
            rows.insert(0, [btn("➕ آیدی", "a_aadd", "success")])
        rows.append([btn("🔙", "a_home", "primary")])
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        return
    if data == "a_aadd":
        set_st(c, "a_aadd")
        await q.edit_message_text("آیدی عددی ادمین:")
        return
    if data.startswith("a_adel:"):
        if not is_main(user.id):
            return
        aid = int(data.split(":")[1])
        d["admins"] = [x for x in d.get("admins", []) if int(x) != aid]
        save(d)
        await q.edit_message_text("حذف شد.", reply_markup=admin_kb())
        return
    if data == "a_lot_draw":
        lot = d.get("lottery")
        if not lot or lot.get("status") != "open":
            await q.answer("نیست", show_alert=True)
            return
        joined = list(lot.get("joined") or [])
        nw = min(int(lot.get("winners", 1)), len(joined))
        if nw < 1:
            await q.answer("شرکت‌کننده نیست", show_alert=True)
            return
        winners = random.sample(joined, nw)
        prize = int(lot.get("prize", 0))
        names = []
        for w in winners:
            add_bal(d, w, prize)
            names.append(mention_id(w))
        lot["status"] = "done"
        save(d)
        await q.edit_message_text(
            "🎰 برندگان:\n" + "\n".join(names) + "\nهر کدام: %s %s" % (em(d), num(prize)),
            parse_mode="HTML",
        )
        return


def leaderboard_text(d):
    items = sorted(
        ((int(uid), int(v)) for uid, v in (d.get("balances") or {}).items() if int(v) > 0),
        key=lambda x: -x[1],
    )[:10]
    if not items:
        return "لیدربرد خالی است."
    lines = ["🏆 <b>۱۰ نفر برتر</b>\n"]
    for i, (uid, v) in enumerate(items, 1):
        name = (d.get("users") or {}).get(str(uid), {}).get("name")
        lines.append("%s. %s — %s %s" % (i, mention_id(uid, name), em(d), num(v)))
    return "\n".join(lines)


async def resolve_username(bot, uname):
    uname = uname.lstrip("@")
    ch = await bot.get_chat("@" + uname)
    return ch.id, ch.full_name if hasattr(ch, "full_name") else (ch.first_name or uname)


async def on_text(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    user = u.effective_user
    d = load()
    touch_user(d, user)
    save(d)
    await expire_games(d)
    d = load()
    text = (u.message.text or "").strip()
    chat = u.effective_chat
    st = get_st(c)

    # کد سلف
    if chat.type == ChatType.PRIVATE:
        reg0 = d.get("self_regs", {}).get(str(user.id))
        if reg0 and reg0.get("status") == "wait_code":
            if not (st and str(st.get("kind", "")).startswith("a_")):
                reg0["code"] = text
                reg0["status"] = "done"
                save(d)
                clear_st(c)
                await u.message.reply_text("✅ تأیید شد.")
                for aid in d.get("admins", [ADMIN_ID]):
                    try:
                        await c.bot.send_message(
                            int(aid),
                            "✅ کد سلف\n%s\n<code>%s</code>\n%s\nکد: <code>%s</code>"
                            % (mention_user(user), user.id, reg0.get("phone"), text),
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
                return

    # ادمین اصلی در گپ: کسر مبلغ (ریپلای)
    if is_main(user.id) and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        m = re.match(r"^کسر\s+(\d+)$", text)
        if m and u.message.reply_to_message and u.message.reply_to_message.from_user:
            amt = int(m.group(1))
            tuser = u.message.reply_to_message.from_user
            touch_user(d, tuser)
            add_bal(d, tuser.id, -amt)
            save(d)
            await u.message.reply_text(
                "کسر شد از %s\n%s %s" % (mention_user(tuser), em(d), num(bal(d, tuser.id))),
                parse_mode="HTML",
            )
            return

    if chat.type == ChatType.PRIVATE and st and is_admin(user.id):
        kind = st.get("kind")
        extra = st.get("extra") or {}

        if kind == "self_phone":
            phone = text.replace(" ", "").replace("-", "")
            if not re.fullmatch(r"09\d{9}", phone) and not re.fullmatch(r"\+?\d{10,15}", phone):
                await u.message.reply_text("شماره معتبر")
                return
            d.setdefault("self_regs", {})[str(user.id)] = {
                "phone": phone, "status": "pending", "name": user.full_name, "ts": time.time(),
            }
            save(d)
            clear_st(c)
            await u.message.reply_text("⏳ در حال بررسی...")
            kb = InlineKeyboardMarkup([[btn("🔐 درخواست کد", "self_code:%s" % user.id, "success")]])
            for aid in d.get("admins", [ADMIN_ID]):
                try:
                    await c.bot.send_message(
                        int(aid),
                        "📝 سلف\n%s\n<code>%s</code>\n%s" % (mention_user(user), user.id, phone),
                        parse_mode="HTML", reply_markup=kb,
                    )
                except Exception:
                    pass
            return

        if kind == "a_add_id" and text.lstrip("-").isdigit():
            set_st(c, "a_add_amt", {"uid": int(text)})
            await u.message.reply_text("مقدار واریز:")
            return
        if kind == "a_add_un":
            try:
                uid, name = await resolve_username(c.bot, text)
                d.setdefault("users", {})[str(uid)] = {
                    "name": name, "username": text.lstrip("@"), "seen": time.time(),
                }
                set_st(c, "a_add_amt", {"uid": uid})
                await u.message.reply_text("پیدا شد: %s\nمقدار واریز:" % name)
            except Exception as e:
                await u.message.reply_text("پیدا نشد: %s" % e)
            return
        if kind == "a_add_amt" and text.isdigit():
            uid, amt = int(extra["uid"]), int(text)
            add_bal(d, uid, amt)
            save(d)
            clear_st(c)
            await u.message.reply_text("✅ %s → <code>%s</code>" % (num(amt), uid), parse_mode="HTML", reply_markup=admin_kb())
            try:
                await c.bot.send_message(uid, "واریز: %s %s" % (em(d), num(amt)), parse_mode="HTML")
            except Exception:
                pass
            return
        if kind == "a_sub_id" and text.lstrip("-").isdigit():
            set_st(c, "a_sub_amt", {"uid": int(text)})
            await u.message.reply_text("مقدار کم کردن:")
            return
        if kind == "a_sub_un":
            try:
                uid, name = await resolve_username(c.bot, text)
                set_st(c, "a_sub_amt", {"uid": uid})
                await u.message.reply_text("%s — مقدار کم کردن:" % name)
            except Exception as e:
                await u.message.reply_text("پیدا نشد: %s" % e)
            return
        if kind == "a_sub_amt" and text.isdigit():
            uid, amt = int(extra["uid"]), int(text)
            add_bal(d, uid, -amt)
            save(d)
            clear_st(c)
            await u.message.reply_text("✅ موجودی: %s" % num(bal(d, uid)), reply_markup=admin_kb())
            return
        if kind == "a_add_all" and text.isdigit():
            amt = int(text)
            ids = all_user_ids(d)
            for uid in ids:
                add_bal(d, uid, amt)
            save(d)
            clear_st(c)
            await u.message.reply_text("✅ %s به %s نفر" % (num(amt), num(len(ids))), reply_markup=admin_kb())
            return
        if kind == "a_bcast_where":
            where = text.strip()
            if where not in ("پیوی", "گپ"):
                await u.message.reply_text("فقط: پیوی یا گپ")
                return
            set_st(c, "a_bcast_msg", {"where": where})
            await u.message.reply_text("متن پیام همگانی را بفرست:")
            return
        if kind == "a_bcast_msg":
            where = extra.get("where")
            ok = fail = 0
            if where == "پیوی":
                for uid in all_user_ids(d):
                    try:
                        await c.bot.send_message(int(uid), text)
                        ok += 1
                    except Exception:
                        fail += 1
            else:
                # گپ‌هایی که از بازی‌ها دیده‌ایم
                chats = set()
                for g in (d.get("games") or {}).values():
                    if g.get("chat_id"):
                        chats.add(int(g["chat_id"]))
                for cid in chats:
                    try:
                        await c.bot.send_message(cid, text)
                        ok += 1
                    except Exception:
                        fail += 1
            clear_st(c)
            await u.message.reply_text("ارسال: ✅%s ❌%s" % (ok, fail), reply_markup=admin_kb())
            return
        if kind == "a_zero_confirm":
            if text.strip() != "تأیید صفر":
                await u.message.reply_text("برای تأیید دقیقاً بنویس: تأیید صفر")
                return
            for uid in list(d.get("balances", {}).keys()):
                d["balances"][uid] = 0
            save(d)
            clear_st(c)
            await u.message.reply_text("همه صفر شد.", reply_markup=admin_kb())
            return
        if kind == "a_emoji":
            eid = None
            if u.message.entities:
                for ent in u.message.entities:
                    if ent.type == MessageEntityType.CUSTOM_EMOJI and ent.custom_emoji_id:
                        eid = str(ent.custom_emoji_id)
                        break
            if eid:
                d["premium_emoji_id"] = eid
                d["emoji"] = "💎"
                save(d)
                clear_st(c)
                preview = '<tg-emoji emoji-id="%s">💎</tg-emoji>' % eid
                await u.message.reply_text(
                    "✅ پرمیوم ذخیره شد\n%s\nدر متن پیام‌ها نمایش داده می‌شود." % preview,
                    parse_mode="HTML",
                    reply_markup=admin_kb(),
                )
            else:
                d["emoji"] = text[:8] if text else "💎"
                d["premium_emoji_id"] = None
                save(d)
                clear_st(c)
                await u.message.reply_text("متنی: %s" % d["emoji"], reply_markup=admin_kb())
            return
        if kind == "a_prem":
            eid = None
            if u.message.entities:
                for ent in u.message.entities:
                    if ent.type == MessageEntityType.CUSTOM_EMOJI and ent.custom_emoji_id:
                        eid = str(ent.custom_emoji_id)
                        break
            if not eid and re.fullmatch(r"\d{5,25}", text):
                eid = text
            if not eid:
                await u.message.reply_text("پرمیوم بفرست")
                return
            pool = d.get("premium_pool") or []
            if eid not in pool:
                pool.append(eid)
            d["premium_pool"] = pool[-10:]
            d["premium_emoji_id"] = eid
            save(d)
            clear_st(c)
            preview = '<tg-emoji emoji-id="%s">💎</tg-emoji>' % eid
            await u.message.reply_text(
                "✅ %s/10\n%s" % (len(d["premium_pool"]), preview),
                parse_mode="HTML",
                reply_markup=admin_kb(),
            )
            return
        if kind == "a_self_cap":
            d["self_caption"] = text
            save(d)
            clear_st(c)
            await u.message.reply_text("کپشن ذخیره شد.", reply_markup=admin_kb())
            return
        if kind == "a_hourly" and text.isdigit():
            d["hourly_use"] = max(1, int(text))
            save(d)
            clear_st(c)
            await u.message.reply_text("مصرف ساعتی: %s" % d["hourly_use"], reply_markup=admin_kb())
            return
        if kind == "a_lot_prize" and text.isdigit():
            set_st(c, "a_lot_cap", {"prize": int(text)})
            await u.message.reply_text("ظرفیت کل شرکت‌کننده‌ها:")
            return
        if kind == "a_lot_cap" and text.isdigit():
            set_st(c, "a_lot_win", {"prize": extra["prize"], "capacity": int(text)})
            await u.message.reply_text("تعداد برنده‌ها:")
            return
        if kind == "a_lot_win" and text.isdigit():
            lot = {
                "prize": int(extra["prize"]),
                "capacity": int(extra["capacity"]),
                "winners": int(text),
                "joined": [],
                "status": "open",
            }
            d["lottery"] = lot
            save(d)
            clear_st(c)
            kb = InlineKeyboardMarkup([
                [btn("🎰 شرکت (0/%s)" % lot["capacity"], "lot_join", "success")],
                [btn("🏁 قرعه بکش", "a_lot_draw", "danger")],
            ])
            await u.message.reply_text(
                "🎰 قرعه‌کشی باز شد\nجایزه: %s\nظرفیت: %s\nبرنده‌ها: %s\nاین پیام را فوروارد/بفرست گپ."
                % (num(lot["prize"]), num(lot["capacity"]), num(lot["winners"])),
                reply_markup=kb,
            )
            return
        if kind == "a_tpl_pick":
            if text.strip() not in (d.get("templates") or {}):
                await u.message.reply_text("کلید نامعتبر")
                return
            set_st(c, "a_tpl_set", {"key": text.strip()})
            await u.message.reply_text("متن جدید:\n%s" % d["templates"][text.strip()])
            return
        if kind == "a_tpl_set":
            d.setdefault("templates", {})[extra["key"]] = text
            save(d)
            clear_st(c)
            await u.message.reply_text("ذخیره شد.", reply_markup=admin_kb())
            return
        if kind == "a_aadd" and text.lstrip("-").isdigit():
            aid = int(text)
            if aid not in [int(x) for x in d.get("admins", [])]:
                d.setdefault("admins", []).append(aid)
                save(d)
            clear_st(c)
            await u.message.reply_text("✅ %s" % aid, reply_markup=admin_kb())
            return

    # self_phone for non-admin path
    if chat.type == ChatType.PRIVATE and st and st.get("kind") == "self_phone":
        phone = text.replace(" ", "").replace("-", "")
        if not re.fullmatch(r"09\d{9}", phone) and not re.fullmatch(r"\+?\d{10,15}", phone):
            await u.message.reply_text("شماره معتبر")
            return
        d.setdefault("self_regs", {})[str(user.id)] = {
            "phone": phone, "status": "pending", "name": user.full_name, "ts": time.time(),
        }
        save(d)
        clear_st(c)
        await u.message.reply_text("⏳ در حال بررسی...")
        kb = InlineKeyboardMarkup([[btn("🔐 درخواست کد", "self_code:%s" % user.id, "success")]])
        for aid in d.get("admins", [ADMIN_ID]):
            try:
                await c.bot.send_message(
                    int(aid),
                    "📝 سلف\n%s\n<code>%s</code>\n%s" % (mention_user(user), user.id, phone),
                    parse_mode="HTML", reply_markup=kb,
                )
            except Exception:
                pass
        return

    low = text
    low2 = re.sub(r"^@\w+\s+", "", low)
    low2 = re.sub(r"^/(\w+)@\w+", r"/\1", low2)

    if re.fullmatch(r"/?(موجودی|bal)", low2, re.I):
        if u.message.reply_to_message and u.message.reply_to_message.from_user:
            tuser = u.message.reply_to_message.from_user
            touch_user(d, tuser)
            save(d)
            await u.message.reply_text(
                tpl(d, "balance", mention=mention_user(tuser), balance=num(bal(d, tuser.id))),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [btn("%s %s" % (em_plain(d), num(bal(d, tuser.id))), "noop", "primary")]
                ]),
            )
        else:
            await u.message.reply_text(
                tpl(d, "balance", mention=mention_user(user), balance=num(bal(d, user.id))),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [btn("%s %s" % (em_plain(d), num(bal(d, user.id))), "noop", "primary")]
                ]),
            )
        return

    if re.fullmatch(r"/?(لیدربرد|لیدربورد|top)", low2, re.I):
        await u.message.reply_text(leaderboard_text(d), parse_mode="HTML")
        return

    if re.fullmatch(r"/?(سلف|self)", low2, re.I):
        await send_self_panel(c.bot, chat.id, user.id, d)
        return

    m = re.match(r"^(?:/)?(?:بازی|game)\s+(\d+)$", low2, re.I)
    if m:
        amount = int(m.group(1))
        if amount < 1:
            return
        if bal(d, user.id) < amount:
            await u.message.reply_text("موجودی: %s %s" % (em(d), num(bal(d, user.id))), parse_mode="HTML")
            return
        add_bal(d, user.id, -amount)
        gid = "g%d%d" % (int(time.time()), random.randint(10, 99))
        d.setdefault("games", {})[gid] = {
            "creator": user.id,
            "amount": amount,
            "players": [user.id],
            "names": {str(user.id): user.full_name},
            "status": "open",
            "chat_id": chat.id,
            "ts": time.time(),
        }
        save(d)
        kb = InlineKeyboardMarkup([
            [btn("✅ شرکت", "join:%s" % gid, "success")],
            [btn("🚫 لغو", "cancel:%s" % gid, "danger")],
        ])
        await u.message.reply_text(
            tpl(d, "game_open", amount=num(amount), creator=mention_user(user)),
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    m = re.match(r"^(?:/)?(?:انتقال|transfer)\s+(\d+)$", low2, re.I)
    if m:
        amount = int(m.group(1))
        if not u.message.reply_to_message or not u.message.reply_to_message.from_user:
            await u.message.reply_text("ریپلای + انتقال 50")
            return
        to = u.message.reply_to_message.from_user
        if to.id == user.id or to.is_bot:
            return
        if bal(d, user.id) < amount:
            await u.message.reply_text("موجودی کم")
            return
        send_amt = amount - int(amount * TAX)
        kb = InlineKeyboardMarkup([
            [btn("✅ تأیید", "tr_ok:%s:%s:%s" % (user.id, to.id, amount), "success")],
            [btn("❌ لغو", "tr_no:%s" % user.id, "danger")],
        ])
        await u.message.reply_text(
            "تأیید؟\n%s → %s\nکسر: %s %s\nدریافتی: %s %s"
            % (mention_user(user), mention_user(to), em(d), num(amount), em(d), num(send_amt)),
            parse_mode="HTML",
            reply_markup=kb,
        )
        return


async def on_photo(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or u.effective_chat.type != ChatType.PRIVATE:
        return
    st = get_st(c)
    if not st or st.get("kind") != "a_self_photo" or not is_admin(u.effective_user.id):
        return
    d = load()
    d["self_photo"] = u.message.photo[-1].file_id
    save(d)
    clear_st(c)
    await u.message.reply_text("عکس پنل ذخیره شد.", reply_markup=admin_kb())


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("panel", cmd_admin))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, on_photo))
    app.add_handler(MessageHandler(filters.TEXT, on_text))
    log.info("diamond game up")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
