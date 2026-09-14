# -*- coding: utf-8 -*-
"""ربات بازی الماس — نسخه کامل"""
from __future__ import annotations
import json, os, re, time, logging, random
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters,
)
from telegram.constants import ChatType, MessageEntityType

BOT_TOKEN = "8727762178:AAGrdb5XFjhkcdoOEIFy1s8U71idRpN0DX8"
ADMIN_ID = 7530457395
DATA = "diamond_game.json"
TAX = 0.01
GAME_TTL = 600
TEHRAN = timezone(timedelta(hours=3, minutes=30))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("dgame")


def D():
    return {
        "admins": [ADMIN_ID],
        "balances": {},
        "users": {},
        "wins": {},
        "losses": {},
        "emoji": "💎",
        "premium_emoji_id": None,
        "premium_pool": [],
        "games": {},
        "self_photo": None,
        "self_caption": "اکانت شما | Your Account",
        "hourly_use": 1,
        "min_bet": 1,
        "max_bet": 0,  # 0 = بدون سقف
        "lottery": None,
        "known_chats": [],
        "templates": {
            "balance": "━━━━━━━━━━━━\n💎 موجودی الماس\n━━━━━━━━━━━━\n\n👤 {mention}\n\n{emoji}  {balance}",
            "game_open": "━━━━━━━━━━━━\n⚔️  بازی الماس\n━━━━━━━━━━━━\n\n💰 شرط: {emoji} {amount}\n👑 میزبان: {creator}\n👥 حالت: {mode}\n\n▸ شرکت با دکمه سبز\n▸ لغو فقط میزبان\n▸ مهلت: ۱۰ دقیقه",
            "game_result": "━━━━━━━━━━━━\n🏁  نتیجه بازی\n━━━━━━━━━━━━\n\n🏆 برنده: {winner}\n💵 برد: {emoji} {win_amount}\n\n━━━━━━━━━━━━\n💀 بازنده: {loser}\n━━━━━━━━━━━━\n\nشرط: {emoji} {amount}",
            "transfer_ok": "━━━━━━━━━━━━\n✅  انتقال موفق\n━━━━━━━━━━━━\n\n📤 {from}\n📥 {to}\n\n{emoji}  {sent}",
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


def short_num(n):
    """فقط لیدربرد: 500k / 1.3m / 2.1b"""
    try:
        n = float(n)
    except Exception:
        return str(n)
    absn = abs(n)
    if absn >= 1_000_000_000_000:
        v = n / 1_000_000_000_000
        s = ("%g" % round(v, 1)) if v % 1 else str(int(v))
        return s + "t"
    if absn >= 1_000_000_000:
        v = n / 1_000_000_000
        s = ("%g" % round(v, 1)) if v % 1 else str(int(v))
        return s + "b"
    if absn >= 1_000_000:
        v = n / 1_000_000
        s = ("%g" % round(v, 1)) if v % 1 else str(int(v))
        return s + "m"
    if absn >= 1_000:
        v = n / 1_000
        s = ("%g" % round(v, 1)) if v % 1 else str(int(v))
        return s + "k"
    return str(int(n))


def parse_amount(text):
    """1000 | 1k | 1کا | 1m | 1م | 1b | 1ب | 1t | 1ت"""
    t = (text or "").strip().replace(",", "").replace(" ", "").replace("،", "")
    t = t.replace("کا", "k").replace("ک", "k")
    t = t.replace("م", "m").replace("ب", "b").replace("ت", "t")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kmbtKMBT])?", t, re.I)
    if not m:
        return None
    val = float(m.group(1))
    suf = (m.group(2) or "").lower()
    mul = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000, "t": 1_000_000_000_000}.get(suf)
    if mul is None:
        return None
    return int(val * mul)


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
    d.setdefault("wins", {}).setdefault(str(user.id), int(d.get("wins", {}).get(str(user.id), 0)))
    d.setdefault("losses", {}).setdefault(str(user.id), int(d.get("losses", {}).get(str(user.id), 0)))


def track_chat(d, chat_id):
    if not chat_id:
        return
    ks = d.setdefault("known_chats", [])
    if int(chat_id) not in [int(x) for x in ks]:
        ks.append(int(chat_id))


def bal(d, uid):
    return int(d.get("balances", {}).get(str(uid), 0))


def set_bal(d, uid, val):
    d.setdefault("balances", {})[str(uid)] = max(0, int(val))


def add_bal(d, uid, delta):
    set_bal(d, uid, bal(d, uid) + int(delta))
    return bal(d, uid)


def add_win(d, uid):
    d.setdefault("wins", {})[str(uid)] = int(d.get("wins", {}).get(str(uid), 0)) + 1


def add_loss(d, uid):
    d.setdefault("losses", {})[str(uid)] = int(d.get("losses", {}).get(str(uid), 0)) + 1


def all_user_ids(d):
    ids = set(str(k) for k in d.get("balances", {}))
    ids |= set(str(k) for k in d.get("users", {}))
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


def rank_in(mapping, uid):
    items = sorted(((int(k), int(v)) for k, v in (mapping or {}).items()), key=lambda x: -x[1])
    for i, (k, _) in enumerate(items, 1):
        if k == int(uid):
            return i
    return None


# ---------- keyboards ----------
def start_kb():
    return InlineKeyboardMarkup([
        [btn("👤 پنل سلف", "self_panel", "primary")],
        [btn("💰 موجودی", "my_bal", "primary")],
        [btn("🏆 لیدربرد", "lb", "primary")],
    ])


def admin_kb():
    return InlineKeyboardMarkup([
        [btn("➕ واریز (آیدی)", "a_add", "success"), btn("➕ واریز (یوزرنیم)", "a_add_un", "success")],
        [btn("➖ کم (آیدی)", "a_sub", "danger"), btn("➖ کم (یوزرنیم)", "a_sub_un", "danger")],
        [btn("📢 واریز همگانی", "a_add_all", "success")],
        [btn("📨 پیام همگانی", "a_bcast", "primary")],
        [btn("🗑 صفر کردن همه", "a_zero", "danger")],
        [btn("🔒 قفل شرط", "a_betlock", "primary")],
        [btn("😀 ایموجی الماس", "a_emoji", "primary"), btn("✨ استخر پرمیوم", "a_prem", "primary")],
        [btn("🖼 عکس پنل سلف", "a_self_photo", "primary")],
        [btn("📝 کپشن پنل سلف", "a_self_cap", "primary")],
        [btn("⏱ مصرف ساعتی", "a_hourly", "primary")],
        [btn("🎰 قرعه‌کشی", "a_lot", "success")],
        [btn("📄 قالب پیام", "a_tpl", "primary")],
        [btn("👤 ادمین‌ها", "a_admins", "primary")],
        [btn("❌ بستن", "close", "danger")],
    ])


def tpl_kb():
    return InlineKeyboardMarkup([
        [btn("balance", "tpl:balance", "primary")],
        [btn("game_open", "tpl:game_open", "primary")],
        [btn("game_result", "tpl:game_result", "primary")],
        [btn("transfer_ok", "tpl:transfer_ok", "primary")],
        [btn("🔙", "a_home", "danger")],
    ])


def self_panel_kb(d, uid):
    info = user_info(d, uid)
    days, rem_h = expiry_parts(d, uid)
    un = info["username"]
    if un and un != "—" and not str(un).startswith("@"):
        un = "@" + un
    o = "sp:%s" % uid
    return InlineKeyboardMarkup([
        [btn(info["name"][:28], o, "success"), btn("اسم", o, "primary")],
        [btn(str(uid), o, "success"), btn("آیدی عددی", o, "primary")],
        [btn(un[:28], o, "success"), btn("یوزرنیم", o, "primary")],
        [btn(num(info["balance"]), o, "success"), btn("موجودی الماس", o, "primary")],
        [btn("%s روز و %s ساعت" % (num(days), num(rem_h)), o, "success"), btn("انقضا", o, "primary")],
        [btn("بازگشت", "sp_back:%s" % uid, "danger")],
    ])


async def send_self_panel(bot, chat_id, uid, d, message=None):
    kb = self_panel_kb(d, uid)
    info = user_info(d, uid)
    cap = d.get("self_caption") or "اکانت شما"
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


async def maybe_finish_lottery(bot, d):
    lot = d.get("lottery")
    if not lot or lot.get("status") != "open":
        return
    end_ts = lot.get("end_ts")
    if not end_ts or time.time() < end_ts:
        return
    joined = list(lot.get("joined") or [])
    nw = min(int(lot.get("winners", 1)), len(joined))
    prize = int(lot.get("prize", 0))
    names = []
    if nw >= 1 and joined:
        winners = random.sample(joined, nw)
        for w in winners:
            add_bal(d, w, prize)
            names.append(mention_id(w))
    else:
        winners = []
    lot["status"] = "done"
    save(d)
    if names:
        msg = "🎰 قرعه‌کشی تمام شد\n\n🏆 برنده‌ها:\n" + "\n".join(names) + "\n\nهر کدام: %s %s" % (em(d), num(prize))
    else:
        msg = "🎰 قرعه‌کشی تمام شد — برنده‌ای نبود."
    for cid in d.get("known_chats") or []:
        try:
            await bot.send_message(int(cid), msg, parse_mode="HTML")
        except Exception:
            pass
    try:
        await bot.send_message(ADMIN_ID, msg, parse_mode="HTML")
    except Exception:
        pass


# ---------- handlers ----------
async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    clear_st(c)
    d = load()
    touch_user(d, u.effective_user)
    track_chat(d, u.effective_chat.id)
    save(d)
    if u.effective_chat.type != ChatType.PRIVATE:
        await u.message.reply_text("گپ: بازی | موجودی | انتقال | لیدربرد | وینرها | لوزرها | سلف")
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
    await maybe_finish_lottery(c.bot, d)
    d = load()

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
                try:
                    await q.edit_message_caption(caption="منو", reply_markup=start_kb())
                except Exception:
                    pass
        else:
            try:
                await q.message.delete()
            except Exception:
                pass
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
        await q.edit_message_text(leaderboard_text(d, user.id), parse_mode="HTML", reply_markup=start_kb())
        return

    if data == "self_panel":
        await send_self_panel(c.bot, q.message.chat_id, user.id, d, message=q.message)
        return

    # انتخاب تعداد بازیکن
    if data.startswith("mode:"):
        # mode:2:amount یا mode:3:amount
        parts = data.split(":")
        if len(parts) < 3:
            return
        need = int(parts[1])
        amount = int(parts[2])
        # فقط کسی که بازی را شروع کرده
        pending = c.user_data.get("pending_game")
        if not pending or pending.get("uid") != user.id or int(pending.get("amount", 0)) != amount:
            # از callback message هم چک کن
            pass
        min_b = int(d.get("min_bet") or 1)
        max_b = int(d.get("max_bet") or 0)
        if amount < min_b or (max_b and amount > max_b):
            await q.answer("خارج از قفل شرط", show_alert=True)
            return
        if bal(d, user.id) < amount:
            await q.answer("موجودی کم", show_alert=True)
            return
        add_bal(d, user.id, -amount)
        gid = "g%d%d" % (int(time.time()), random.randint(10, 99))
        d.setdefault("games", {})[gid] = {
            "creator": user.id,
            "amount": amount,
            "need": need,
            "players": [user.id],
            "names": {str(user.id): user.full_name},
            "status": "open",
            "chat_id": q.message.chat_id,
            "ts": time.time(),
        }
        track_chat(d, q.message.chat_id)
        save(d)
        mode = "۲ نفره" if need == 2 else "۳ نفره"
        kb = InlineKeyboardMarkup([
            [btn("✅ شرکت", "join:%s" % gid, "success")],
            [btn("🚫 لغو", "cancel:%s" % gid, "danger")],
        ])
        await q.edit_message_text(
            tpl(d, "game_open", amount=num(amount), creator=mention_user(user), mode=mode),
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

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
        need = int(game.get("need") or 2)
        if bal(d, user.id) < amount:
            await q.answer("موجودی کم", show_alert=True)
            return
        add_bal(d, user.id, -amount)
        game["players"].append(user.id)
        game["names"][str(user.id)] = user.full_name
        if len(game["players"]) >= need:
            game["status"] = "done"
            players = list(game["players"])
            winner = random.choice(players)
            losers = [p for p in players if p != winner]
            pot = amount * len(players)
            win_amount = int(pot * (1 - TAX))
            add_bal(d, winner, win_amount)
            add_win(d, winner)
            for L in losers:
                add_loss(d, L)
            save(d)
            wname = game["names"].get(str(winner), str(winner))
            lnames = ", ".join(mention_id(L, game["names"].get(str(L))) for L in losers)
            text = tpl(
                d, "game_result",
                amount=num(amount),
                winner=mention_id(winner, wname),
                loser=lnames,
                win_amount=num(win_amount),
            )
            rows = [[btn("✅ %s | %s" % (wname[:14], num(bal(d, winner))), "noop", "success")]]
            for L in losers:
                ln = game["names"].get(str(L), str(L))
                rows.append([btn("❌ %s | %s" % (ln[:14], num(bal(d, L))), "noop", "danger")])
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
            return
        save(d)
        left = need - len(game["players"])
        kb = InlineKeyboardMarkup([
            [btn("✅ شرکت (مانده %s)" % left, "join:%s" % gid, "success")],
            [btn("🚫 لغو", "cancel:%s" % gid, "danger")],
        ])
        mode = "۲ نفره" if need == 2 else "۳ نفره"
        await q.edit_message_text(
            tpl(d, "game_open", amount=num(amount), creator=mention_id(game["creator"], game["names"].get(str(game["creator"]))), mode=mode)
            + "\n\nپیوسته: %s / %s" % (len(game["players"]), need),
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
        try:
            frm = int(data.split(":")[1])
            if user.id != frm:
                await q.answer("پنل برای تو نیست", show_alert=True)
                return
        except Exception:
            pass
        await q.edit_message_text("لغو شد.")
        return

    if not is_admin(user.id):
        return

    if data == "a_home":
        clear_st(c)
        await q.edit_message_text("🎛 پنل", reply_markup=admin_kb())
        return
    if data == "a_add":
        set_st(c, "a_add_id")
        await q.edit_message_text("آیدی عددی:")
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
        await q.edit_message_text("مقدار همگانی (مثال 1k یا 1000):\n%s کاربر" % num(len(all_user_ids(d))))
        return
    if data == "a_bcast":
        set_st(c, "a_bcast_where")
        await q.edit_message_text("پیام همگانی کجا؟ بفرست: پیوی\nیا: گپ")
        return
    if data == "a_zero":
        set_st(c, "a_zero_confirm")
        await q.edit_message_text("برای تأیید بنویس: تأیید صفر")
        return
    if data == "a_betlock":
        set_st(c, "a_bet_min")
        await q.edit_message_text("حداقل شرط (مثال 100 یا 1k):")
        return
    if data == "a_emoji":
        set_st(c, "a_emoji")
        await q.edit_message_text("ایموجی پرمیوم یا متنی:")
        return
    if data == "a_prem":
        set_st(c, "a_prem")
        await q.edit_message_text("پرمیوم بفرست. الان: %s/10" % len(d.get("premium_pool") or []))
        return
    if data == "a_self_photo":
        set_st(c, "a_self_photo")
        await q.edit_message_text("عکس پنل سلف:")
        return
    if data == "a_self_cap":
        set_st(c, "a_self_cap")
        await q.edit_message_text("کپشن پنل:\n%s" % (d.get("self_caption") or ""))
        return
    if data == "a_hourly":
        set_st(c, "a_hourly")
        await q.edit_message_text("مصرف ساعتی:")
        return
    if data == "a_lot":
        set_st(c, "a_lot_prize")
        await q.edit_message_text("جایزه هر برنده (مثال 10k):")
        return
    if data == "a_tpl":
        await q.edit_message_text(
            "یک قالب را انتخاب کن:\n\n"
            "متغیرها (کپی کن):\n"
            "<code>{mention}</code> تگ کاربر\n"
            "<code>{emoji}</code> ایموجی الماس\n"
            "<code>{balance}</code> موجودی\n"
            "<code>{amount}</code> مبلغ شرط\n"
            "<code>{creator}</code> میزبان\n"
            "<code>{mode}</code> دو/سه نفره\n"
            "<code>{winner}</code> برنده\n"
            "<code>{loser}</code> بازنده\n"
            "<code>{win_amount}</code> مبلغ برد\n"
            "<code>{from}</code> فرستنده\n"
            "<code>{to}</code> گیرنده\n"
            "<code>{sent}</code> مبلغ رسیده",
            parse_mode="HTML",
            reply_markup=tpl_kb(),
        )
        return
    if data.startswith("tpl:"):
        key = data.split(":")[1]
        if key not in (d.get("templates") or {}):
            return
        set_st(c, "a_tpl_set", {"key": key})
        await q.edit_message_text(
            "متن جدید برای <code>%s</code>:\n\nفعلی:\n%s" % (key, d["templates"][key]),
            parse_mode="HTML",
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


def leaderboard_text(d, viewer_id=None):
    items = sorted(
        ((int(uid), int(v)) for uid, v in (d.get("balances") or {}).items() if int(v) > 0),
        key=lambda x: -x[1],
    )
    top = items[:10]
    if not top:
        return "لیدربرد خالی است."
    lines = ["🏆 <b>۱۰ نفر برتر</b>\n"]
    for i, (uid, v) in enumerate(top, 1):
        name = (d.get("users") or {}).get(str(uid), {}).get("name") or str(uid)
        lines.append("%s: %s → %s" % (i, name, short_num(v)))
    if viewer_id is not None:
        r = rank_in(d.get("balances") or {}, viewer_id)
        lines.append("\nرتبه شما\n%s" % (r if r else "—"))
    return "\n".join(lines)


def wins_text(d, viewer_id=None):
    items = sorted(
        ((int(uid), int(v)) for uid, v in (d.get("wins") or {}).items() if int(v) > 0),
        key=lambda x: -x[1],
    )[:10]
    if not items:
        return "هنوز بردی ثبت نشده."
    lines = ["🏆 <b>وینرها — بیشترین برد</b>\n"]
    for i, (uid, v) in enumerate(items, 1):
        name = (d.get("users") or {}).get(str(uid), {}).get("name") or str(uid)
        lines.append("%s: %s → %s برد" % (i, name, num(v)))
    if viewer_id is not None:
        r = rank_in(d.get("wins") or {}, viewer_id)
        lines.append("\nرتبه شما\n%s" % (r if r else "—"))
    return "\n".join(lines)


def losses_text(d, viewer_id=None):
    items = sorted(
        ((int(uid), int(v)) for uid, v in (d.get("losses") or {}).items() if int(v) > 0),
        key=lambda x: -x[1],
    )[:10]
    if not items:
        return "هنوز باختی ثبت نشده."
    lines = ["💀 <b>لوزرها — بیشترین باخت</b>\n"]
    for i, (uid, v) in enumerate(items, 1):
        name = (d.get("users") or {}).get(str(uid), {}).get("name") or str(uid)
        lines.append("%s: %s → %s باخت" % (i, name, num(v)))
    if viewer_id is not None:
        r = rank_in(d.get("losses") or {}, viewer_id)
        lines.append("\nرتبه شما\n%s" % (r if r else "—"))
    return "\n".join(lines)


async def resolve_username(bot, uname):
    uname = uname.lstrip("@")
    ch = await bot.get_chat("@" + uname)
    name = getattr(ch, "full_name", None) or getattr(ch, "first_name", None) or uname
    return ch.id, name


async def on_text(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    user = u.effective_user
    d = load()
    touch_user(d, user)
    track_chat(d, u.effective_chat.id)
    save(d)
    await expire_games(d)
    await maybe_finish_lottery(c.bot, d)
    d = load()
    text = (u.message.text or "").strip()
    chat = u.effective_chat
    st = get_st(c)

    # کسر ادمین اصلی در گپ
    if is_main(user.id) and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        m = re.match(r"^کسر\s+(.+)$", text)
        if m and u.message.reply_to_message and u.message.reply_to_message.from_user:
            amt = parse_amount(m.group(1))
            if amt is None or amt < 1:
                await u.message.reply_text("مبلغ نامعتبر")
                return
            tuser = u.message.reply_to_message.from_user
            touch_user(d, tuser)
            add_bal(d, tuser.id, -amt)
            save(d)
            await u.message.reply_text(
                "کسر از %s\n%s %s" % (mention_user(tuser), em(d), num(bal(d, tuser.id))),
                parse_mode="HTML",
            )
            return

    if chat.type == ChatType.PRIVATE and st and is_admin(user.id):
        kind = st.get("kind")
        extra = st.get("extra") or {}

        if kind == "a_add_id" and text.lstrip("-").isdigit():
            set_st(c, "a_add_amt", {"uid": int(text)})
            await u.message.reply_text("مقدار (1k / 1000 / 1m):")
            return
        if kind == "a_add_un":
            try:
                uid, name = await resolve_username(c.bot, text)
                d.setdefault("users", {})[str(uid)] = {"name": name, "username": text.lstrip("@"), "seen": time.time()}
                set_st(c, "a_add_amt", {"uid": uid})
                await u.message.reply_text("%s — مقدار:" % name)
            except Exception as e:
                await u.message.reply_text("پیدا نشد: %s" % e)
            return
        if kind == "a_add_amt":
            amt = parse_amount(text)
            if amt is None:
                await u.message.reply_text("مبلغ نامعتبر")
                return
            uid = int(extra["uid"])
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
                await u.message.reply_text("%s — مقدار:" % name)
            except Exception as e:
                await u.message.reply_text("پیدا نشد: %s" % e)
            return
        if kind == "a_sub_amt":
            amt = parse_amount(text)
            if amt is None:
                await u.message.reply_text("نامعتبر")
                return
            uid = int(extra["uid"])
            add_bal(d, uid, -amt)
            save(d)
            clear_st(c)
            await u.message.reply_text("✅ موجودی: %s" % num(bal(d, uid)), reply_markup=admin_kb())
            return
        if kind == "a_add_all":
            amt = parse_amount(text)
            if amt is None:
                await u.message.reply_text("نامعتبر")
                return
            ids = all_user_ids(d)
            for uid in ids:
                add_bal(d, uid, amt)
            save(d)
            clear_st(c)
            await u.message.reply_text("✅ %s به %s نفر" % (num(amt), num(len(ids))), reply_markup=admin_kb())
            return
        if kind == "a_bcast_where":
            if text.strip() not in ("پیوی", "گپ"):
                await u.message.reply_text("پیوی یا گپ")
                return
            set_st(c, "a_bcast_msg", {"where": text.strip()})
            await u.message.reply_text("متن پیام:")
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
                for cid in d.get("known_chats") or []:
                    try:
                        await c.bot.send_message(int(cid), text)
                        ok += 1
                    except Exception:
                        fail += 1
            clear_st(c)
            await u.message.reply_text("ارسال ✅%s ❌%s" % (ok, fail), reply_markup=admin_kb())
            return
        if kind == "a_zero_confirm":
            if text.strip() != "تأیید صفر":
                await u.message.reply_text("بنویس: تأیید صفر")
                return
            for uid in list(d.get("balances", {}).keys()):
                d["balances"][uid] = 0
            save(d)
            clear_st(c)
            await u.message.reply_text("همه صفر شد.", reply_markup=admin_kb())
            return
        if kind == "a_bet_min":
            amt = parse_amount(text)
            if amt is None or amt < 1:
                await u.message.reply_text("نامعتبر")
                return
            set_st(c, "a_bet_max", {"min": amt})
            await u.message.reply_text("حداکثر شرط (0 = بدون سقف):")
            return
        if kind == "a_bet_max":
            amt = parse_amount(text)
            if amt is None:
                await u.message.reply_text("نامعتبر")
                return
            d["min_bet"] = int(extra["min"])
            d["max_bet"] = int(amt)
            save(d)
            clear_st(c)
            await u.message.reply_text("قفل شرط: %s تا %s" % (num(d["min_bet"]), num(d["max_bet"]) if d["max_bet"] else "∞"), reply_markup=admin_kb())
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
                await u.message.reply_text("✅ پرمیوم\n<tg-emoji emoji-id=\"%s\">💎</tg-emoji>" % eid, parse_mode="HTML", reply_markup=admin_kb())
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
            await u.message.reply_text("✅ %s/10\n<tg-emoji emoji-id=\"%s\">💎</tg-emoji>" % (len(d["premium_pool"]), eid), parse_mode="HTML", reply_markup=admin_kb())
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
            await u.message.reply_text("مصرف: %s" % d["hourly_use"], reply_markup=admin_kb())
            return
        if kind == "a_lot_prize":
            amt = parse_amount(text)
            if amt is None:
                await u.message.reply_text("نامعتبر")
                return
            set_st(c, "a_lot_win", {"prize": amt})
            await u.message.reply_text("تعداد برنده‌ها:")
            return
        if kind == "a_lot_win" and text.isdigit():
            set_st(c, "a_lot_end", {"prize": extra["prize"], "winners": int(text)})
            await u.message.reply_text("زمان پایان به دقیقه (مثال 60 = یک ساعت):")
            return
        if kind == "a_lot_end" and text.isdigit():
            mins = int(text)
            end_ts = time.time() + mins * 60
            ids = list(all_user_ids(d))
            lot = {
                "prize": int(extra["prize"]),
                "winners": int(extra["winners"]),
                "capacity": len(ids),
                "joined": [int(x) for x in ids],
                "status": "open",
                "end_ts": end_ts,
            }
            d["lottery"] = lot
            save(d)
            clear_st(c)
            end_local = datetime.fromtimestamp(end_ts, TEHRAN).strftime("%Y-%m-%d %H:%M")
            msg = (
                "🎰 قرعه‌کشی شروع شد\n\n"
                "🎁 جایزه هر برنده: %s %s\n"
                "👥 شرکت‌کننده: %s (همه کاربران)\n"
                "🏆 تعداد برنده: %s\n"
                "⏰ پایان: %s (تهران)"
                % (em(d), num(lot["prize"]), num(len(ids)), num(lot["winners"]), end_local)
            )
            ok = 0
            for cid in d.get("known_chats") or []:
                try:
                    await c.bot.send_message(int(cid), msg, parse_mode="HTML")
                    ok += 1
                except Exception:
                    pass
            await u.message.reply_text("قرعه ثبت شد. ارسال به %s گپ.\n%s" % (ok, msg), parse_mode="HTML", reply_markup=admin_kb())
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

    low = text
    low2 = re.sub(r"^@\w+\s+", "", low)
    low2 = re.sub(r"^/(\w+)@\w+", r"/\1", low2)

    if re.fullmatch(r"/?(موجودی|bal)", low2, re.I):
        if u.message.reply_to_message and u.message.reply_to_message.from_user:
            tuser = u.message.reply_to_message.from_user
            touch_user(d, tuser)
            save(d)
            target, tid = tuser, tuser.id
        else:
            target, tid = user, user.id
        await u.message.reply_text(
            tpl(d, "balance", mention=mention_user(target), balance=num(bal(d, tid))),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [btn("%s %s" % (em_plain(d), num(bal(d, tid))), "noop", "primary")]
            ]),
        )
        return

    if re.fullmatch(r"/?(لیدربرد|لیدربورد|top)", low2, re.I):
        await u.message.reply_text(leaderboard_text(d, user.id), parse_mode="HTML")
        return

    if re.fullmatch(r"/?(وینرها|winners|wins)", low2, re.I):
        await u.message.reply_text(wins_text(d, user.id), parse_mode="HTML")
        return

    if re.fullmatch(r"/?(لوزرها|losers|losses)", low2, re.I):
        await u.message.reply_text(losses_text(d, user.id), parse_mode="HTML")
        return

    if re.fullmatch(r"/?(سلف|self)", low2, re.I):
        await send_self_panel(c.bot, chat.id, user.id, d)
        return

    m = re.match(r"^(?:/)?(?:بازی|game)\s+(.+)$", low2, re.I)
    if m:
        amount = parse_amount(m.group(1))
        if amount is None or amount < 1:
            await u.message.reply_text("مبلغ نامعتبر — مثال: بازی 100 یا بازی 1k")
            return
        min_b = int(d.get("min_bet") or 1)
        max_b = int(d.get("max_bet") or 0)
        if amount < min_b:
            await u.message.reply_text("حداقل شرط: %s" % num(min_b))
            return
        if max_b and amount > max_b:
            await u.message.reply_text("حداکثر شرط: %s" % num(max_b))
            return
        if bal(d, user.id) < amount:
            await u.message.reply_text("موجودی: %s %s" % (em(d), num(bal(d, user.id))), parse_mode="HTML")
            return
        c.user_data["pending_game"] = {"uid": user.id, "amount": amount}
        kb = InlineKeyboardMarkup([
            [btn("۲ نفره", "mode:2:%s" % amount, "primary")],
            [btn("۳ نفره", "mode:3:%s" % amount, "success")],
        ])
        await u.message.reply_text(
            "بازی %s %s\nحالت را انتخاب کن:" % (em(d), num(amount)),
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    m = re.match(r"^(?:/)?(?:انتقال|transfer)\s+(.+)$", low2, re.I)
    if m:
        amount = parse_amount(m.group(1))
        if amount is None or amount < 1:
            await u.message.reply_text("مبلغ نامعتبر")
            return
        if not u.message.reply_to_message or not u.message.reply_to_message.from_user:
            await u.message.reply_text("ریپلای + انتقال 50 یا انتقال 1k")
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
