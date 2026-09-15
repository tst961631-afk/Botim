# -*- coding: utf-8 -*-
"""ربات بازی الماس — نسخه کامل + دوئل + شرط تماشاچی + همگانی تأخیری"""
from __future__ import annotations
import json, os, re, time, logging, random, asyncio, asyncio
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
        "self_caption": "اکانت شما",
        "hourly_use": 1,
        "min_bet": 1,
        "max_bet": 0,
        "lottery": None,
        "known_chats": [],
        # همگانی تأخیری برای کاربران جدید
        "grant": None,
        "bot_bank": 0,
        "donations": {},
        "top1_reward": None,
        "chat_users": {},
        "templates": {
            "balance": "موجودی الماس\n\n{mention}\n{emoji} {balance}",
            "game_open": "بازی باز است\n\nشرط: {emoji} {amount}\nمیزبان: {creator}\nحالت: {mode}\n\nشرکت با دکمه سبز\nلغو فقط برای میزبان\nمهلت: ۱۰ دقیقه",
            "game_result": "نتیجه بازی\n\nبرنده: {winner}\nبرد: {emoji} {win_amount}\n\nبازنده: {loser}\n\nشرط میز: {emoji} {amount}",
            "transfer_ok": "انتقال انجام شد\n\nاز: {from}\nبه: {to}\n\n{emoji} {sent}",
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
    try:
        n = float(n)
    except Exception:
        return str(n)
    absn = abs(n)
    for thr, suf in ((1e12, "t"), (1e9, "b"), (1e6, "m"), (1e3, "k")):
        if absn >= thr:
            v = n / thr
            s = ("%g" % round(v, 1)) if v % 1 else str(int(v))
            return s + suf
    return str(int(n))


def parse_amount(text):
    t = (text or "").strip().replace(",", "").replace(" ", "").replace("،", "")
    t = t.replace("کا", "k").replace("ک", "k").replace("م", "m").replace("ب", "b").replace("ت", "t")
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


def track_chat(d, chat_id, user=None):
    if not chat_id:
        return
    ks = d.setdefault("known_chats", [])
    if int(chat_id) not in [int(x) for x in ks]:
        ks.append(int(chat_id))
    if user and not getattr(user, "is_bot", False):
        cu = d.setdefault("chat_users", {}).setdefault(str(chat_id), [])
        if int(user.id) not in [int(x) for x in cu]:
            cu.append(int(user.id))


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


def top1_uid(d):
    items = [(int(u), int(v)) for u, v in (d.get("balances") or {}).items()]
    if not items:
        return None
    items.sort(key=lambda x: -x[1])
    return items[0][0] if items[0][1] > 0 else None


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


def apply_grant(d, uid):
    """همگانی تأخیری برای کاربر جدید"""
    g = d.get("grant")
    if not g:
        return 0
    if time.time() > float(g.get("until_ts") or 0):
        return 0
    claimed = [int(x) for x in (g.get("claimed") or [])]
    if int(uid) in claimed:
        return 0
    # فقط اگر قبلاً موجودی/ثبت جدی نداشته (اولین تعامل بعد از همگانی)
    amt = int(g.get("amount") or 0)
    if amt < 1:
        return 0
    add_bal(d, uid, amt)
    claimed.append(int(uid))
    g["claimed"] = claimed
    d["grant"] = g
    return amt


async def announce_record(bot, d, uid, chat_id=None):
    """اگر کاربر نفر اول شد اعلام کن"""
    t1 = top1_uid(d)
    if t1 is None or int(t1) != int(uid):
        return
    prev = d.get("last_top1")
    if prev is not None and int(prev) == int(uid):
        return
    d["last_top1"] = int(uid)
    save(d)
    name = user_info(d, uid)["name"]
    msg = "👑 رکورد جدید!\n%s الان نفر اول لیدربرده\n%s %s" % (
        mention_id(uid, name), em(d), num(bal(d, uid)),
    )
    targets = set(int(x) for x in (d.get("known_chats") or []))
    if chat_id:
        targets.add(int(chat_id))
    for cid in targets:
        try:
            await bot.send_message(cid, msg, parse_mode="HTML")
        except Exception:
            pass


# ---------- keyboards ----------
def start_kb():
    return InlineKeyboardMarkup([
        [btn("👤 پنل سلف", "self_panel", "primary")],
        [btn("💰 موجودی", "my_bal", "primary")],
        [btn("🏆 لیدربرد", "lb", "primary")],
    ])


def admin_kb():
    return InlineKeyboardMarkup([
        [btn("➕ واریز (آیدی)", "a_add", "success"), btn("➖ کم (آیدی)", "a_sub", "danger")],
        [btn("📢 واریز همگانی", "a_add_all", "success")],
        [btn("📨 پیام همگانی", "a_bcast", "primary")],
        [btn("🗑 صفر کردن همه", "a_zero", "danger")],
        [btn("🔒 قفل شرط", "a_betlock", "primary")],
        [btn("😀 ایموجی الماس", "a_emoji", "primary")],
        [btn("📝 کپشن پنل سلف", "a_self_cap", "primary")],
        [btn("⏱ مصرف ساعتی", "a_hourly", "primary")],
        [btn("🎰 قرعه‌کشی", "a_lot", "success"), btn("🚫 لغو قرعه", "a_lot_cancel", "danger")],
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
    wins = int(d.get("wins", {}).get(str(uid), 0))
    losses = int(d.get("losses", {}).get(str(uid), 0))
    rk = rank_in(d.get("balances") or {}, uid) or "—"
    return InlineKeyboardMarkup([
        [btn(info["name"][:28], o, "success"), btn("اسم", o, "primary")],
        [btn(str(uid), o, "success"), btn("آیدی", o, "primary")],
        [btn(un[:28], o, "success"), btn("یوزرنیم", o, "primary")],
        [btn(num(info["balance"]), o, "success"), btn("موجودی", o, "primary")],
        [btn("#%s" % rk, o, "success"), btn("رتبه", o, "primary")],
        [btn("%s برد" % num(wins), o, "success"), btn("%s باخت" % num(losses), o, "danger")],
        [btn("%s روز و %s ساعت" % (num(days), num(rem_h)), o, "success"), btn("انقضا", o, "primary")],
        [btn("بازگشت", "sp_back:%s" % uid, "danger")],
    ])


def game_kb(d, game, gid):
    rows = [
        [btn("✅ شرکت", "join:%s" % gid, "success")],
        [btn("🚫 لغو", "cancel:%s" % gid, "danger")],
    ]
    # شرط تماشاچی روی بازیکنان فعلی
    for pid in game.get("players") or []:
        name = game.get("names", {}).get(str(pid), str(pid))[:12]
        rows.append([btn("👁 شرط روی %s" % name, "sidebet:%s:%s" % (gid, pid), "primary")])
    return InlineKeyboardMarkup(rows)


async def send_self_panel(bot, chat_id, uid, d, message=None):
    kb = self_panel_kb(d, uid)
    info = user_info(d, uid)
    rk = rank_in(d.get("balances") or {}, uid) or "—"
    wins = int(d.get("wins", {}).get(str(uid), 0))
    losses = int(d.get("losses", {}).get(str(uid), 0))
    cap = d.get("self_caption") or "اکانت شما"
    text = "%s\n\n%s\nرتبه #%s | برد %s | باخت %s" % (cap, info["name"], rk, num(wins), num(losses))
    photo_id = None
    try:
        photos = await bot.get_user_profile_photos(int(uid), limit=1)
        if photos.total_count > 0:
            photo_id = photos.photos[0][-1].file_id
    except Exception:
        pass
    if message:
        try:
            if not message.photo:
                await message.edit_text(text, reply_markup=kb)
                return
        except Exception:
            pass
    if photo_id:
        try:
            await bot.send_photo(chat_id, photo=photo_id, caption=text, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id, text, reply_markup=kb)


async def notify_deposit(bot, uid, amount, d, broadcast=False):
    new_b = bal(d, uid)
    if broadcast:
        text = "%s\nالماس از طرف ادمین برای کاربرا شارژ شد" % num(amount)
    else:
        text = "%s\nالماس از طرف مدیریت انتقال یافت" % num(amount)
    kb = InlineKeyboardMarkup([[btn("%s %s" % (em_plain(d), num(new_b)), "noop", "primary")]])
    try:
        await bot.send_message(int(uid), text, reply_markup=kb)
    except Exception:
        pass


async def expire_games(d):
    now = time.time()
    ch = False
    for g in (d.get("games") or {}).values():
        if g.get("status") == "open" and now - g.get("ts", now) >= GAME_TTL:
            amount = int(g["amount"])
            for pid in g.get("players") or []:
                add_bal(d, pid, amount)
            # برگشت شرط تماشاچی
            for b in g.get("side_bets") or []:
                add_bal(d, b["uid"], int(b["amount"]))
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
        for w in random.sample(joined, nw):
            add_bal(d, w, prize)
            names.append(mention_id(w))
    lot["status"] = "done"
    save(d)
    msg = "🎰 قرعه‌کشی تمام شد\n\n🏆 برنده‌ها:\n" + ("\n".join(names) if names else "—") + "\n\nهر کدام: %s %s" % (em(d), num(prize))
    for cid in d.get("known_chats") or []:
        try:
            await bot.send_message(int(cid), msg, parse_mode="HTML")
        except Exception:
            pass


async def finish_game(q, c, d, gid, game):
    """انیمیشن + نتیجه + شرط تماشاچی + رکورد"""
    players = list(game["players"])
    amount = int(game["amount"])
    try:
        await q.edit_message_text("✨")
    except Exception:
        pass
    await asyncio.sleep(1.6)
    winner = random.choice(players)
    losers = [p for p in players if p != winner]
    pot = amount * len(players)
    win_amount = int(pot * (1 - TAX))
    add_bal(d, winner, win_amount)
    add_win(d, winner)
    for L in losers:
        add_loss(d, L)
    # side bets: برد روی برنده = x2 منهای کمیسیون ساده
    side_lines = []
    for b in game.get("side_bets") or []:
        bu, onp, bam = int(b["uid"]), int(b["on"]), int(b["amount"])
        if onp == winner:
            pay = int(bam * 1.9)
            add_bal(d, bu, pay)
            side_lines.append("%s برد شرط %s" % (mention_id(bu), num(pay)))
        # باخت: پولش قبلاً کسر شده
    game["status"] = "done"
    game["winner"] = winner
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
    if side_lines:
        text += "\n\n👁 شرط تماشاچی:\n" + "\n".join(side_lines)
    rows = [[btn("✅ %s | %s" % (wname[:14], num(bal(d, winner))), "noop", "success")]]
    for L in losers:
        ln = game["names"].get(str(L), str(L))
        rows.append([btn("❌ %s | %s" % (ln[:14], num(bal(d, L))), "noop", "danger")])
    await q.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
    pass  # no record spam


# ---------- handlers ----------
async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    clear_st(c)
    d = load()
    touch_user(d, u.effective_user)
    track_chat(d, u.effective_chat.id)
    gamt = apply_grant(d, u.effective_user.id)
    save(d)
    extra = ""
    if gamt:
        extra = "\n🎁 همگانی: +%s" % num(gamt)
    if u.effective_chat.type != ChatType.PRIVATE:
        await u.message.reply_text("گپ: بازی | دعوت | موجودی | انتقال | لیدربرد | وینرها | لوزرها | سلف" + extra)
        return
    await u.message.reply_text("سلام 👋" + extra, reply_markup=start_kb())


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
    gamt = apply_grant(d, user.id)
    save(d)
    await expire_games(d)
    await maybe_finish_lottery(c.bot, d)
    d = load()

    if data.startswith("sp:"):
        if user.id != int(data.split(":")[1]):
            await q.answer("تو دسترسی بهش نداری", show_alert=True)
            return
        await q.answer()
        return

    if data.startswith("sp_back:"):
        if user.id != int(data.split(":")[1]):
            await q.answer("تو دسترسی بهش نداری", show_alert=True)
            return
        await q.answer()
        if u.effective_chat.type == ChatType.PRIVATE:
            try:
                await q.edit_message_text("منو:", reply_markup=start_kb())
            except Exception:
                pass
        else:
            try:
                await q.message.delete()
            except Exception:
                pass
        return

    await q.answer()
    if gamt:
        try:
            await c.bot.send_message(user.id, "🎁 همگانی تأخیری: +%s %s" % (num(gamt), em_plain(d)))
        except Exception:
            pass

    if data == "close":
        await q.edit_message_text("بسته شد.")
        return
    if data == "noop":
        return

    if data == "my_bal":
        await q.edit_message_text(
            tpl(d, "balance", mention=mention_user(user), balance=num(bal(d, user.id))),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[btn("%s %s" % (em_plain(d), num(bal(d, user.id))), "noop", "primary")]]),
        )
        return

    if data == "lb":
        await q.edit_message_text(leaderboard_text(d, user.id), parse_mode="HTML", reply_markup=start_kb())
        return

    if data == "self_panel":
        await send_self_panel(c.bot, q.message.chat_id, user.id, d, message=q.message)
        return

    # mode:2:amount:creator
    if data.startswith("mode:"):
        parts = data.split(":")
        if len(parts) < 4:
            await q.answer("منقضی", show_alert=True)
            return
        need, amount, creator_id = int(parts[1]), int(parts[2]), int(parts[3])
        if user.id != creator_id:
            await q.answer("تو دسترسی بهش نداری", show_alert=True)
            return
        min_b, max_b = int(d.get("min_bet") or 1), int(d.get("max_bet") or 0)
        if amount < min_b or (max_b and amount > max_b):
            await q.answer("خارج از قفل شرط", show_alert=True)
            return
        if bal(d, user.id) < amount:
            await q.answer("موجودی کم", show_alert=True)
            return
        add_bal(d, user.id, -amount)
        gid = "g%d%d" % (int(time.time()), random.randint(10, 99))
        game = {
            "creator": user.id,
            "amount": amount,
            "need": need,
            "players": [user.id],
            "names": {str(user.id): user.full_name},
            "status": "open",
            "chat_id": q.message.chat_id,
            "ts": time.time(),
            "invite": None,
            "side_bets": [],
        }
        d.setdefault("games", {})[gid] = game
        track_chat(d, q.message.chat_id)
        save(d)
        mode = "۲ نفره" if need == 2 else "۳ نفره"
        await q.edit_message_text(
            tpl(d, "game_open", amount=num(amount), creator=mention_user(user), mode=mode),
            parse_mode="HTML",
            reply_markup=game_kb(d, game, gid),
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
        if game.get("invite") and user.id != int(game["invite"]) and user.id != int(game["creator"]):
            await q.answer("این دوئل خصوصی است", show_alert=True)
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
            await finish_game(q, c, d, gid, game)
            return
        save(d)
        left = need - len(game["players"])
        mode = "۲ نفره" if need == 2 else "۳ نفره"
        inv = "\n🔒 دوئل خصوصی" if game.get("invite") else ""
        await q.edit_message_text(
            tpl(d, "game_open", amount=num(amount), creator=mention_id(game["creator"], game["names"].get(str(game["creator"]))), mode=mode)
            + "\n\nپیوسته: %s / %s%s" % (len(game["players"]), need, inv),
            parse_mode="HTML",
            reply_markup=game_kb(d, game, gid),
        )
        return

    if data.startswith("cancel:"):
        gid = data.split(":")[1]
        game = d.get("games", {}).get(gid)
        if not game or game.get("status") != "open":
            await q.answer("قابل لغو نیست", show_alert=True)
            return
        if user.id != game["creator"]:
            await q.answer("تو دسترسی بهش نداری", show_alert=True)
            return
        amount = int(game["amount"])
        for pid in game.get("players", []):
            add_bal(d, pid, amount)
        for b in game.get("side_bets") or []:
            add_bal(d, b["uid"], int(b["amount"]))
        game["status"] = "cancelled"
        save(d)
        await q.edit_message_text("🚫 لغو شد — الماس‌ها برگشت.")
        return

    # شرط تماشاچی: sidebet:gid:player → بعد مبلغ از state
    if data.startswith("sidebet:"):
        parts = data.split(":")
        if len(parts) < 3:
            return
        gid, onp = parts[1], int(parts[2])
        game = d.get("games", {}).get(gid)
        if not game or game.get("status") != "open":
            await q.answer("بازی بسته است", show_alert=True)
            return
        if user.id in game.get("players", []):
            await q.answer("بازیکن نمی‌تواند شرط ببندد", show_alert=True)
            return
        set_st(c, "sidebet_amt", {"gid": gid, "on": onp})
        try:
            me = await c.bot.get_me()
            link = "https://t.me/%s?start=sb_%s_%s" % (me.username, gid, onp)
            await c.bot.send_message(user.id, "مبلغ شرط را بفرست (مثال 1k):\n%s" % link)
            await q.answer("پیوی ربات را چک کن", show_alert=True)
        except Exception:
            await q.answer("اول ربات را استارت کن", show_alert=True)
        return

    if data.startswith("tr_ok:"):
        parts = data.split(":")
        frm, to, amount = int(parts[1]), int(parts[2]), int(parts[3])
        if user.id != frm:
            await q.answer("تو دسترسی بهش نداری", show_alert=True)
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
        pass
        return

    if data.startswith("tr_no:"):
        try:
            if user.id != int(data.split(":")[1]):
                await q.answer("تو دسترسی بهش نداری", show_alert=True)
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
    if data == "a_sub":
        set_st(c, "a_sub_id")
        await q.edit_message_text("آیدی برای کم کردن:")
        return
    if data == "a_add_all":
        set_st(c, "a_add_all")
        await q.edit_message_text("مقدار همگانی (مثال 5k):\n%s کاربر فعلی" % num(len(all_user_ids(d))))
        return
    if data == "a_bcast":
        set_st(c, "a_bcast_where")
        await q.edit_message_text("پیوی یا گپ؟")
        return
    if data == "a_zero":
        set_st(c, "a_zero_confirm")
        await q.edit_message_text("بنویس: تأیید صفر")
        return
    if data == "a_betlock":
        set_st(c, "a_bet_min")
        await q.edit_message_text("حداقل شرط:")
        return
    if data == "a_emoji":
        set_st(c, "a_emoji")
        await q.edit_message_text("ایموجی پرمیوم یا متنی:")
        return
    if data == "a_prem":
        set_st(c, "a_prem")
        await q.edit_message_text("پرمیوم بفرست")
        return
    if data == "a_self_cap":
        set_st(c, "a_self_cap")
        await q.edit_message_text("کپشن پنل:")
        return
    if data == "a_hourly":
        set_st(c, "a_hourly")
        await q.edit_message_text("مصرف ساعتی:")
        return
    if data == "a_lot":
        set_st(c, "a_lot_prize")
        await q.edit_message_text("جایزه هر برنده:")
        return
    if data == "a_lot_cancel":
        lot = d.get("lottery")
        if not lot or lot.get("status") != "open":
            await q.answer("قرعه فعالی نیست", show_alert=True)
            return
        lot["status"] = "cancelled"
        save(d)
        await q.edit_message_text("🚫 لغو شد.", reply_markup=admin_kb())
        return
    if data == "a_tpl":
        await q.edit_message_text(
            "قالب را انتخاب کن:\n\n"
            "<code>{mention}</code> تگ کاربر\n"
            "<code>{emoji}</code> ایموجی\n"
            "<code>{balance}</code> موجودی\n"
            "<code>{amount}</code> شرط\n"
            "<code>{creator}</code> میزبان\n"
            "<code>{mode}</code> حالت\n"
            "<code>{winner}</code> برنده\n"
            "<code>{loser}</code> بازنده\n"
            "<code>{win_amount}</code> مبلغ برد\n"
            "<code>{from}</code> فرستنده\n"
            "<code>{to}</code> گیرنده\n"
            "<code>{sent}</code> رسیده",
            parse_mode="HTML",
            reply_markup=tpl_kb(),
        )
        return
    if data.startswith("tpl:"):
        key = data.split(":")[1]
        set_st(c, "a_tpl_set", {"key": key})
        await q.edit_message_text("متن جدید <code>%s</code>:\n%s" % (key, d.get("templates", {}).get(key, "")), parse_mode="HTML")
        return
    if data == "a_admins":
        lines = ["👤 ادمین‌ها\n"]
        rows = []
        for a in d.get("admins", []):
            lines.append("• <code>%s</code>" % a)
            if int(a) != ADMIN_ID and is_main(user.id):
                rows.append([btn("🗑 %s" % a, "a_adel:%s" % a, "danger")])
        if is_main(user.id):
            rows.insert(0, [btn("➕ آیدی", "a_aadd", "success")])
        rows.append([btn("🔙", "a_home", "primary")])
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        return
    if data == "a_aadd":
        set_st(c, "a_aadd")
        await q.edit_message_text("آیدی ادمین:")
        return
    if data.startswith("a_adel:") and is_main(user.id):
        aid = int(data.split(":")[1])
        d["admins"] = [x for x in d.get("admins", []) if int(x) != aid]
        save(d)
        await q.edit_message_text("حذف شد.", reply_markup=admin_kb())
        return


def leaderboard_text(d, viewer_id=None):
    items = sorted(((int(u), int(v)) for u, v in (d.get("balances") or {}).items() if int(v) > 0), key=lambda x: -x[1])
    top = items[:5]
    if not top:
        return "لیدربرد خالی است."
    out = ["🏆 <b>۵ نفر برتر</b>", ""]
    for i, (uid, v) in enumerate(top, 1):
        name = (d.get("users") or {}).get(str(uid), {}).get("name") or str(uid)
        out.append("%s: %s  ➡️  %s" % (i, mention_id(uid, name), short_num(v)))
        if i < len(top):
            out.append("")
            out.append("────────────")
            out.append("")
    if viewer_id is not None:
        r = rank_in(d.get("balances") or {}, viewer_id)
        out.append("")
        out.append("رتبه شما")
        out.append(str(r if r else "—"))
    return "\n".join(out)


def wins_text(d, viewer_id=None):
    items = sorted(((int(u), int(v)) for u, v in (d.get("wins") or {}).items() if int(v) > 0), key=lambda x: -x[1])[:5]
    if not items:
        return "هنوز بردی ثبت نشده."
    out = ["🏆 <b>وینرها</b>", ""]
    for i, (uid, v) in enumerate(items, 1):
        name = (d.get("users") or {}).get(str(uid), {}).get("name") or str(uid)
        out.append("%s: %s  ➡️  %s برد" % (i, mention_id(uid, name), num(v)))
        if i < len(items):
            out.append("")
            out.append("────────────")
            out.append("")
    if viewer_id is not None:
        r = rank_in(d.get("wins") or {}, viewer_id)
        out.append("")
        out.append("رتبه شما")
        out.append(str(r if r else "—"))
    return "\n".join(out)


def losses_text(d, viewer_id=None):
    items = sorted(((int(u), int(v)) for u, v in (d.get("losses") or {}).items() if int(v) > 0), key=lambda x: -x[1])[:5]
    if not items:
        return "هنوز باختی ثبت نشده."
    out = ["💀 <b>لوزرها</b>", ""]
    for i, (uid, v) in enumerate(items, 1):
        name = (d.get("users") or {}).get(str(uid), {}).get("name") or str(uid)
        out.append("%s: %s  ➡️  %s باخت" % (i, mention_id(uid, name), num(v)))
        if i < len(items):
            out.append("")
            out.append("────────────")
            out.append("")
    if viewer_id is not None:
        r = rank_in(d.get("losses") or {}, viewer_id)
        out.append("")
        out.append("رتبه شما")
        out.append(str(r if r else "—"))
    return "\n".join(out)



def help_text():
    return (
        "📖 <b>راهنما</b>\n\n"
        "• <b>بازی 1k</b> — ۲ یا ۳ نفره\n"
        "• <b>دعوت 5k</b> + ریپلای — دوئل\n"
        "• <b>موجودی / انتقال / لیدربرد</b>\n"
        "• <b>وینرها / لوزرها / سلف</b>\n"
        "• <b>دونیت بات 1k</b> — بانک بات\n"
        "• <b>بانک بات</b>\n"
    )

async def on_text(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    user = u.effective_user
    d = load()
    touch_user(d, user)
    track_chat(d, u.effective_chat.id)
    gamt = apply_grant(d, user.id)
    save(d)
    await expire_games(d)
    await maybe_finish_lottery(c.bot, d)
    d = load()
    text = (u.message.text or "").strip()
    chat = u.effective_chat
    st = get_st(c)

    if u.message.reply_to_message and u.message.reply_to_message.from_user and u.message.reply_to_message.from_user.is_bot:
        if re.fullmatch(r"/?(موجودی|bal)", text, re.I):
            await u.message.reply_text("من خودم الماسم 😎 میخوای چیو ببینی؟")
            return

    # ادمین: ریپلای + ایدی
    if is_admin(user.id) and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        if text.strip() in ("ایدی", "آیدی", "id", "ID") and u.message.reply_to_message and u.message.reply_to_message.from_user:
            tu = u.message.reply_to_message.from_user
            touch_user(d, tu)
            save(d)
            un = ("@" + tu.username) if tu.username else "—"
            msg = "اطلاعات کاربر\nاسم: %s\nیوزرنیم: %s\nآیدی: <code>%s</code>" % (tu.full_name, un, tu.id)
            for aid in d.get("admins", [ADMIN_ID]):
                try:
                    await c.bot.send_message(int(aid), msg, parse_mode="HTML")
                except Exception:
                    pass
            await u.message.reply_text("به پیوی ادمین ارسال شد.")
            return


    if gamt and chat.type == ChatType.PRIVATE:
        await u.message.reply_text("🎁 همگانی: +%s %s" % (num(gamt), em(d)), parse_mode="HTML")

    # شرط تماشاچی مبلغ
    if chat.type == ChatType.PRIVATE and st and st.get("kind") == "sidebet_amt":
        amt = parse_amount(text)
        if amt is None or amt < 1:
            await u.message.reply_text("مبلغ نامعتبر")
            return
        if bal(d, user.id) < amt:
            await u.message.reply_text("موجودی کم")
            return
        gid = st["extra"]["gid"]
        onp = int(st["extra"]["on"])
        game = d.get("games", {}).get(gid)
        if not game or game.get("status") != "open":
            clear_st(c)
            await u.message.reply_text("بازی دیگر باز نیست")
            return
        add_bal(d, user.id, -amt)
        game.setdefault("side_bets", []).append({"uid": user.id, "on": onp, "amount": amt})
        save(d)
        clear_st(c)
        await u.message.reply_text("✅ شرط %s روی %s ثبت شد" % (num(amt), mention_id(onp)), parse_mode="HTML")
        return

    if is_main(user.id) and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        m = re.match(r"^کسر\s+(.+)$", text)
        if m and u.message.reply_to_message and u.message.reply_to_message.from_user:
            amt = parse_amount(m.group(1))
            if amt and amt > 0:
                tuser = u.message.reply_to_message.from_user
                touch_user(d, tuser)
                add_bal(d, tuser.id, -amt)
                save(d)
                await u.message.reply_text("کسر از %s: %s" % (mention_user(tuser), num(bal(d, tuser.id))), parse_mode="HTML")
                return

    if chat.type == ChatType.PRIVATE and st and is_admin(user.id):
        kind = st.get("kind")
        extra = st.get("extra") or {}
        if kind == "a_add_id" and text.lstrip("-").isdigit():
            set_st(c, "a_add_amt", {"uid": int(text)})
            await u.message.reply_text("مقدار:")
            return
        if kind == "a_add_amt":
            amt = parse_amount(text)
            if amt is None:
                await u.message.reply_text("نامعتبر")
                return
            uid = int(extra["uid"])
            add_bal(d, uid, amt)
            save(d)
            clear_st(c)
            await u.message.reply_text("✅ %s → %s" % (num(amt), uid), reply_markup=admin_kb())
            await notify_deposit(c.bot, uid, amt, d, False)
            return
        if kind == "a_sub_id" and text.lstrip("-").isdigit():
            set_st(c, "a_sub_amt", {"uid": int(text)})
            await u.message.reply_text("مقدار:")
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
            await u.message.reply_text("✅ %s" % num(bal(d, uid)), reply_markup=admin_kb())
            return
        if kind == "a_add_all":
            amt = parse_amount(text)
            if amt is None:
                await u.message.reply_text("نامعتبر")
                return
            set_st(c, "a_grant_days", {"amount": amt})
            await u.message.reply_text(
                "همگانی %s برای کاربران فعلی واریز می‌شود.\n"
                "چند روز کاربران جدید هم بگیرند؟ (مثال 7)\n0 = فقط فعلی‌ها" % num(amt)
            )
            return
        if kind == "a_grant_days" and text.isdigit():
            amt = int(extra["amount"])
            days = int(text)
            ids = all_user_ids(d)
            for uid in ids:
                add_bal(d, uid, amt)
            if days > 0:
                d["grant"] = {
                    "amount": amt,
                    "until_ts": time.time() + days * 86400,
                    "claimed": [int(x) for x in ids],
                }
            else:
                d["grant"] = None
            save(d)
            clear_st(c)
            msg = "✅ %s به %s نفر" % (num(amt), num(len(ids)))
            if days > 0:
                msg += "\nکاربران جدید تا %s روز هم می‌گیرند." % days
            await u.message.reply_text(msg, reply_markup=admin_kb())
            return
        if kind == "a_bcast_where":
            if text.strip() not in ("پیوی", "گپ"):
                await u.message.reply_text("پیوی یا گپ")
                return
            set_st(c, "a_bcast_msg", {"where": text.strip()})
            await u.message.reply_text("متن:")
            return
        if kind == "a_bcast_msg":
            ok = fail = 0
            if extra.get("where") == "پیوی":
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
            await u.message.reply_text("✅%s ❌%s" % (ok, fail), reply_markup=admin_kb())
            return
        if kind == "a_zero_confirm":
            if text.strip() != "تأیید صفر":
                await u.message.reply_text("تأیید صفر")
                return
            for uid in list(d.get("balances", {}).keys()):
                d["balances"][uid] = 0
            save(d)
            clear_st(c)
            await u.message.reply_text("صفر شد.", reply_markup=admin_kb())
            return
        if kind == "a_bet_min":
            amt = parse_amount(text)
            if not amt:
                await u.message.reply_text("نامعتبر")
                return
            set_st(c, "a_bet_max", {"min": amt})
            await u.message.reply_text("حداکثر (0=آزاد):")
            return
        if kind == "a_bet_max":
            amt = parse_amount(text)
            if amt is None:
                await u.message.reply_text("نامعتبر")
                return
            d["min_bet"], d["max_bet"] = int(extra["min"]), int(amt)
            save(d)
            clear_st(c)
            await u.message.reply_text("قفل شد.", reply_markup=admin_kb())
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
                d["emoji"] = text[:8] if text else "💎"
                d["premium_emoji_id"] = None
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
            await u.message.reply_text("OK %s/10" % len(d["premium_pool"]), reply_markup=admin_kb())
            return
        if kind == "a_self_cap":
            d["self_caption"] = text
            save(d)
            clear_st(c)
            await u.message.reply_text("ذخیره.", reply_markup=admin_kb())
            return
        if kind == "a_hourly" and text.isdigit():
            d["hourly_use"] = max(1, int(text))
            save(d)
            clear_st(c)
            await u.message.reply_text("OK", reply_markup=admin_kb())
            return
        if kind == "a_lot_prize":
            amt = parse_amount(text)
            if not amt:
                await u.message.reply_text("نامعتبر")
                return
            set_st(c, "a_lot_win", {"prize": amt})
            await u.message.reply_text("تعداد برنده:")
            return
        if kind == "a_lot_win" and text.isdigit():
            set_st(c, "a_lot_end", {"prize": extra["prize"], "winners": int(text)})
            await u.message.reply_text("زمان پایان (دقیقه):")
            return
        if kind == "a_lot_end" and text.isdigit():
            mins = int(text)
            end_ts = time.time() + mins * 60
            ids = [int(x) for x in all_user_ids(d)]
            d["lottery"] = {
                "prize": int(extra["prize"]),
                "winners": int(extra["winners"]),
                "joined": ids,
                "status": "open",
                "end_ts": end_ts,
            }
            save(d)
            clear_st(c)
            end_local = datetime.fromtimestamp(end_ts, TEHRAN).strftime("%Y-%m-%d %H:%M")
            msg = "🎰 قرعه‌کشی\nجایزه: %s\nبرنده: %s\nشرکت‌کننده: %s\nپایان: %s" % (
                num(extra["prize"]), num(extra["winners"]), num(len(ids)), end_local)
            for cid in d.get("known_chats") or []:
                try:
                    await c.bot.send_message(int(cid), msg)
                except Exception:
                    pass
            await u.message.reply_text(msg, reply_markup=admin_kb())
            return
        if kind == "a_tpl_set":
            d.setdefault("templates", {})[extra["key"]] = text
            save(d)
            clear_st(c)
            await u.message.reply_text("ذخیره.", reply_markup=admin_kb())
            return
        if kind == "a_aadd" and text.lstrip("-").isdigit():
            aid = int(text)
            if aid not in [int(x) for x in d.get("admins", [])]:
                d.setdefault("admins", []).append(aid)
                save(d)
            clear_st(c)
            await u.message.reply_text("✅", reply_markup=admin_kb())
            return

    low2 = re.sub(r"^@\w+\s+", "", text)
    low2 = re.sub(r"^/(\w+)@\w+", r"/\1", low2)

    if re.fullmatch(r"/?(راهنما|help)", low2, re.I):
        await u.message.reply_text(help_text(), parse_mode="HTML")
        return

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
            reply_markup=InlineKeyboardMarkup([[btn("%s %s" % (em_plain(d), num(bal(d, tid))), "noop", "primary")]]),
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

    if re.fullmatch(r"/?(بازیکنان|players)", low2, re.I):
        if chat.type != ChatType.PRIVATE or not is_admin(user.id):
            return
        ids = sorted(all_user_ids(d), key=lambda x: -bal(d, x))
        lines = ["👥 %s نفر\n" % num(len(ids))]
        for uid in ids[:60]:
            info = user_info(d, uid)
            lines.append("• %s\n  <code>%s</code> | %s" % (info["name"], uid, num(bal(d, uid))))
        await u.message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    # دوئل خصوصی: دعوت 5k + reply
    m = re.match(r"^(?:/)?(?:دعوت|duel)\s+(.+)$", low2, re.I)
    if m:
        amount = parse_amount(m.group(1))
        if not amount or amount < 1:
            await u.message.reply_text("مثال: دعوت 5k (ریپلای روی فرد)")
            return
        if not u.message.reply_to_message or not u.message.reply_to_message.from_user:
            await u.message.reply_text("روی پیام حریف ریپلای کن: دعوت 5k")
            return
        foe = u.message.reply_to_message.from_user
        if foe.id == user.id or foe.is_bot:
            await u.message.reply_text("نامعتبر")
            return
        min_b, max_b = int(d.get("min_bet") or 1), int(d.get("max_bet") or 0)
        if amount < min_b or (max_b and amount > max_b):
            await u.message.reply_text("خارج از قفل شرط")
            return
        if bal(d, user.id) < amount:
            await u.message.reply_text("موجودی کم")
            return
        add_bal(d, user.id, -amount)
        gid = "d%d%d" % (int(time.time()), random.randint(10, 99))
        game = {
            "creator": user.id,
            "amount": amount,
            "need": 2,
            "players": [user.id],
            "names": {str(user.id): user.full_name},
            "status": "open",
            "chat_id": chat.id,
            "ts": time.time(),
            "invite": foe.id,
            "side_bets": [],
        }
        d.setdefault("games", {})[gid] = game
        track_chat(d, chat.id)
        save(d)
        kb = InlineKeyboardMarkup([
            [btn("✅ قبول دوئل", "join:%s" % gid, "success")],
            [btn("🚫 لغو", "cancel:%s" % gid, "danger")],
        ])
        await u.message.reply_text(
            "🔒 دوئل خصوصی\n%s vs %s\nشرط: %s %s\nفقط طرف مقابل می‌تواند بپیوندد."
            % (mention_user(user), mention_user(foe), em(d), num(amount)),
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    m = re.match(r"^(?:/)?(?:بازی|game)\s+(.+)$", low2, re.I)
    if m:
        amount = parse_amount(m.group(1))
        if not amount or amount < 1:
            await u.message.reply_text("مثال: بازی 1k")
            return
        min_b, max_b = int(d.get("min_bet") or 1), int(d.get("max_bet") or 0)
        if amount < min_b:
            await u.message.reply_text("حداقل: %s" % num(min_b))
            return
        if max_b and amount > max_b:
            await u.message.reply_text("حداکثر: %s" % num(max_b))
            return
        if bal(d, user.id) < amount:
            await u.message.reply_text("موجودی: %s" % num(bal(d, user.id)))
            return
        kb = InlineKeyboardMarkup([
            [btn("۲ نفره", "mode:2:%s:%s" % (amount, user.id), "primary")],
            [btn("۳ نفره", "mode:3:%s:%s" % (amount, user.id), "success")],
        ])
        await u.message.reply_text(
            "بازی %s %s\nحالت را انتخاب کن (فقط خودت):" % (em(d), num(amount)),
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    m = re.match(r"^(?:/)?(?:انتقال|transfer)\s+(.+)$", low2, re.I)
    if m:
        amount = parse_amount(m.group(1))
        if not amount or amount < 1:
            await u.message.reply_text("نامعتبر")
            return
        if not u.message.reply_to_message or not u.message.reply_to_message.from_user:
            await u.message.reply_text("ریپلای + انتقال 1k")
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
            "تأیید؟\n%s → %s\nکسر: %s\nدریافتی: %s" % (mention_user(user), mention_user(to), num(amount), num(send_amt)),
            parse_mode="HTML",
            reply_markup=kb,
        )
        return


async def on_startup(app):
    d = load()
    try:
        await app.bot.send_message(
            ADMIN_ID,
            "🤖 روشن شد\nکاربران: %s\nگپ‌ها: %s\nدستور: بازیکنان" % (num(len(all_user_ids(d))), num(len(d.get("known_chats") or []))),
        )
    except Exception:
        pass


def main():
    app = Application.builder().token(BOT_TOKEN).post_init(on_startup).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("panel", cmd_admin))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.TEXT, on_text))
    log.info("up")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
