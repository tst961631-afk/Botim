# -*- coding: utf-8 -*-
"""
ربات: پست روزانه کانال + پیام «ناشناس» به کیان
- زمان تهران، تاریخ شمسی و میلادی، ثانیه دقیق
- جبران ارسال از‌دست‌رفته هنگام روشن شدن
- کاربر فکر می‌کند ناشناس است؛ برای ادمین فوروارد واقعی می‌آید
"""
from __future__ import annotations
import json, os, logging, re
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ChatMemberHandler, ContextTypes, filters,
)
from telegram.constants import ChatType, ChatMemberStatus

BOT_TOKEN = "8975007734:AAEkghW4tK0DeG9uOKw87Lgep8XUWlMiiLY"
ADMIN_ID = 7530457395
DATA = "kian_bot_data.json"
TZ = ZoneInfo("Asia/Tehran")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("kianbot")


def D():
    return {
        "channel_id": None,
        "channel_title": "",
        "post_text": "سلام! پست روزانه.",
        "post_hour": 22,
        "post_minute": 0,
        "post_enabled": False,
        "last_sent_day": "",  # YYYY-MM-DD Tehran
        # map: admin_msg_id -> user_id (for reply bridge)
        "bridges": {},
        # user_id -> last admin reply text (optional inbox)
        "user_replies": {},
    }


def load():
    if os.path.exists(DATA):
        try:
            with open(DATA, "r", encoding="utf-8") as f:
                d = json.load(f)
            base = D()
            for k, v in base.items():
                d.setdefault(k, v)
            return d
        except Exception as e:
            log.error(e)
    return D()


def save(d):
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def is_admin(uid):
    return int(uid) == ADMIN_ID


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
    c.user_data["st"] = {"kind": kind, "extra": extra or {}}


def get_st(c):
    return c.user_data.get("st")


def clear_st(c):
    c.user_data.pop("st", None)


# ---------- Jalali ----------
def gregorian_to_jalali(gy, gm, gd):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621
    gy2 = gy + 1 if gm > 2 else gy
    days = (365 * gy) + (gy2 // 4) - (gy2 // 100) + (gy2 // 400) - 80 + gd + g_d_m[gm - 1]
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + days // 31
        jd = 1 + days % 31
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + (days - 186) % 30
    return jy, jm, jd


def format_footer(now: datetime) -> str:
    jy, jm, jd = gregorian_to_jalali(now.year, now.month, now.day)
    return (
        f"\n\n⏱ ارسال: {now.strftime('%H:%M:%S')}\n"
        f"📅 شمسی: {jy:04d}/{jm:02d}/{jd:02d}\n"
        f"📅 میلادی: {now.strftime('%Y-%m-%d')}"
    )


# ---------- channel post ----------
async def send_daily_post(bot, d, force=False):
    if not d.get("channel_id"):
        return False, "کانال ثبت نشده"
    if not d.get("post_enabled") and not force:
        return False, "پست خاموش است"
    now = datetime.now(TZ)
    day_key = now.strftime("%Y-%m-%d")
    if not force and d.get("last_sent_day") == day_key:
        return False, "امروز قبلاً ارسال شده"

    text = (d.get("post_text") or "").strip() or "—"
    body = text + format_footer(now)
    try:
        await bot.send_message(int(d["channel_id"]), body)
        d["last_sent_day"] = day_key
        save(d)
        return True, "ok"
    except Exception as e:
        log.error("post: %s", e)
        return False, str(e)


async def job_daily(context: ContextTypes.DEFAULT_TYPE):
    d = load()
    ok, msg = await send_daily_post(context.bot, d)
    if ok:
        log.info("daily post sent")
    else:
        log.info("daily skip: %s", msg)


async def catchup_on_start(app: Application):
    """اگر امروز هنوز پست نرفته و از ساعت مقرر گذشته، الان بفرست"""
    d = load()
    if not d.get("post_enabled") or not d.get("channel_id"):
        return
    now = datetime.now(TZ)
    day_key = now.strftime("%Y-%m-%d")
    if d.get("last_sent_day") == day_key:
        return
    target = now.replace(
        hour=int(d.get("post_hour", 22)),
        minute=int(d.get("post_minute", 0)),
        second=0,
        microsecond=0,
    )
    if now >= target:
        ok, msg = await send_daily_post(app.bot, d)
        log.info("catchup: %s %s", ok, msg)
        try:
            await app.bot.send_message(
                ADMIN_ID,
                f"⏱ جبران پست امروز: {'✅ ارسال شد' if ok else '❌ ' + msg}",
            )
        except Exception:
            pass


def reschedule(app: Application, d: dict):
    if not app.job_queue:
        log.warning("job-queue missing")
        return
    for j in app.job_queue.get_jobs_by_name("daily_post"):
        j.schedule_removal()
    h = int(d.get("post_hour", 22))
    m = int(d.get("post_minute", 0))
    app.job_queue.run_daily(
        job_daily,
        time=dtime(hour=h, minute=m, second=0, tzinfo=TZ),
        name="daily_post",
    )
    log.info("scheduled daily at %02d:%02d Tehran", h, m)


# ---------- keyboards ----------
def admin_kb(d=None):
    d = d or load()
    ch = d.get("channel_title") or d.get("channel_id") or "-"
    return InlineKeyboardMarkup([
        [btn(f"📢 کانال: {str(ch)[:20]}", "a_ch", "primary")],
        [btn("📝 متن پست", "a_text", "primary")],
        [btn(f"⏰ ساعت: {d.get('post_hour', 22):02d}:{d.get('post_minute', 0):02d}", "a_time", "primary")],
        [
            btn("🟢 روشن", "a_on", "success") if not d.get("post_enabled") else btn("🔴 خاموش", "a_off", "danger"),
        ],
        [btn("📤 ارسال الان", "a_now", "success")],
        [btn("📬 پیام‌های ناشناس", "a_inbox", "primary")],
        [btn("❌ بستن", "a_close", "danger")],
    ])


def user_start_kb():
    return InlineKeyboardMarkup([
        [btn("✉️ ارسال پیام ناشناس به کیانی", "u_anon", "success")],
    ])


def user_after_send_kb():
    return InlineKeyboardMarkup([
        [btn("✉️ پیام جدید", "u_anon", "success")],
        [btn("📥 جواب‌های کیان", "u_inbox", "primary")],
    ])


def user_got_reply_kb():
    return InlineKeyboardMarkup([
        [btn("👁 دیدن جواب", "u_inbox", "primary")],
        [btn("✉️ پیام جدید به کیان", "u_anon", "success")],
    ])


# ---------- handlers ----------
async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    clear_st(c)
    if is_admin(u.effective_user.id) and u.effective_chat.type == ChatType.PRIVATE:
        await u.message.reply_text("🎛 پنل ادمین", reply_markup=admin_kb())
        return
    if u.effective_chat.type == ChatType.PRIVATE:
        await u.message.reply_text(
            "سلام 👋\nمی‌تونی برای کیان پیام ناشناس بفرستی.",
            reply_markup=user_start_kb(),
        )


async def cmd_admin(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_chat.type != ChatType.PRIVATE or not is_admin(u.effective_user.id):
        return
    clear_st(c)
    await u.message.reply_text("🎛 پنل ادمین", reply_markup=admin_kb())


async def on_my_member(u: Update, c: ContextTypes.DEFAULT_TYPE):
    r = u.my_chat_member
    chat = r.chat
    if chat.type != ChatType.CHANNEL:
        return
    if r.new_chat_member.status != ChatMemberStatus.ADMINISTRATOR:
        return
    d = load()
    d["channel_id"] = chat.id
    d["channel_title"] = chat.title or str(chat.id)
    save(d)
    try:
        await c.bot.send_message(
            ADMIN_ID,
            f"✅ کانال ثبت شد: {chat.title}\n<code>{chat.id}</code>",
            parse_mode="HTML",
            reply_markup=admin_kb(d),
        )
    except Exception:
        pass


async def on_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    data = q.data or ""
    d = load()
    user = q.from_user
    await q.answer()

    # user callbacks
    if data == "u_anon":
        set_st(c, "anon")
        await q.edit_message_text(
            "پیام ناشناس خودت را برای کیان بفرست\n(متن، عکس، ویس، ...)",
            reply_markup=InlineKeyboardMarkup([[btn("انصراف", "u_cancel", "danger")]]),
        )
        return
    if data == "u_cancel":
        clear_st(c)
        await q.edit_message_text("لغو شد.", reply_markup=user_start_kb())
        return
    if data == "u_inbox":
        items = d.get("user_replies", {}).get(str(user.id), [])
        if not items:
            await q.edit_message_text(
                "هنوز جوابی نیست.",
                reply_markup=user_after_send_kb(),
            )
            return
        # show last 5
        lines = ["📥 جواب‌های کیان:\n"]
        for it in items[-5:]:
            lines.append(f"• {it.get('text', '')[:200]}")
            lines.append(f"  _{it.get('at', '')}_")
        await q.edit_message_text(
            "\n".join(lines),
            reply_markup=user_after_send_kb(),
        )
        return

    # admin only
    if not is_admin(user.id):
        return

    if data == "a_close":
        await q.edit_message_text("بسته شد.")
        return
    if data == "a_home":
        clear_st(c)
        await q.edit_message_text("🎛 پنل ادمین", reply_markup=admin_kb(d))
        return
    if data == "a_ch":
        await q.edit_message_text(
            f"کانال فعلی: {d.get('channel_title')}\n<code>{d.get('channel_id')}</code>\n\n"
            f"بات را ادمین کانال کن تا خودکار ثبت شود.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[btn("🔙", "a_home", "primary")]]),
        )
        return
    if data == "a_text":
        set_st(c, "set_text")
        await q.edit_message_text(
            f"متن فعلی:\n{d.get('post_text')}\n\nمتن جدید را بفرست:",
            reply_markup=InlineKeyboardMarkup([[btn("انصراف", "a_home", "danger")]]),
        )
        return
    if data == "a_time":
        set_st(c, "set_time")
        await q.edit_message_text(
            "ساعت را بفرست مثل 22:30 (وقت تهران):",
            reply_markup=InlineKeyboardMarkup([[btn("انصراف", "a_home", "danger")]]),
        )
        return
    if data == "a_on":
        d["post_enabled"] = True
        save(d)
        reschedule(c.application, d)
        await q.edit_message_text("🟢 پست روزانه روشن شد.", reply_markup=admin_kb(d))
        return
    if data == "a_off":
        d["post_enabled"] = False
        save(d)
        await q.edit_message_text("🔴 پست روزانه خاموش شد.", reply_markup=admin_kb(d))
        return
    if data == "a_now":
        ok, msg = await send_daily_post(c.bot, d, force=True)
        await q.edit_message_text(
            "✅ ارسال شد." if ok else f"❌ {msg}",
            reply_markup=admin_kb(load()),
        )
        return
    if data == "a_inbox":
        n = len(d.get("bridges", {}))
        await q.edit_message_text(
            f"📬 پیام‌های ناشناس\n"
            f"پل‌های فعال (برای ریپلای): حدودی در حافظه\n"
            f"پیام‌های جدید به‌صورت فوروارد برایت می‌آیند.\n"
            f"روی فوروارد ریپلای کن تا جواب به کاربر برود.",
            reply_markup=InlineKeyboardMarkup([[btn("🔙", "a_home", "primary")]]),
        )
        return


async def on_private(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or u.effective_chat.type != ChatType.PRIVATE:
        return
    user = u.effective_user
    d = load()
    text = (u.message.text or "").strip()

    # admin reply to forwarded anon message
    if is_admin(user.id) and u.message.reply_to_message:
        rp = u.message.reply_to_message
        # bridge by replied message id
        b = d.get("bridges", {}).get(str(rp.message_id))
        # also try forward origin
        target_uid = None
        if b:
            target_uid = int(b["user_id"])
        elif rp.forward_from:
            target_uid = rp.forward_from.id
        elif getattr(rp, "forward_origin", None):
            fo = rp.forward_origin
            if hasattr(fo, "sender_user") and fo.sender_user:
                target_uid = fo.sender_user.id

        if target_uid:
            reply_text = u.message.text or u.message.caption or ""
            try:
                # deliver to user
                if u.message.text:
                    await c.bot.send_message(
                        target_uid,
                        "💬 کیان جوابت داد:\n\n" + u.message.text,
                        reply_markup=user_got_reply_kb(),
                    )
                else:
                    await c.bot.send_message(
                        target_uid,
                        "💬 کیان جوابت داد:",
                        reply_markup=user_got_reply_kb(),
                    )
                    await c.bot.copy_message(target_uid, u.message.chat_id, u.message.message_id)
                # store in user inbox
                key = str(target_uid)
                d.setdefault("user_replies", {}).setdefault(key, []).append({
                    "text": reply_text or "[رسانه]",
                    "at": datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),
                })
                d["user_replies"][key] = d["user_replies"][key][-20:]
                save(d)
                await u.message.reply_text("✅ جواب برای کاربر ارسال شد.")
            except Exception as e:
                await u.message.reply_text(f"خطا: {e}")
            return

    # admin states
    if is_admin(user.id):
        st = get_st(c)
        if st and st.get("kind") == "set_text":
            if not text:
                await u.message.reply_text("متن بفرست")
                return
            d["post_text"] = text
            save(d)
            clear_st(c)
            await u.message.reply_text("✅ متن پست ذخیره شد.", reply_markup=admin_kb(d))
            return
        if st and st.get("kind") == "set_time":
            m = re.match(r"^(\d{1,2}):(\d{2})$", text)
            if not m:
                await u.message.reply_text("فرمت: 22:30")
                return
            h, mi = int(m.group(1)), int(m.group(2))
            if not (0 <= h <= 23 and 0 <= mi <= 59):
                await u.message.reply_text("ساعت نامعتبر")
                return
            d["post_hour"], d["post_minute"] = h, mi
            save(d)
            clear_st(c)
            reschedule(c.application, d)
            await u.message.reply_text(f"✅ ساعت {h:02d}:{mi:02d} تهران", reply_markup=admin_kb(d))
            return
        if text in ("پنل", "/admin", "/panel"):
            clear_st(c)
            await u.message.reply_text("🎛 پنل", reply_markup=admin_kb(d))
            return

    # user anon flow
    st = get_st(c)
    if st and st.get("kind") == "anon":
        clear_st(c)
        # notify admin with REAL forward (admin sees identity)
        try:
            header = await c.bot.send_message(
                ADMIN_ID,
                f"📬 پیام «ناشناس» جدید\nاز: {user.full_name}\nآیدی: <code>{user.id}</code>\n"
                f"@{user.username or '—'}\nریپلای روی پیام بعدی = جواب",
                parse_mode="HTML",
            )
        except Exception:
            header = None
        try:
            # forward so admin sees real user
            sent = await c.bot.forward_message(
                chat_id=ADMIN_ID,
                from_chat_id=u.message.chat_id,
                message_id=u.message.message_id,
            )
            d.setdefault("bridges", {})[str(sent.message_id)] = {
                "user_id": user.id,
                "ts": datetime.now(TZ).isoformat(),
            }
            # cleanup old bridges
            if len(d["bridges"]) > 500:
                keys = list(d["bridges"].keys())[:-400]
                for k in keys:
                    d["bridges"].pop(k, None)
            save(d)
        except Exception as e:
            await u.message.reply_text(f"ارسال نشد: {e}")
            return

        await u.message.reply_text(
            "✅ پیام ناشناس به کیان ارسال شد.",
            reply_markup=user_after_send_kb(),
        )
        return


async def post_init(app: Application):
    d = load()
    reschedule(app, d)
    await catchup_on_start(app)


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("panel", cmd_admin))
    app.add_handler(ChatMemberHandler(on_my_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, on_private))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.COMMAND, on_private))
    log.info("kian bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
