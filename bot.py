# -*- coding: utf-8 -*-
"""ربات مدیریت کانال — پنل شیشه‌ای، زمان‌بندی، قفل عضویت، استارت سفارشی"""
from __future__ import annotations
import json, os, re, logging, asyncio
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ChatMemberHandler, ContextTypes, filters,
)
from telegram.constants import ChatType, ChatMemberStatus, ParseMode
from telegram.error import TelegramError

BOT_TOKEN = "8975007734:AAECUtykIq5YSt0Wc3YpFKtgOKSAs-muOoY"
ADMIN_ID = 7530457395
DATA = "channel_mgr.json"
TZ = ZoneInfo("Asia/Tehran")
STATE_TTL = 180

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("chmgr")


def D():
    return {
        "start_msg": "سلام 👋\nبه ربات مدیریت کانال خوش آمدید.",
        "channels": {},  # str(id) -> {title, username}
        "active_channel": None,
        "signature": "",
        "force_join": [],  # list of channel ids/usernames required
        "force_enabled": False,
        "queue": [],  # scheduled posts
        "admins": [ADMIN_ID],
    }


def load():
    if os.path.exists(DATA):
        try:
            with open(DATA, "r", encoding="utf-8") as f:
                d = json.load(f)
            base = D()
            for k, v in base.items():
                d.setdefault(k, v)
            if ADMIN_ID not in d.get("admins", []):
                d.setdefault("admins", []).append(ADMIN_ID)
            return d
        except Exception as e:
            log.error(e)
    return D()


def save(d):
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def is_admin(uid):
    d = load()
    return int(uid) in set(d.get("admins", [ADMIN_ID])) or int(uid) == ADMIN_ID


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
    c.user_data["st"] = {"kind": kind, "ts": datetime.now().timestamp(), "extra": extra or {}}


def get_st(c):
    st = c.user_data.get("st")
    if not st:
        return None
    if datetime.now().timestamp() - st.get("ts", 0) > STATE_TTL:
        c.user_data.pop("st", None)
        return None
    return st


def clear_st(c):
    c.user_data.pop("st", None)


def main_kb(d=None):
    d = d or load()
    ch = d.get("active_channel")
    ch_title = "-"
    if ch and ch in d.get("channels", {}):
        ch_title = d["channels"][ch].get("title") or ch
    return InlineKeyboardMarkup([
        [btn(f"📢 کانال فعال: {ch_title[:20]}", "ch_list", "primary")],
        [btn("✍️ ارسال پست", "post", "success"), btn("📅 زمان‌بندی", "sched", "primary")],
        [btn("📋 صف پست‌ها", "queue", "primary")],
        [btn("📌 پین", "pin", "primary"), btn("📍 آنپین", "unpin", "primary")],
        [btn("🗑 حذف پیام", "delete", "danger")],
        [btn("🚫 بن", "ban", "danger"), btn("✅ آنبن", "unban", "success")],
        [btn("⬆️ ادمین کردن", "promote", "success"), btn("⬇️ عزل ادمین", "demote", "danger")],
        [btn("🔐 قفل عضویت", "force", "primary")],
        [btn("💬 متن استارت", "startmsg", "primary"), btn("✒️ امضا", "sign", "primary")],
        [btn("📊 وضعیت", "status", "primary")],
        [btn("❌ بستن", "close", "danger")],
    ])


def cancel_kb():
    return InlineKeyboardMarkup([[btn("❌ انصراف", "cancel", "danger")]])


# ---------- force join ----------
async def check_force(bot, user_id):
    d = load()
    if not d.get("force_enabled"):
        return True, []
    missing = []
    for ch in d.get("force_join", []):
        try:
            cid = int(ch) if str(ch).lstrip("-").isdigit() else ch
            m = await bot.get_chat_member(cid, user_id)
            if m.status in ("left", "kicked"):
                missing.append(ch)
        except Exception:
            missing.append(ch)
    return (len(missing) == 0), missing


def force_kb(missing, d):
    rows = []
    for ch in missing:
        info = d.get("channels", {}).get(str(ch), {})
        un = info.get("username")
        title = info.get("title") or str(ch)
        if un:
            rows.append([btn(f"عضویت در {title}", url=f"https://t.me/{un}")])
        else:
            rows.append([btn(f"{title}", "noop", "primary")])
    rows.append([btn("✅ عضو شدم — بررسی", "force_check", "success")])
    return InlineKeyboardMarkup(rows)


# ---------- jobs ----------
async def process_queue(context: ContextTypes.DEFAULT_TYPE):
    d = load()
    now = datetime.now(TZ)
    left = []
    for item in d.get("queue", []):
        try:
            when = datetime.fromisoformat(item["when"]).replace(tzinfo=TZ)
        except Exception:
            continue
        if when <= now:
            try:
                await send_post(context.bot, item)
                log.info("scheduled sent %s", item.get("id"))
            except Exception as e:
                log.error("sched fail: %s", e)
                try:
                    await context.bot.send_message(ADMIN_ID, f"خطا در پست زمان‌بندی:\n{e}")
                except Exception:
                    pass
        else:
            left.append(item)
    d["queue"] = left
    save(d)


async def send_post(bot, item):
    d = load()
    chat_id = item.get("chat_id") or d.get("active_channel")
    if not chat_id:
        raise RuntimeError("کانال فعال نیست")
    chat_id = int(chat_id)
    text = item.get("text") or ""
    sig = d.get("signature") or ""
    if sig and text:
        text = text + "\n\n" + sig
    elif sig and not text:
        text = sig
    media = item.get("file_id")
    mtype = item.get("media_type")  # photo/video/document/None
    pin = item.get("pin", False)
    msg = None
    if mtype == "photo" and media:
        msg = await bot.send_photo(chat_id, media, caption=text or None)
    elif mtype == "video" and media:
        msg = await bot.send_video(chat_id, media, caption=text or None)
    elif mtype == "document" and media:
        msg = await bot.send_document(chat_id, media, caption=text or None)
    else:
        if not text:
            raise RuntimeError("متن خالی")
        msg = await bot.send_message(chat_id, text)
    if pin and msg:
        try:
            await bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
        except Exception:
            pass
    return msg


# ---------- handlers ----------
async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    d = load()
    user = u.effective_user
    if is_admin(user.id) and u.effective_chat.type == ChatType.PRIVATE:
        await u.message.reply_text("🎛 پنل مدیریت کانال", reply_markup=main_kb(d))
        return
    ok, missing = await check_force(c.bot, user.id)
    if not ok:
        await u.message.reply_text(
            "برای استفاده ابتدا در کانال‌های زیر عضو شوید:",
            reply_markup=force_kb(missing, d),
        )
        return
    await u.message.reply_text(d.get("start_msg") or "سلام")


async def cmd_admin(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_chat.type != ChatType.PRIVATE:
        return
    if not is_admin(u.effective_user.id):
        return
    clear_st(c)
    await u.message.reply_text("🎛 پنل مدیریت کانال", reply_markup=main_kb())


async def on_my_member(u: Update, c: ContextTypes.DEFAULT_TYPE):
    r = u.my_chat_member
    chat = r.chat
    if chat.type != ChatType.CHANNEL:
        return
    new = r.new_chat_member
    if new.status != ChatMemberStatus.ADMINISTRATOR:
        return
    d = load()
    cid = str(chat.id)
    d.setdefault("channels", {})[cid] = {
        "title": chat.title or cid,
        "username": chat.username or "",
    }
    if not d.get("active_channel"):
        d["active_channel"] = cid
    save(d)
    try:
        await c.bot.send_message(
            ADMIN_ID,
            f"✅ کانال ثبت شد\nعنوان: {chat.title}\nآیدی: <code>{chat.id}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_kb(d),
        )
    except Exception as e:
        log.error(e)


async def on_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    data = q.data or ""
    user = q.from_user
    d = load()

    if data == "force_check":
        ok, missing = await check_force(c.bot, user.id)
        if ok:
            await q.edit_message_text(d.get("start_msg") or "تأیید شد ✅")
        else:
            await q.answer("هنوز عضو نشدی", show_alert=True)
            try:
                await q.edit_message_reply_markup(reply_markup=force_kb(missing, d))
            except Exception:
                pass
        return

    if data == "noop":
        await q.answer()
        return

    if not is_admin(user.id):
        await q.answer("فقط ادمین", show_alert=True)
        return

    await q.answer()

    if data == "close":
        await q.edit_message_text("بسته شد.")
        return
    if data == "cancel":
        clear_st(c)
        await q.edit_message_text("لغو شد.", reply_markup=main_kb(d))
        return
    if data == "home":
        clear_st(c)
        await q.edit_message_text("🎛 پنل مدیریت کانال", reply_markup=main_kb(d))
        return

    if data == "status":
        ch = d.get("active_channel")
        title = d.get("channels", {}).get(str(ch), {}).get("title", "-")
        txt = (
            f"📊 وضعیت\n"
            f"کانال فعال: {title}\n"
            f"<code>{ch}</code>\n"
            f"تعداد کانال‌ها: {len(d.get('channels', {}))}\n"
            f"صف زمان‌بندی: {len(d.get('queue', []))}\n"
            f"قفل عضویت: {'فعال' if d.get('force_enabled') else 'خاموش'}\n"
            f"امضا: {'دارد' if d.get('signature') else 'ندارد'}"
        )
        await q.edit_message_text(txt, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[btn("🔙", "home", "primary")]]))
        return

    if data == "ch_list":
        rows = []
        for cid, info in d.get("channels", {}).items():
            mark = "✅ " if cid == d.get("active_channel") else ""
            rows.append([btn(f"{mark}{info.get('title', cid)[:30]}", f"ch_set:{cid}", "success" if cid == d.get("active_channel") else "primary")])
        rows.append([btn("🔙", "home", "danger")])
        await q.edit_message_text("کانال فعال را انتخاب کن:", reply_markup=InlineKeyboardMarkup(rows) if rows else InlineKeyboardMarkup([[btn("🔙", "home", "danger")]]))
        return

    if data.startswith("ch_set:"):
        cid = data.split(":", 1)[1]
        if cid in d.get("channels", {}):
            d["active_channel"] = cid
            save(d)
        await q.edit_message_text("✅ کانال فعال شد.", reply_markup=main_kb(d))
        return

    if data == "post":
        set_st(c, "post")
        await q.edit_message_text(
            "پست را بفرست:\n• متن\n• عکس/ویدیو با کپشن\n\nبعداً می‌پرسیم پین شود یا نه.",
            reply_markup=cancel_kb(),
        )
        return

    if data == "sched":
        set_st(c, "sched_content")
        await q.edit_message_text(
            "محتوای پست زمان‌بندی را بفرست (متن یا رسانه):",
            reply_markup=cancel_kb(),
        )
        return

    if data == "queue":
        rows = []
        lines = ["📋 صف زمان‌بندی:\n"]
        for i, item in enumerate(d.get("queue", [])):
            when = item.get("when", "?")
            preview = (item.get("text") or item.get("media_type") or "رسانه")[:40]
            lines.append(f"{i+1}. {when} — {preview}")
            rows.append([btn(f"🗑 حذف #{i+1}", f"qdel:{item.get('id')}", "danger")])
        if not d.get("queue"):
            lines.append("خالی است.")
        rows.append([btn("🔙", "home", "primary")])
        await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("qdel:"):
        qid = data.split(":", 1)[1]
        d["queue"] = [x for x in d.get("queue", []) if str(x.get("id")) != qid]
        save(d)
        await q.answer("حذف شد")
        data = "queue"
        # fallthrough show queue
        rows = []
        lines = ["📋 صف:\n"]
        for i, item in enumerate(d.get("queue", [])):
            lines.append(f"{i+1}. {item.get('when')} — {(item.get('text') or '')[:30]}")
            rows.append([btn(f"🗑 #{i+1}", f"qdel:{item.get('id')}", "danger")])
        rows.append([btn("🔙", "home", "primary")])
        await q.edit_message_text("\n".join(lines) if d.get("queue") else "صف خالی است.", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data == "pin":
        set_st(c, "pin")
        await q.edit_message_text("آیدی عددی پیام در کانال را بفرست (یا لینک پیام):", reply_markup=cancel_kb())
        return
    if data == "unpin":
        set_st(c, "unpin")
        await q.edit_message_text("آیدی پیام برای آنپین (یا 0 برای آخرین پین):", reply_markup=cancel_kb())
        return
    if data == "delete":
        set_st(c, "delete")
        await q.edit_message_text("آیدی پیام برای حذف:", reply_markup=cancel_kb())
        return
    if data == "ban":
        set_st(c, "ban")
        await q.edit_message_text("آیدی عددی کاربر برای بن:", reply_markup=cancel_kb())
        return
    if data == "unban":
        set_st(c, "unban")
        await q.edit_message_text("آیدی عددی کاربر برای آنبن:", reply_markup=cancel_kb())
        return
    if data == "promote":
        set_st(c, "promote")
        await q.edit_message_text("آیدی عددی کاربر برای ادمین کردن در کانال:", reply_markup=cancel_kb())
        return
    if data == "demote":
        set_st(c, "demote")
        await q.edit_message_text("آیدی عددی برای عزل ادمین کانال:", reply_markup=cancel_kb())
        return

    if data == "force":
        kb = InlineKeyboardMarkup([
            [btn("🟢 روشن کردن قفل", "force_on", "success"), btn("🔴 خاموش", "force_off", "danger")],
            [btn("➕ افزودن کانال اجباری", "force_add", "primary")],
            [btn("📋 لیست", "force_list", "primary")],
            [btn("🔙", "home", "primary")],
        ])
        await q.edit_message_text(
            f"🔐 قفل عضویت: {'فعال' if d.get('force_enabled') else 'خاموش'}\n"
            f"تعداد: {len(d.get('force_join', []))}",
            reply_markup=kb,
        )
        return
    if data == "force_on":
        d["force_enabled"] = True
        save(d)
        await q.answer("روشن شد")
        await q.edit_message_text("قفل عضویت فعال شد.", reply_markup=main_kb(d))
        return
    if data == "force_off":
        d["force_enabled"] = False
        save(d)
        await q.answer("خاموش شد")
        await q.edit_message_text("قفل عضویت خاموش شد.", reply_markup=main_kb(d))
        return
    if data == "force_add":
        set_st(c, "force_add")
        await q.edit_message_text("آیدی عددی کانال اجباری را بفرست (بات باید ادمین باشد):", reply_markup=cancel_kb())
        return
    if data == "force_list":
        lines = ["کانال‌های اجباری:"] + [f"• <code>{x}</code>" for x in d.get("force_join", [])] or ["خالی"]
        rows = [[btn(f"🗑 {x}", f"force_del:{x}", "danger")] for x in d.get("force_join", [])]
        rows.append([btn("🔙", "force", "primary")])
        await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return
    if data.startswith("force_del:"):
        x = data.split(":", 1)[1]
        d["force_join"] = [i for i in d.get("force_join", []) if str(i) != x]
        save(d)
        await q.answer("حذف شد")
        await q.edit_message_text("حذف شد.", reply_markup=main_kb(d))
        return

    if data == "startmsg":
        set_st(c, "startmsg")
        await q.edit_message_text(
            f"متن استارت فعلی:\n\n{d.get('start_msg')}\n\nمتن جدید را بفرست:",
            reply_markup=cancel_kb(),
        )
        return
    if data == "sign":
        set_st(c, "sign")
        await q.edit_message_text(
            f"امضای فعلی:\n{d.get('signature') or '(خالی)'}\n\nامضای جدید را بفرست (یا - برای حذف):",
            reply_markup=cancel_kb(),
        )
        return

    # post confirm pin
    if data == "post_pin_yes":
        item = c.user_data.get("pending_post")
        clear_st(c)
        if not item:
            await q.edit_message_text("منقضی شد.", reply_markup=main_kb(d))
            return
        item["pin"] = True
        try:
            await send_post(c.bot, item)
            await q.edit_message_text("✅ پست ارسال و پین شد.", reply_markup=main_kb(d))
        except Exception as e:
            await q.edit_message_text(f"خطا: {e}", reply_markup=main_kb(d))
        c.user_data.pop("pending_post", None)
        return
    if data == "post_pin_no":
        item = c.user_data.get("pending_post")
        clear_st(c)
        if not item:
            await q.edit_message_text("منقضی شد.", reply_markup=main_kb(d))
            return
        item["pin"] = False
        try:
            await send_post(c.bot, item)
            await q.edit_message_text("✅ پست ارسال شد.", reply_markup=main_kb(d))
        except Exception as e:
            await q.edit_message_text(f"خطا: {e}", reply_markup=main_kb(d))
        c.user_data.pop("pending_post", None)
        return


def parse_msg_id(text: str):
    text = text.strip()
    # link https://t.me/c/123/456 or t.me/username/456
    m = re.search(r"/(\d+)/?$", text)
    if m:
        return int(m.group(1))
    if text.isdigit():
        return int(text)
    return None


async def on_private(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or u.effective_chat.type != ChatType.PRIVATE:
        return
    user = u.effective_user
    if not is_admin(user.id):
        # non-admin: only start handled
        return
    st = get_st(c)
    if not st:
        if u.message.text and u.message.text.strip() in ("پنل", "/admin", "admin"):
            await u.message.reply_text("🎛 پنل", reply_markup=main_kb())
        return

    d = load()
    kind = st.get("kind")
    text = (u.message.text or u.message.caption or "").strip()

    if kind == "startmsg":
        if not text:
            await u.message.reply_text("متن بفرست")
            return
        d["start_msg"] = text
        save(d)
        clear_st(c)
        await u.message.reply_text("✅ متن استارت ذخیره شد.", reply_markup=main_kb(d))
        return

    if kind == "sign":
        d["signature"] = "" if text == "-" else text
        save(d)
        clear_st(c)
        await u.message.reply_text("✅ امضا ذخیره شد.", reply_markup=main_kb(d))
        return

    if kind == "force_add":
        cid = text.lstrip("@")
        if not cid:
            await u.message.reply_text("آیدی")
            return
        d.setdefault("force_join", [])
        if cid not in d["force_join"] and str(cid) not in d["force_join"]:
            d["force_join"].append(cid)
        save(d)
        clear_st(c)
        await u.message.reply_text("✅ به قفل اضافه شد.", reply_markup=main_kb(d))
        return

    if kind in ("pin", "unpin", "delete"):
        mid = parse_msg_id(text)
        if mid is None:
            await u.message.reply_text("آیدی نامعتبر")
            return
        chat_id = d.get("active_channel")
        if not chat_id:
            await u.message.reply_text("کانال فعال نیست")
            return
        chat_id = int(chat_id)
        clear_st(c)
        try:
            if kind == "pin":
                await c.bot.pin_chat_message(chat_id, mid)
                await u.message.reply_text("✅ پین شد.", reply_markup=main_kb(d))
            elif kind == "unpin":
                if mid == 0:
                    await c.bot.unpin_all_chat_messages(chat_id)
                else:
                    await c.bot.unpin_chat_message(chat_id, mid)
                await u.message.reply_text("✅ آنپین شد.", reply_markup=main_kb(d))
            else:
                await c.bot.delete_message(chat_id, mid)
                await u.message.reply_text("✅ حذف شد.", reply_markup=main_kb(d))
        except Exception as e:
            await u.message.reply_text(f"خطا: {e}", reply_markup=main_kb(d))
        return

    if kind in ("ban", "unban", "promote", "demote"):
        if not text.lstrip("-").isdigit():
            await u.message.reply_text("آیدی عددی بفرست")
            return
        uid = int(text)
        chat_id = d.get("active_channel")
        if not chat_id:
            await u.message.reply_text("کانال فعال نیست")
            return
        chat_id = int(chat_id)
        clear_st(c)
        try:
            if kind == "ban":
                await c.bot.ban_chat_member(chat_id, uid)
                await u.message.reply_text("✅ بن شد.", reply_markup=main_kb(d))
            elif kind == "unban":
                await c.bot.unban_chat_member(chat_id, uid, only_if_banned=True)
                await u.message.reply_text("✅ آنبن شد.", reply_markup=main_kb(d))
            elif kind == "promote":
                await c.bot.promote_chat_member(
                    chat_id, uid,
                    can_post_messages=True,
                    can_edit_messages=True,
                    can_delete_messages=True,
                    can_invite_users=True,
                    can_manage_chat=True,
                )
                await u.message.reply_text("✅ ادمین شد.", reply_markup=main_kb(d))
            else:
                await c.bot.promote_chat_member(
                    chat_id, uid,
                    can_post_messages=False,
                    can_edit_messages=False,
                    can_delete_messages=False,
                    can_invite_users=False,
                    can_manage_chat=False,
                )
                await u.message.reply_text("✅ عزل شد.", reply_markup=main_kb(d))
        except Exception as e:
            await u.message.reply_text(f"خطا: {e}", reply_markup=main_kb(d))
        return

    if kind == "post":
        item = build_item_from_message(u.message, d)
        if not item:
            await u.message.reply_text("محتوا نامعتبر")
            return
        c.user_data["pending_post"] = item
        clear_st(c)
        kb = InlineKeyboardMarkup([
            [btn("📌 بله، پین شود", "post_pin_yes", "success")],
            [btn("ارسال بدون پین", "post_pin_no", "primary")],
            [btn("انصراف", "cancel", "danger")],
        ])
        await u.message.reply_text("پین شود؟", reply_markup=kb)
        return

    if kind == "sched_content":
        item = build_item_from_message(u.message, d)
        if not item:
            await u.message.reply_text("محتوا نامعتبر")
            return
        c.user_data["pending_sched"] = item
        set_st(c, "sched_time")
        await u.message.reply_text(
            "زمان را بفرست (وقت تهران):\n"
            "• <code>2026-09-10 21:30</code>\n"
            "• یا <code>21:30</code> برای امروز/فردا",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_kb(),
        )
        return

    if kind == "sched_time":
        item = c.user_data.get("pending_sched")
        if not item:
            clear_st(c)
            await u.message.reply_text("منقضی", reply_markup=main_kb(d))
            return
        when = parse_when(text)
        if not when:
            await u.message.reply_text("فرمت زمان نامعتبر")
            return
        item["when"] = when.isoformat()
        item["id"] = str(int(datetime.now().timestamp()))
        item["chat_id"] = d.get("active_channel")
        d.setdefault("queue", []).append(item)
        save(d)
        clear_st(c)
        c.user_data.pop("pending_sched", None)
        await u.message.reply_text(
            f"✅ زمان‌بندی شد برای {when.strftime('%Y-%m-%d %H:%M')} تهران",
            reply_markup=main_kb(d),
        )
        return


def build_item_from_message(msg, d):
    item = {
        "chat_id": d.get("active_channel"),
        "text": (msg.caption or msg.text or "").strip(),
        "file_id": None,
        "media_type": None,
        "pin": False,
    }
    if msg.photo:
        item["file_id"] = msg.photo[-1].file_id
        item["media_type"] = "photo"
    elif msg.video:
        item["file_id"] = msg.video.file_id
        item["media_type"] = "video"
    elif msg.document:
        item["file_id"] = msg.document.file_id
        item["media_type"] = "document"
    if not item["text"] and not item["file_id"]:
        return None
    return item


def parse_when(text: str):
    text = text.strip()
    now = datetime.now(TZ)
    # full
    for fmt in ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=TZ)
        except ValueError:
            pass
    # time only
    m = re.match(r"^(\d{1,2}):(\d{2})$", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        dt = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if dt <= now:
            dt += timedelta(days=1)
        return dt
    return None


async def post_init(app: Application):
    if app.job_queue:
        app.job_queue.run_repeating(process_queue, interval=30, first=5)
    else:
        log.warning("Install: pip install 'python-telegram-bot[job-queue]'")


def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(ChatMemberHandler(on_my_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL), on_private))
    log.info("channel manager started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
