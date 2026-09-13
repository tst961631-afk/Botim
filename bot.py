# -*- coding: utf-8 -*-
"""ربات بازی الماس — نسخه کامل‌تر"""
from __future__ import annotations
import json, os, re, time, logging, random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters,
)
from telegram.constants import ChatType, MessageEntityType

BOT_TOKEN = "8727762178:AAGrdb5XFjhkcdoOEIFy1s8U71idRpN0DX8"
ADMIN_ID = 7530457395
DATA = "diamond_game.json"
TAX = 0.01
GAME_TTL = 600  # 10 دقیقه

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("dgame")


def D():
    return {
        "admins": [ADMIN_ID],
        "balances": {},
        "users": {},  # str(uid) -> {name, username, seen}
        "emoji": "💎",
        "premium_emoji_id": None,
        "premium_pool": [],  # تا ۱۰ آیدی
        "games": {},
        "self_regs": {},
        "self_photo": None,  # file_id عکس پنل سلف
        "hourly_use": 1,  # مصرف ساعتی الماس
        "templates": {
            "balance": "💰 موجودی {mention}\n{emoji} {balance}",
            "game_open": "🎮 بازی {emoji} {amount}\nسازنده: {creator}\nنفر دوم روی شرکت بزند.",
            "game_result": "🎮 نتیجه بازی {emoji} {amount}\n\n🏆 برنده: {winner}\nدریافتی: {emoji} {win_amount}\n\nبازنده: {loser}",
            "transfer_ok": "✅ انتقال انجام شد\n{from} → {to}\nمبلغ: {emoji} {sent}",
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
                        d["templates"].setdefault(tk, tv)
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
    d = load()
    return int(uid) in {int(x) for x in d.get("admins", [ADMIN_ID])}


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
    d.setdefault("users", {})[str(user.id)] = {
        "name": user.full_name or str(user.id),
        "username": user.username or "",
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
    ids = set()
    for k in d.get("balances", {}):
        ids.add(str(k))
    for k in d.get("users", {}):
        ids.add(str(k))
    for g in (d.get("games") or {}).values():
        for p in g.get("players") or []:
            ids.add(str(p))
        if g.get("creator"):
            ids.add(str(g["creator"]))
    return ids


def btn(text, data, style=None):
    kw = {"text": text[:64], "callback_data": data}
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
    if time.time() - st.get("ts", 0) > 600:
        c.user_data.pop("st", None)
        return None
    return st


def clear_st(c):
    c.user_data.pop("st", None)


def em(d):
    return d.get("emoji") or "💎"


def mention_user(user):
    return '<a href="tg://user?id=%s">%s</a>' % (user.id, user.full_name or user.id)


def mention_id(uid, name=None):
    if not name:
        d = load()
        name = (d.get("users") or {}).get(str(uid), {}).get("name") or str(uid)
    return '<a href="tg://user?id=%s">%s</a>' % (uid, name)


def tpl(d, key, **kw):
    t = (d.get("templates") or {}).get(key) or D()["templates"].get(key, "")
    kw.setdefault("emoji", em(d))
    for k, v in list(kw.items()):
        t = t.replace("{%s}" % k, str(v))
    return t


def expiry_text(d, uid):
    """بر اساس موجودی / مصرف ساعتی"""
    h = max(1, int(d.get("hourly_use") or 1))
    b = bal(d, uid)
    hours = b // h
    days = hours // 24
    rem_h = hours % 24
    return days, rem_h, hours


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
        [btn("➕ واریز به کاربر", "a_add", "success")],
        [btn("📢 واریز همگانی", "a_add_all", "success")],
        [btn("➖ برداشت", "a_sub", "danger")],
        [btn("😀 ایموجی الماس", "a_emoji", "primary")],
        [btn("✨ استخر پرمیوم", "a_prem", "primary")],
        [btn("🖼 عکس پنل سلف", "a_self_photo", "primary")],
        [btn("⏱ مصرف ساعتی", "a_hourly", "primary")],
        [btn("📝 قالب پیام‌ها", "a_tpl", "primary")],
        [btn("👤 ادمین‌ها", "a_admins", "primary")],
        [btn("❌ بستن", "close", "danger")],
    ])


def self_panel_kb(d, uid):
    name = (d.get("users") or {}).get(str(uid), {}).get("name") or str(uid)
    b = bal(d, uid)
    days, rem_h, _ = expiry_text(d, uid)
    return InlineKeyboardMarkup([
        [btn("👤 %s" % name[:30], "noop")],
        [btn("%s %s" % (em(d), num(b)), "noop", "primary")],
        [btn("⏳ %s روز و %s ساعت" % (num(days), num(rem_h)), "noop")],
        [btn("📊 مصرف: %s %s / ساعت" % (num(d.get("hourly_use") or 1), em(d)), "noop")],
    ])


# ---------- handlers ----------
async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    clear_st(c)
    d = load()
    touch_user(d, u.effective_user)
    save(d)
    if u.effective_chat.type != ChatType.PRIVATE:
        await u.message.reply_text("در گپ: بازی | موجودی | انتقال | لیدربرد | سلف")
        return
    if is_admin(u.effective_user.id):
        await u.message.reply_text("ادمین — /admin\nمنوی کاربر:", reply_markup=start_kb())
        return
    await u.message.reply_text("سلام 👋", reply_markup=start_kb())


async def cmd_admin(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_chat.type != ChatType.PRIVATE or not is_admin(u.effective_user.id):
        return
    clear_st(c)
    await u.message.reply_text("🎛 پنل ادمین", reply_markup=admin_kb())


async def expire_games(d):
    now = time.time()
    changed = False
    for gid, g in list((d.get("games") or {}).items()):
        if g.get("status") != "open":
            continue
        if now - g.get("ts", now) >= GAME_TTL:
            amount = int(g["amount"])
            for pid in g.get("players") or []:
                add_bal(d, pid, amount)
            g["status"] = "expired"
            changed = True
    if changed:
        save(d)


async def on_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    data = q.data or ""
    d = load()
    user = q.from_user
    touch_user(d, user)
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
                [btn("%s %s" % (em(d), num(bal(d, user.id))), "noop", "primary")]
            ]),
        )
        return

    if data == "lb":
        await q.edit_message_text(leaderboard_text(d), parse_mode="HTML", reply_markup=start_kb())
        return

    if data == "self_panel":
        await send_self_panel(c.bot, q.message.chat_id, user.id, d, edit=q)
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
            await q.answer("نیست", show_alert=True)
            return
        reg["status"] = "wait_code"
        save(d)
        try:
            await c.bot.send_message(int(uid), "🔐 کد تأیید را بفرست:")
        except Exception:
            pass
        await q.edit_message_text("درخواست کد ارسال شد.\n%s" % reg.get("phone"))
        return

    # بازی
    if data.startswith("join:"):
        await expire_games(d)
        d = load()
        gid = data.split(":")[1]
        game = d.get("games", {}).get(gid)
        if not game or game.get("status") != "open":
            await q.answer("بسته/منقضی", show_alert=True)
            return
        if time.time() - game.get("ts", 0) >= GAME_TTL:
            await q.answer("زمان تمام شد", show_alert=True)
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
        touch_user(d, user)
        if len(game["players"]) >= 2:
            game["status"] = "done"
            p1, p2 = game["players"][0], game["players"][1]
            winner = random.choice([p1, p2])
            loser = p2 if winner == p1 else p1
            pot = amount * 2
            win_amount = int(pot * (1 - TAX))
            add_bal(d, winner, win_amount)
            game["winner"] = winner
            save(d)
            wname = game["names"].get(str(winner), str(winner))
            lname = game["names"].get(str(loser), str(loser))
            text = tpl(
                d, "game_result",
                amount=num(amount),
                winner=mention_id(winner, wname),
                loser=mention_id(loser, lname),
                win_amount=num(win_amount),
                loser_balance=num(bal(d, loser)),
                winner_balance=num(bal(d, winner)),
            )
            kb = InlineKeyboardMarkup([
                [btn("✅ %s | %s %s" % (wname[:15], em(d), num(bal(d, winner))), "noop", "success")],
                [btn("❌ %s | %s %s" % (lname[:15], em(d), num(bal(d, loser))), "noop", "danger")],
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
        if user.id != game["creator"] and not is_admin(user.id):
            await q.answer("فقط سازنده", show_alert=True)
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
        if len(parts) < 4:
            return
        frm, to, amount = int(parts[1]), int(parts[2]), int(parts[3])
        if user.id != frm:
            await q.answer("فقط فرستنده", show_alert=True)
            return
        if bal(d, frm) < amount:
            await q.edit_message_text("موجودی کافی نیست.")
            return
        fee = int(amount * TAX)
        send_amt = amount - fee
        if send_amt < 1:
            await q.edit_message_text("مقدار کم است.")
            return
        add_bal(d, frm, -amount)
        add_bal(d, to, send_amt)
        save(d)
        await q.edit_message_text(
            tpl(d, "transfer_ok", **{"from": mention_id(frm), "to": mention_id(to), "sent": num(send_amt), "amount": num(amount)}),
            parse_mode="HTML",
        )
        try:
            await c.bot.send_message(to, "دریافت: %s %s" % (em(d), num(send_amt)))
        except Exception:
            pass
        return

    if data.startswith("tr_no:"):
        await q.edit_message_text("لغو شد.")
        return

    # owner-only panel: ignore foreign clicks on self panel decorative
    if data.startswith("sp:"):
        owner = int(data.split(":")[1])
        if user.id != owner:
            await q.answer("این پنل مال شما نیست", show_alert=True)
            return
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
    if data == "a_sub":
        set_st(c, "a_sub_id")
        await q.edit_message_text("آیدی برای برداشت:")
        return
    if data == "a_add_all":
        set_st(c, "a_add_all")
        ids = all_user_ids(d)
        await q.edit_message_text("مقدار همگانی را بفرست:\n(اکنون %s کاربر در دیتابیس)" % num(len(ids)))
        return
    if data == "a_emoji":
        set_st(c, "a_emoji")
        await q.edit_message_text("ایموجی متنی یا پرمیوم الماس را بفرست:")
        return
    if data == "a_prem":
        set_st(c, "a_prem")
        n = len(d.get("premium_pool") or [])
        await q.edit_message_text("ایموجی پرمیوم بفرست (تا ۱۰ تا). الان: %s" % n)
        return
    if data == "a_self_photo":
        set_st(c, "a_self_photo")
        await q.edit_message_text("عکس پنل سلف را بفرست:")
        return
    if data == "a_hourly":
        set_st(c, "a_hourly")
        await q.edit_message_text("مصرف ساعتی الماس (عدد):")
        return
    if data == "a_tpl":
        set_st(c, "a_tpl_pick")
        await q.edit_message_text(
            "کدام قالب؟ بفرست یکی از:\nbalance\ngame_open\ngame_result\ntransfer_ok\n\n"
            "متغیرها: {mention} {emoji} {balance} {amount} {creator} {winner} {loser} {win_amount} {from} {to} {sent}"
        )
        return
    if data == "a_admins":
        lines = ["👤 ادمین‌ها\n"]
        rows = []
        for a in d.get("admins", []):
            lines.append("• <code>%s</code>%s" % (a, " (اصلی)" if int(a) == ADMIN_ID else ""))
            if int(a) != ADMIN_ID and is_main(user.id):
                rows.append([btn("🗑 %s" % a, "a_adel:%s" % a, "danger")])
        if is_main(user.id):
            rows.insert(0, [btn("➕ آیدی عددی", "a_aadd", "success")])
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


async def send_self_panel(bot, chat_id, uid, d, edit=None):
    kb = self_panel_kb(d, uid)
    # دکمه‌ها را owner-tag کن تا بقیه نزنند
    rows = []
    for row in kb.inline_keyboard:
        rows.append([btn(b.text, "sp:%s" % uid, getattr(b, "style", None)) for b in row])
    kb = InlineKeyboardMarkup(rows)
    caption = "پنل سلف شما"
    photo = d.get("self_photo")
    if edit:
        try:
            if photo and edit.message.photo:
                await edit.edit_message_caption(caption=caption, reply_markup=kb)
                return
            await edit.edit_message_text(caption, reply_markup=kb)
            return
        except Exception:
            pass
    if photo:
        try:
            await bot.send_photo(chat_id, photo=photo, caption=caption, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id, caption, reply_markup=kb)


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
                d["self_regs"][str(user.id)] = reg0
                save(d)
                clear_st(c)
                await u.message.reply_text("✅ تأیید شد. جزئیات اعلام می‌شود.")
                for aid in d.get("admins", [ADMIN_ID]):
                    try:
                        await c.bot.send_message(
                            int(aid),
                            "✅ کد سلف\n%s\nآیدی: <code>%s</code>\nشماره: <code>%s</code>\nکد: <code>%s</code>"
                            % (mention_user(user), user.id, reg0.get("phone"), text),
                            parse_mode="HTML",
                        )
                    except Exception as e:
                        log.error(e)
                return

    if chat.type == ChatType.PRIVATE and st:
        kind = st.get("kind")
        extra = st.get("extra") or {}

        if kind == "self_phone":
            phone = text.replace(" ", "").replace("-", "")
            if not re.fullmatch(r"09\d{9}", phone) and not re.fullmatch(r"\+?\d{10,15}", phone):
                await u.message.reply_text("شماره معتبر بفرست")
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
                        "📝 ثبت سلف\n%s\n<code>%s</code>\n%s" % (mention_user(user), user.id, phone),
                        parse_mode="HTML",
                        reply_markup=kb,
                    )
                except Exception:
                    pass
            return

        if is_admin(user.id):
            if kind == "a_add_id" and text.lstrip("-").isdigit():
                set_st(c, "a_add_amt", {"uid": int(text)})
                await u.message.reply_text("مقدار واریز:")
                return
            if kind == "a_add_amt" and text.isdigit():
                uid, amt = int(extra["uid"]), int(text)
                add_bal(d, uid, amt)
                d.setdefault("users", {}).setdefault(str(uid), {"name": str(uid), "username": "", "seen": time.time()})
                save(d)
                clear_st(c)
                await u.message.reply_text("✅ %s → %s" % (num(amt), uid), reply_markup=admin_kb())
                try:
                    await c.bot.send_message(uid, "واریز: %s %s" % (em(d), num(amt)))
                except Exception:
                    pass
                return
            if kind == "a_sub_id" and text.lstrip("-").isdigit():
                set_st(c, "a_sub_amt", {"uid": int(text)})
                await u.message.reply_text("مقدار برداشت:")
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
                await u.message.reply_text("✅ همگانی %s به %s کاربر" % (num(amt), num(len(ids))), reply_markup=admin_kb())
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
                else:
                    d["emoji"] = text[:8]
                save(d)
                clear_st(c)
                await u.message.reply_text("ذخیره شد.", reply_markup=admin_kb())
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
                await u.message.reply_text("استخر: %s/10" % len(d["premium_pool"]), reply_markup=admin_kb())
                return
            if kind == "a_self_photo":
                await u.message.reply_text("عکس بفرست (نه متن)")
                return
            if kind == "a_hourly" and text.isdigit():
                d["hourly_use"] = max(1, int(text))
                save(d)
                clear_st(c)
                await u.message.reply_text("مصرف ساعتی: %s" % d["hourly_use"], reply_markup=admin_kb())
                return
            if kind == "a_tpl_pick":
                key = text.strip()
                if key not in (d.get("templates") or {}):
                    await u.message.reply_text("کلید نامعتبر")
                    return
                set_st(c, "a_tpl_set", {"key": key})
                await u.message.reply_text("متن جدید قالب %s را بفرست:\nفعلی:\n%s" % (key, d["templates"][key]))
                return
            if kind == "a_tpl_set":
                d.setdefault("templates", {})[extra["key"]] = text
                save(d)
                clear_st(c)
                await u.message.reply_text("قالب ذخیره شد.", reply_markup=admin_kb())
                return
            if kind == "a_aadd" and text.lstrip("-").isdigit():
                aid = int(text)
                if aid not in [int(x) for x in d.get("admins", [])]:
                    d.setdefault("admins", []).append(aid)
                    save(d)
                clear_st(c)
                await u.message.reply_text("✅ %s" % aid, reply_markup=admin_kb())
                return

    # عکس پنل سلف
    if chat.type == ChatType.PRIVATE and st and st.get("kind") == "a_self_photo" and u.message.photo:
        d["self_photo"] = u.message.photo[-1].file_id
        save(d)
        clear_st(c)
        await u.message.reply_text("عکس پنل ذخیره شد.", reply_markup=admin_kb())
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
                    [btn("%s %s" % (em(d), num(bal(d, tuser.id))), "noop", "primary")]
                ]),
            )
        else:
            await u.message.reply_text(
                tpl(d, "balance", mention=mention_user(user), balance=num(bal(d, user.id))),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [btn("%s %s" % (em(d), num(bal(d, user.id))), "noop", "primary")]
                ]),
            )
        return

    if re.fullmatch(r"/?(لیدربرد|لیدربورد|top)", low2, re.I):
        await u.message.reply_text(leaderboard_text(d), parse_mode="HTML")
        return

    if re.fullmatch(r"/?(سلف|self)", low2, re.I):
        # پنل فقط برای زننده — در گپ هم
        await send_self_panel(c.bot, chat.id, user.id, d)
        return

    m = re.match(r"^(?:/)?(?:بازی|game)\s+(\d+)$", low2, re.I)
    if m:
        amount = int(m.group(1))
        if amount < 1:
            await u.message.reply_text("مقدار نامعتبر")
            return
        if bal(d, user.id) < amount:
            await u.message.reply_text("موجودی: %s %s" % (em(d), num(bal(d, user.id))))
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
            await u.message.reply_text("ریپلای کن: انتقال 50")
            return
        to = u.message.reply_to_message.from_user
        if to.id == user.id or to.is_bot:
            await u.message.reply_text("نامعتبر")
            return
        if bal(d, user.id) < amount:
            await u.message.reply_text("موجودی کم")
            return
        fee = int(amount * TAX)
        send_amt = amount - fee
        kb = InlineKeyboardMarkup([
            [btn("✅ تأیید", "tr_ok:%s:%s:%s" % (user.id, to.id, amount), "success")],
            [btn("❌ لغو", "tr_no:1", "danger")],
        ])
        await u.message.reply_text(
            "تأیید؟\n%s → %s\nمبلغ کسر: %s %s\nدریافتی: %s %s"
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
