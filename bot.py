# -*- coding: utf-8 -*-
"""
بات کانال — پست روزانه + شمارنده روز + ایموجی پرمیوم
- وقتی بات ادمین کانال شود → پیوی ادمین
- زمان‌بندی روزانه به وقت ایران
- آپلود ایموجی پرمیوم فقط پیوی، فقط یوزرنیم‌های مجاز
- ذخیره custom_emoji_id و استفاده در پست
"""
import json
import logging
import os
import re
import string
import random
from datetime import time as dtime
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MessageEntity,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ChatMemberHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ChatMemberStatus, ChatType, MessageEntityType

BOT_TOKEN = "8975007734:AAFGsTyR56CLHJnr7ZFgz8DMAs2INlg1Qfc"
ADMIN_ID = 7530457395
DATA_FILE = "channel_bot_data.json"
TZ = ZoneInfo("Asia/Tehran")

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("channelbot")


def D():
    return {
        "allowed_uploaders": [],  # usernames lower without @
        "emojis": {},  # code -> {id, code}
        "channels": {},  # str(chat_id) -> settings
    }


def load():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            base = D()
            for k, v in base.items():
                d.setdefault(k, v)
            return d
        except Exception as e:
            log.error(e)
    return D()


def save(d):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def is_admin(uid):
    return uid == ADMIN_ID


def code_gen(n=3):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def btn(text, data, style=None):
    kw = {"text": text, "callback_data": data}
    if style in ("danger", "success", "primary"):
        kw["style"] = style
    try:
        return InlineKeyboardButton(**kw)
    except TypeError:
        kw.pop("style", None)
        return InlineKeyboardButton(**kw)


def main_kb():
    return InlineKeyboardMarkup([
        [btn("📢 کانال‌ها", "ch_list", "primary")],
        [btn("😀 ایموجی‌های پرمیوم", "em_list", "primary")],
        [btn("👤 افراد مجاز آپلود", "up_list", "primary")],
        [btn("❌ بستن", "close", "danger")],
    ])


def set_st(c, kind, extra=None):
    c.user_data["st"] = {"kind": kind, "extra": extra or {}}


def get_st(c):
    return c.user_data.get("st")


def clear_st(c):
    c.user_data.pop("st", None)


# ---------- extract premium emoji id ----------
def extract_custom_emoji_id(message) -> str | None:
    """از پیام کاربر custom_emoji_id را بردار"""
    if not message:
        return None
    # entities روی متن
    for ent in message.entities or []:
        if getattr(ent, "type", None) in (MessageEntityType.CUSTOM_EMOJI, "custom_emoji"):
            eid = getattr(ent, "custom_emoji_id", None)
            if eid:
                return str(eid)
    # caption entities
    for ent in message.caption_entities or []:
        if getattr(ent, "type", None) in (MessageEntityType.CUSTOM_EMOJI, "custom_emoji"):
            eid = getattr(ent, "custom_emoji_id", None)
            if eid:
                return str(eid)
    # بعضی کلاینت‌ها استیکر custom می‌فرستند
    if message.sticker and getattr(message.sticker, "custom_emoji_id", None):
        return str(message.sticker.custom_emoji_id)
    return None


def build_post_text_and_entities(template: str, day: int, emoji_id: str | None):
    """
    {day} و {emoji} را جایگزین می‌کند.
    برای ایموجی پرمیوم یک کاراکتر جای‌نگهدار می‌گذارد و entity می‌سازد.
    """
    text = template.replace("{day}", str(day))
    entities = []
    if "{emoji}" in text and emoji_id:
        # placeholder یک کاراکتر (از BMP) — تلگرام با entity به custom emoji تبدیلش می‌کند
        placeholder = "🟢"
        before, after = text.split("{emoji}", 1)
        offset = len(before.encode("utf-16-le")) // 2  # Telegram uses UTF-16 code units
        text = before + placeholder + after
        length = len(placeholder.encode("utf-16-le")) // 2
        entities.append(
            MessageEntity(
                type=MessageEntityType.CUSTOM_EMOJI,
                offset=offset,
                length=length,
                custom_emoji_id=str(emoji_id),
            )
        )
    else:
        text = text.replace("{emoji}", "")
    return text, entities


# ---------- jobs ----------
async def daily_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data.get("chat_id")
    if chat_id is None:
        return
    d = load()
    ch = d.get("channels", {}).get(str(chat_id))
    if not ch or not ch.get("enabled"):
        return
    day = int(ch.get("day", 1))
    template = ch.get("template") or "{day} روز بدون تو"
    emoji_code = ch.get("emoji_code") or ""
    emoji_id = None
    if emoji_code and emoji_code in d.get("emojis", {}):
        emoji_id = d["emojis"][emoji_code].get("id")

    text, entities = build_post_text_and_entities(template, day, emoji_id)
    try:
        await context.bot.send_message(
            chat_id=int(chat_id),
            text=text,
            entities=entities or None,
        )
    except Exception as e:
        log.error("post %s: %s", chat_id, e)
        try:
            await context.bot.send_message(
                ADMIN_ID,
                f"خطا در ارسال پست کانال {chat_id}:\n{e}",
            )
        except Exception:
            pass
        return

    ch["day"] = day + 1
    save(d)


def schedule_channel(app: Application, chat_id: int, hour: int, minute: int):
    name = f"daily_{chat_id}"
    # remove old
    jobs = app.job_queue.get_jobs_by_name(name) if app.job_queue else []
    for j in jobs:
        j.schedule_removal()
    if not app.job_queue:
        log.error("JobQueue not available — install python-telegram-bot[job-queue]")
        return
    app.job_queue.run_daily(
        daily_job,
        time=dtime(hour=hour, minute=minute, tzinfo=TZ),
        data={"chat_id": chat_id},
        name=name,
    )
    log.info("scheduled %s at %02d:%02d Tehran", chat_id, hour, minute)


def cancel_channel_job(app: Application, chat_id: int):
    name = f"daily_{chat_id}"
    if not app.job_queue:
        return
    for j in app.job_queue.get_jobs_by_name(name):
        j.schedule_removal()


async def restore_jobs(app: Application):
    d = load()
    for cid, ch in d.get("channels", {}).items():
        if ch.get("enabled"):
            schedule_channel(app, int(cid), int(ch.get("hour", 22)), int(ch.get("minute", 0)))


# ---------- handlers ----------
async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if is_admin(u.effective_user.id):
        await u.message.reply_text(
            "پنل مدیریت کانال\n/panel یا «پنل»",
            reply_markup=main_kb(),
        )
    else:
        await u.message.reply_text(
            "اگر مجاز به آپلود ایموجی هستی، یک ایموجی پرمیوم تکی در همین پیوی بفرست."
        )


async def cmd_panel(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_admin(u.effective_user.id):
        return
    clear_st(c)
    await u.message.reply_text("🎛 پنل ادمین", reply_markup=main_kb())


async def on_my_chat_member(u: Update, c: ContextTypes.DEFAULT_TYPE):
    r = u.my_chat_member
    chat = r.chat
    if chat.type != ChatType.CHANNEL:
        return
    new = r.new_chat_member
    if new.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER):
        return
    # فقط وقتی ادمین شد
    if new.status != ChatMemberStatus.ADMINISTRATOR:
        return
    d = load()
    cid = str(chat.id)
    if cid not in d["channels"]:
        d["channels"][cid] = {
            "title": chat.title or cid,
            "enabled": False,
            "hour": 22,
            "minute": 0,
            "day": 1,
            "template": "{day} روز بدون تو\n{emoji}",
            "emoji_code": "",
        }
        save(d)
    else:
        d["channels"][cid]["title"] = chat.title or d["channels"][cid].get("title")
        save(d)
    try:
        await c.bot.send_message(
            ADMIN_ID,
            f"✅ کانال تنظیم شد\n"
            f"عنوان: {chat.title}\n"
            f"آیدی: <code>{chat.id}</code>\n\n"
            f"از پنل → کانال‌ها تنظیم کن.",
            parse_mode="HTML",
            reply_markup=main_kb(),
        )
    except Exception as e:
        log.error("notify admin: %s", e)


async def on_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.answer("فقط ادمین", show_alert=True)
        return
    d = load()
    cb = q.data

    if cb == "close":
        await q.edit_message_text("بسته شد.")
        return
    if cb == "home":
        clear_st(c)
        await q.edit_message_text("🎛 پنل ادمین", reply_markup=main_kb())
        return

    # --- uploaders ---
    if cb == "up_list":
        users = d.get("allowed_uploaders", [])
        lines = ["👤 افراد مجاز آپلود ایموجی:\n"]
        rows = []
        if not users:
            lines.append("خالی است.")
        for un in users:
            lines.append(f"• @{un}")
            rows.append([btn(f"🗑 @{un}", f"up_del_{un}", "danger")])
        rows.insert(0, [btn("➕ افزودن یوزرنیم", "up_add", "success")])
        rows.append([btn("🔙", "home", "primary")])
        await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))
        return

    if cb == "up_add":
        set_st(c, "up_add")
        await q.edit_message_text("یوزرنیم را بفرست (با یا بدون @):")
        return

    if cb.startswith("up_del_"):
        un = cb[len("up_del_"):]
        d["allowed_uploaders"] = [x for x in d.get("allowed_uploaders", []) if x != un]
        save(d)
        await q.answer("حذف شد")
        cb = "up_list"
        # reuse list
        users = d.get("allowed_uploaders", [])
        lines = ["👤 افراد مجاز:\n"] + ([f"• @{u}" for u in users] or ["خالی"])
        rows = [[btn("➕ افزودن", "up_add", "success")]]
        for un2 in users:
            rows.append([btn(f"🗑 @{un2}", f"up_del_{un2}", "danger")])
        rows.append([btn("🔙", "home", "primary")])
        await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))
        return

    # --- emojis ---
    if cb == "em_list":
        em = d.get("emojis", {})
        lines = ["😀 ایموجی‌های پرمیوم ثبت‌شده\n"]
        if not em:
            lines.append("هنوز چیزی نیست.")
        else:
            for code, info in em.items():
                lines.append(f"کد: <code>{code}</code>  |  id: <code>{info.get('id')}</code>")
        rows = [[btn("🔙", "home", "primary")]]
        # حذف تکی
        for code in list(em.keys())[:30]:
            rows.insert(-1, [btn(f"🗑 {code}", f"em_del_{code}", "danger")])
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        return

    if cb.startswith("em_del_"):
        code = cb[len("em_del_"):]
        d.get("emojis", {}).pop(code, None)
        save(d)
        await q.answer("حذف شد")
        await q.edit_message_text("حذف شد.", reply_markup=InlineKeyboardMarkup([[btn("لیست", "em_list", "primary"), btn("خانه", "home", "primary")]]))
        return

    # --- channels ---
    if cb == "ch_list":
        chs = d.get("channels", {})
        lines = ["📢 کانال‌ها\n"]
        rows = []
        if not chs:
            lines.append("کانالی ثبت نشده. بات را در کانال ادمین کن.")
        for cid, ch in chs.items():
            st = "🟢" if ch.get("enabled") else "⚪"
            lines.append(f"{st} {ch.get('title', cid)}\n<code>{cid}</code> — روز {ch.get('day', 1)}")
            rows.append([btn(ch.get("title", cid)[:30], f"ch_{cid}", "primary")])
        rows.append([btn("🔙", "home", "primary")])
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        return

    if cb.startswith("ch_") and not cb.startswith("ch_set") and not cb.startswith("ch_en") and not cb.startswith("ch_dis") and not cb.startswith("ch_tpl") and not cb.startswith("ch_time") and not cb.startswith("ch_day") and not cb.startswith("ch_em") and not cb.startswith("ch_cancel"):
        cid = cb[3:]
        ch = d.get("channels", {}).get(cid)
        if not ch:
            await q.answer("نیست", show_alert=True)
            return
        txt = (
            f"📢 <b>{ch.get('title')}</b>\n"
            f"آیدی: <code>{cid}</code>\n"
            f"وضعیت: {'فعال' if ch.get('enabled') else 'غیرفعال'}\n"
            f"ساعت: {ch.get('hour', 22):02d}:{ch.get('minute', 0):02d} (تهران)\n"
            f"روز فعلی: {ch.get('day', 1)}\n"
            f"کد ایموجی: <code>{ch.get('emoji_code') or '-'}</code>\n"
            f"قالب:\n<code>{ch.get('template')}</code>"
        )
        kb = InlineKeyboardMarkup([
            [btn("🟢 فعال‌سازی روزانه", f"ch_en_{cid}", "success")],
            [btn("⏹ لغو زمان‌بندی", f"ch_cancel_{cid}", "danger")],
            [btn("⏰ تنظیم ساعت", f"ch_time_{cid}", "primary")],
            [btn("📝 قالب متن", f"ch_tpl_{cid}", "primary")],
            [btn("😀 کد ایموجی", f"ch_em_{cid}", "primary")],
            [btn("🔢 عدد روز", f"ch_day_{cid}", "primary")],
            [btn("🔙", "ch_list", "primary")],
        ])
        await q.edit_message_text(txt, parse_mode="HTML", reply_markup=kb)
        return

    if cb.startswith("ch_en_"):
        cid = cb[len("ch_en_"):]
        ch = d["channels"].get(cid)
        if not ch:
            return
        ch["enabled"] = True
        save(d)
        schedule_channel(c.application, int(cid), int(ch.get("hour", 22)), int(ch.get("minute", 0)))
        await q.answer("فعال شد")
        await q.edit_message_text(
            f"✅ ارسال روزانه فعال شد\n{ch.get('hour', 22):02d}:{ch.get('minute', 0):02d} به وقت تهران",
            reply_markup=InlineKeyboardMarkup([[btn("کانال", f"ch_{cid}", "primary"), btn("خانه", "home", "primary")]]),
        )
        return

    if cb.startswith("ch_cancel_"):
        cid = cb[len("ch_cancel_"):]
        if cid in d.get("channels", {}):
            d["channels"][cid]["enabled"] = False
            save(d)
        cancel_channel_job(c.application, int(cid))
        await q.answer("لغو شد")
        await q.edit_message_text("⏹ زمان‌بندی لغو شد.", reply_markup=InlineKeyboardMarkup([[btn("بازگشت", f"ch_{cid}", "primary")]]))
        return

    if cb.startswith("ch_time_"):
        cid = cb[len("ch_time_"):]
        set_st(c, "ch_time", {"cid": cid})
        await q.edit_message_text("ساعت را بفرست مثل 22:00 یا 9:30 (وقت تهران):")
        return

    if cb.startswith("ch_tpl_"):
        cid = cb[len("ch_tpl_"):]
        set_st(c, "ch_tpl", {"cid": cid})
        await q.edit_message_text(
            "قالب متن را بفرست.\n"
            "متغیرها:\n"
            "<code>{day}</code> = عدد روز\n"
            "<code>{emoji}</code> = ایموجی پرمیوم\n\n"
            "مثال:\n"
            "<code>{day} روز بدون تو\n{emoji}</code>",
            parse_mode="HTML",
        )
        return

    if cb.startswith("ch_em_"):
        cid = cb[len("ch_em_"):]
        set_st(c, "ch_em", {"cid": cid})
        em = d.get("emojis", {})
        lines = ["کد ایموجی را بفرست (از لیست):\n"]
        for code in em:
            lines.append(f"• <code>{code}</code>")
        if not em:
            lines.append("لیست خالی است — اول ایموجی ثبت کنید.")
        await q.edit_message_text("\n".join(lines), parse_mode="HTML")
        return

    if cb.startswith("ch_day_"):
        cid = cb[len("ch_day_"):]
        set_st(c, "ch_day", {"cid": cid})
        await q.edit_message_text("عدد روز فعلی را بفرست (مثلاً 1):")
        return


async def on_private(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or u.effective_chat.type != ChatType.PRIVATE:
        return
    user = u.effective_user
    d = load()

    # admin text states
    if is_admin(user.id):
        if u.message.text and u.message.text.strip() in ("پنل", "/panel"):
            clear_st(c)
            await u.message.reply_text("🎛 پنل ادمین", reply_markup=main_kb())
            return
        st = get_st(c)
        if st and u.message.text:
            text = u.message.text.strip()
            kind = st.get("kind")
            extra = st.get("extra") or {}
            if kind == "up_add":
                un = text.lstrip("@").lower()
                if un and un not in d["allowed_uploaders"]:
                    d["allowed_uploaders"].append(un)
                    save(d)
                clear_st(c)
                await u.message.reply_text(f"✅ @{un} مجاز شد.", reply_markup=main_kb())
                return
            if kind == "ch_time":
                cid = extra.get("cid")
                m = re.match(r"^(\d{1,2}):(\d{2})$", text)
                if not m:
                    await u.message.reply_text("فرمت: 22:00")
                    return
                h, mi = int(m.group(1)), int(m.group(2))
                if not (0 <= h <= 23 and 0 <= mi <= 59):
                    await u.message.reply_text("ساعت نامعتبر")
                    return
                ch = d["channels"].get(cid)
                if ch:
                    ch["hour"], ch["minute"] = h, mi
                    save(d)
                    if ch.get("enabled"):
                        schedule_channel(c.application, int(cid), h, mi)
                clear_st(c)
                await u.message.reply_text(f"✅ ساعت {h:02d}:{mi:02d}", reply_markup=main_kb())
                return
            if kind == "ch_tpl":
                cid = extra.get("cid")
                if cid in d["channels"]:
                    d["channels"][cid]["template"] = text
                    save(d)
                clear_st(c)
                await u.message.reply_text("✅ قالب ذخیره شد.", reply_markup=main_kb())
                return
            if kind == "ch_em":
                cid = extra.get("cid")
                code = text.strip().lower()
                if code not in d.get("emojis", {}):
                    await u.message.reply_text("این کد در لیست نیست.")
                    return
                d["channels"][cid]["emoji_code"] = code
                save(d)
                clear_st(c)
                await u.message.reply_text(f"✅ ایموجی `{code}`", reply_markup=main_kb())
                return
            if kind == "ch_day":
                cid = extra.get("cid")
                try:
                    n = int(text)
                    if n < 1:
                        n = 1
                except ValueError:
                    await u.message.reply_text("عدد بفرست")
                    return
                if cid in d["channels"]:
                    d["channels"][cid]["day"] = n
                    save(d)
                clear_st(c)
                await u.message.reply_text(f"✅ روز = {n}", reply_markup=main_kb())
                return

    # premium emoji upload (allowed users or admin)
    uname = (user.username or "").lower()
    allowed = uname in set(d.get("allowed_uploaders", [])) or is_admin(user.id)
    if not allowed:
        return

    eid = extract_custom_emoji_id(u.message)
    if not eid:
        # اگر متن عادی بود و ادمین نبود نادیده
        return

    # ثبت ایموجی
    # کد یکتا
    existing = {info.get("id"): code for code, info in d.get("emojis", {}).items()}
    if eid in existing:
        await u.message.reply_text(f"قبلاً ثبت شده.\nکد: <code>{existing[eid]}</code>", parse_mode="HTML")
        return
    code = code_gen(3)
    while code in d.get("emojis", {}):
        code = code_gen(3)
    d.setdefault("emojis", {})[code] = {"id": eid, "code": code, "by": uname or str(user.id)}
    save(d)
    await u.message.reply_text(
        f"✅ ایموجی پرمیوم ثبت شد\n"
        f"کد: <code>{code}</code>\n"
        f"id: <code>{eid}</code>",
        parse_mode="HTML",
    )
    if not is_admin(user.id):
        try:
            await c.bot.send_message(
                ADMIN_ID,
                f"ایموجی جدید از @{uname}\nکد: <code>{code}</code>\nid: <code>{eid}</code>",
                parse_mode="HTML",
            )
        except Exception:
            pass


async def post_init(app: Application):
    await restore_jobs(app)


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("panel", cmd_panel))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE, on_private))
    log.info("channel bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
