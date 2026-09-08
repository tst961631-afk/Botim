# -*- coding: utf-8 -*-
"""
ربات رله گپ: چت در گپ از طریق پیوی ادمین
- ریپلای روی بات در گپ → فوری پیوی ادمین
- ریپلای ادمین در پیوی → ارسال در گپ روی همان پیام
- دکمه ۲۰ پیام اخیر + ریپلای روی هرکدام
- تأیید گپ (بدون تأیید تکی هر پیام)
- استیکر / ویس / عکس / فیلم / متن / فایل
"""
from __future__ import annotations
import json, os, logging, time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ChatMemberHandler, ContextTypes, filters,
)
from telegram.constants import ChatType, ChatMemberStatus

BOT_TOKEN = "8975007734:AAECUtykIq5YSt0Wc3YpFKtgOKSAs-muOoY"
ADMIN_ID = 8918154552
DATA = "relay_data.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("relay")


def D():
    return {
        "groups": {},          # str(chat_id) -> {title, approved: bool}
        "active_group": None,  # str(chat_id)
        # map: admin_pm_message_id -> {chat_id, reply_to_message_id}
        "bridges": {},
    }


def load():
    if os.path.exists(DATA):
        try:
            with open(DATA, "r", encoding="utf-8") as f:
                d = json.load(f)
            b = D()
            for k, v in b.items():
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


def main_kb(d=None):
    d = d or load()
    ag = d.get("active_group")
    title = "-"
    if ag and ag in d.get("groups", {}):
        title = d["groups"][ag].get("title") or ag
        if not d["groups"][ag].get("approved"):
            title = f"(تأییدنشده) {title}"
    return InlineKeyboardMarkup([
        [btn(f"📢 گپ فعال: {str(title)[:22]}", "g_list", "primary")],
        [btn("📥 ۲۰ پیام اخیر", "g_hist", "success")],
        [btn("✍️ ارسال به گپ", "g_send", "success")],
        [btn("📋 لیست گپ‌ها", "g_list", "primary")],
        [btn("ℹ️ وضعیت", "g_status", "primary")],
        [btn("❌ بستن", "close", "danger")],
    ])


def set_st(c, kind, extra=None):
    c.user_data["st"] = {"kind": kind, "extra": extra or {}, "ts": time.time()}


def get_st(c):
    st = c.user_data.get("st")
    if not st:
        return None
    if time.time() - st.get("ts", 0) > 300:
        c.user_data.pop("st", None)
        return None
    return st


def clear_st(c):
    c.user_data.pop("st", None)


def bridge_put(d, pm_msg_id, chat_id, reply_to):
    d.setdefault("bridges", {})[str(pm_msg_id)] = {
        "chat_id": int(chat_id),
        "reply_to": int(reply_to),
        "ts": time.time(),
    }
    # cleanup old bridges
    now = time.time()
    d["bridges"] = {
        k: v for k, v in d["bridges"].items()
        if now - v.get("ts", 0) < 86400 * 3
    }


def bridge_get(d, pm_msg_id):
    return d.get("bridges", {}).get(str(pm_msg_id))


# ---------- copy any message to target ----------
async def copy_to_chat(bot, message, chat_id, reply_to=None):
    """کپی کامل پیام (متن/عکس/ویس/استیکر/فیلم/...) به گپ"""
    kwargs = {"chat_id": int(chat_id)}
    if reply_to:
        kwargs["reply_to_message_id"] = int(reply_to)
    try:
        return await bot.copy_message(
            from_chat_id=message.chat_id,
            message_id=message.message_id,
            **kwargs,
        )
    except Exception as e:
        log.error("copy fail: %s", e)
        # fallback text
        text = message.text or message.caption or "[رسانه غیرقابل کپی]"
        return await bot.send_message(
            chat_id=int(chat_id),
            text=text,
            reply_to_message_id=int(reply_to) if reply_to else None,
        )


async def forward_group_msg_to_admin(bot, message, d):
    """پیام گپ (ریپلای روی بات یا از هیستوری) را به ادمین بفرست و bridge بساز"""
    user = message.from_user
    chat = message.chat
    name = user.full_name if user else "?"
    un = f"@{user.username}" if user and user.username else str(user.id if user else "?")
    header = (
        f"💬 از گپ: {chat.title or chat.id}\n"
        f"👤 {name} ({un})\n"
        f"msgid: {message.message_id}\n"
        f"— ریپلای روی این پیام = جواب در گپ —"
    )
    try:
        await bot.send_message(ADMIN_ID, header)
    except Exception:
        pass
    try:
        sent = await bot.copy_message(
            chat_id=ADMIN_ID,
            from_chat_id=message.chat_id,
            message_id=message.message_id,
        )
        bridge_put(d, sent.message_id, message.chat_id, message.message_id)
        save(d)
        return sent
    except Exception as e:
        log.error("fwd admin: %s", e)
        # text fallback
        body = message.text or message.caption or "[رسانه]"
        sent = await bot.send_message(ADMIN_ID, f"{header}\n\n{body}")
        bridge_put(d, sent.message_id, message.chat_id, message.message_id)
        save(d)
        return sent


# ---------- commands ----------
async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_admin(u.effective_user.id):
        await u.message.reply_text("این ربات فقط برای ادمین است.")
        return
    if u.effective_chat.type != ChatType.PRIVATE:
        return
    clear_st(c)
    await u.message.reply_text("🎛 پنل رله گپ", reply_markup=main_kb())


async def cmd_panel(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_admin(u.effective_user.id) or u.effective_chat.type != ChatType.PRIVATE:
        return
    clear_st(c)
    await u.message.reply_text("🎛 پنل رله گپ", reply_markup=main_kb())


# bot added to group
async def on_my_member(u: Update, c: ContextTypes.DEFAULT_TYPE):
    r = u.my_chat_member
    chat = r.chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    new = r.new_chat_member
    if new.status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
        return
    d = load()
    cid = str(chat.id)
    d.setdefault("groups", {})[cid] = {
        "title": chat.title or cid,
        "approved": d.get("groups", {}).get(cid, {}).get("approved", False),
    }
    save(d)
    kb = InlineKeyboardMarkup([
        [btn("✅ تأیید این گپ", f"approve:{cid}", "success")],
        [btn("🎯 فعال کردن", f"activate:{cid}", "primary")],
        [btn("📋 پنل", "home", "primary")],
    ])
    try:
        await c.bot.send_message(
            ADMIN_ID,
            f"گپ جدید / به‌روز شد\nعنوان: {chat.title}\nآیدی: <code>{chat.id}</code>\n"
            f"برای رله بدون تأیید تکی، گپ را تأیید کن.",
            parse_mode="HTML",
            reply_markup=kb,
        )
    except Exception as e:
        log.error(e)


async def on_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    data = q.data or ""
    if not is_admin(q.from_user.id):
        await q.answer("فقط ادمین", show_alert=True)
        return
    await q.answer()
    d = load()

    if data == "close":
        await q.edit_message_text("بسته شد.")
        return
    if data == "home":
        clear_st(c)
        await q.edit_message_text("🎛 پنل رله گپ", reply_markup=main_kb(d))
        return

    if data == "g_status":
        ag = d.get("active_group")
        g = d.get("groups", {}).get(ag or "", {})
        txt = (
            f"وضعیت\n"
            f"گپ فعال: {g.get('title', '-')}\n"
            f"آیدی: <code>{ag}</code>\n"
            f"تأییدشده: {'بله' if g.get('approved') else 'خیر'}\n"
            f"تعداد گپ‌ها: {len(d.get('groups', {}))}"
        )
        await q.edit_message_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[btn("🔙", "home", "primary")]]))
        return

    if data == "g_list":
        rows = []
        for cid, info in d.get("groups", {}).items():
            ok = "✅" if info.get("approved") else "⏳"
            act = "🔹" if cid == d.get("active_group") else ""
            rows.append([btn(f"{act}{ok} {info.get('title', cid)[:24]}", f"g_menu:{cid}", "primary")])
        if not rows:
            rows = [[btn("گپی نیست — بات را به گپ اضافه کن", "home", "danger")]]
        rows.append([btn("🔙", "home", "danger")])
        await q.edit_message_text("گپ‌ها:", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("g_menu:"):
        cid = data.split(":", 1)[1]
        info = d.get("groups", {}).get(cid, {})
        txt = (
            f"گپ: {info.get('title')}\n"
            f"<code>{cid}</code>\n"
            f"تأیید: {'بله' if info.get('approved') else 'خیر'}"
        )
        kb = InlineKeyboardMarkup([
            [btn("🎯 فعال‌سازی", f"activate:{cid}", "success")],
            [btn("✅ تأیید گپ", f"approve:{cid}", "success")],
            [btn("❌ رد تأیید", f"unapprove:{cid}", "danger")],
            [btn("📥 ۲۰ پیام اخیر", f"hist:{cid}", "primary")],
            [btn("🔙", "g_list", "primary")],
        ])
        await q.edit_message_text(txt, parse_mode="HTML", reply_markup=kb)
        return

    if data.startswith("approve:"):
        cid = data.split(":", 1)[1]
        d.setdefault("groups", {}).setdefault(cid, {"title": cid, "approved": False})
        d["groups"][cid]["approved"] = True
        if not d.get("active_group"):
            d["active_group"] = cid
        save(d)
        await q.edit_message_text("✅ گپ تأیید شد. رله بدون تأیید تکی فعال است.", reply_markup=main_kb(d))
        return

    if data.startswith("unapprove:"):
        cid = data.split(":", 1)[1]
        if cid in d.get("groups", {}):
            d["groups"][cid]["approved"] = False
            save(d)
        await q.edit_message_text("تأیید برداشته شد.", reply_markup=main_kb(d))
        return

    if data.startswith("activate:"):
        cid = data.split(":", 1)[1]
        if cid not in d.get("groups", {}):
            await q.answer("نیست", show_alert=True)
            return
        d["active_group"] = cid
        save(d)
        await q.edit_message_text(f"✅ گپ فعال: {d['groups'][cid].get('title')}", reply_markup=main_kb(d))
        return

    if data == "g_send":
        ag = d.get("active_group")
        if not ag or not d.get("groups", {}).get(ag, {}).get("approved"):
            await q.edit_message_text("اول یک گپ تأییدشده را فعال کن.", reply_markup=main_kb(d))
            return
        set_st(c, "send_to_group")
        await q.edit_message_text(
            "هر چیزی بفرست (متن، عکس، ویس، استیکر، فیلم، فایل)\nمستقیم به گپ فعال ارسال می‌شود.\nبرای پایان: /panel",
            reply_markup=InlineKeyboardMarkup([[btn("تمام", "home", "danger")]]),
        )
        return

    if data == "g_hist" or data.startswith("hist:"):
        if data.startswith("hist:"):
            cid = data.split(":", 1)[1]
        else:
            cid = d.get("active_group")
        if not cid:
            await q.edit_message_text("گپ فعال نیست.", reply_markup=main_kb(d))
            return
        await q.edit_message_text("⏳ در حال دریافت پیام‌های اخیر...")
        await fetch_recent(c, d, cid, q)
        return


async def fetch_recent(c, d, cid, q=None):
    """
    تلگرام API مستقیم «آخرین N پیام» به بات نمی‌دهد مگر اینکه پیام‌ها را دیده باشد.
    راهکار: از pin/ادمین پیام بخواهیم — یا پیام‌هایی که بات دیده را نگه داریم.
    اینجا از cache اخیر گروه استفاده می‌کنیم که هنگام پیام‌های گپ پر شده.
    """
    cache = d.get("groups", {}).get(str(cid), {}).get("recent", [])
    if not cache:
        msg = (
            "هنوز پیامی از این گپ کش نشده.\n"
            "چند پیام در گپ رد و بدل شود (یا به بات ریپلای شود) تا اینجا بیاید.\n"
            "بعد دوباره «۲۰ پیام اخیر» را بزن."
        )
        if q:
            await q.edit_message_text(msg, reply_markup=main_kb(d))
        else:
            await c.bot.send_message(ADMIN_ID, msg, reply_markup=main_kb(d))
        return

    items = cache[-20:]
    await c.bot.send_message(
        ADMIN_ID,
        f"📥 {len(items)} پیام اخیر گپ (ریپلای روی هرکدام = جواب در گپ):",
    )
    for it in items:
        try:
            # re-copy from group if possible
            sent = await c.bot.copy_message(
                chat_id=ADMIN_ID,
                from_chat_id=int(cid),
                message_id=int(it["message_id"]),
            )
            bridge_put(d, sent.message_id, cid, it["message_id"])
        except Exception:
            body = it.get("preview") or f"[msg {it.get('message_id')}]"
            who = it.get("from") or "?"
            sent = await c.bot.send_message(
                ADMIN_ID,
                f"👤 {who}\n{body}\n— ریپلای = جواب در گپ —",
            )
            bridge_put(d, sent.message_id, cid, it["message_id"])
    save(d)
    await c.bot.send_message(ADMIN_ID, "✅ تمام. روی هر پیام ریپلای کن.", reply_markup=main_kb(d))


def push_recent(d, message):
    cid = str(message.chat_id)
    g = d.setdefault("groups", {}).setdefault(cid, {"title": message.chat.title or cid, "approved": False, "recent": []})
    if message.chat.title:
        g["title"] = message.chat.title
    preview = message.text or message.caption or ""
    if not preview:
        if message.sticker:
            preview = "[استیکر]"
        elif message.voice:
            preview = "[ویس]"
        elif message.photo:
            preview = "[عکس]"
        elif message.video:
            preview = "[ویدیو]"
        elif message.document:
            preview = "[فایل]"
        else:
            preview = "[رسانه]"
    who = message.from_user.full_name if message.from_user else "?"
    g.setdefault("recent", []).append({
        "message_id": message.message_id,
        "from": who,
        "preview": preview[:200],
        "ts": time.time(),
    })
    g["recent"] = g["recent"][-40:]


# ---------- group messages ----------
async def on_group(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    chat = u.effective_chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    d = load()
    cid = str(chat.id)
    # register group
    d.setdefault("groups", {}).setdefault(cid, {"title": chat.title or cid, "approved": False, "recent": []})
    if chat.title:
        d["groups"][cid]["title"] = chat.title
    push_recent(d, u.message)
    save(d)

    # only relay if reply to this bot
    rp = u.message.reply_to_message
    if not rp or not rp.from_user or not rp.from_user.is_bot:
        return
    me = await c.bot.get_me()
    if rp.from_user.id != me.id:
        return

    # group must be approved
    if not d["groups"][cid].get("approved"):
        try:
            await u.message.reply_text("این گپ هنوز توسط ادمین تأیید نشده.")
        except Exception:
            pass
        # still notify admin to approve
        try:
            kb = InlineKeyboardMarkup([[btn("✅ تأیید گپ", f"approve:{cid}", "success")]])
            await c.bot.send_message(
                ADMIN_ID,
                f"پیام از گپ تأییدنشده {chat.title}\n<code>{cid}</code>",
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception:
            pass
        return

    await forward_group_msg_to_admin(c.bot, u.message, d)


# ---------- admin private ----------
async def on_admin_private(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    if u.effective_chat.type != ChatType.PRIVATE:
        return
    if not is_admin(u.effective_user.id):
        return

    d = load()
    text = (u.message.text or "").strip()

    if text in ("/panel", "پنل", "/start"):
        clear_st(c)
        await u.message.reply_text("🎛 پنل رله گپ", reply_markup=main_kb(d))
        return

    # reply to a bridged message → send to group
    if u.message.reply_to_message:
        b = bridge_get(d, u.message.reply_to_message.message_id)
        if b:
            try:
                await copy_to_chat(c.bot, u.message, b["chat_id"], reply_to=b["reply_to"])
                await u.message.reply_text("✅ در گپ ارسال شد.")
            except Exception as e:
                await u.message.reply_text(f"خطا: {e}")
            return

    st = get_st(c)
    if st and st.get("kind") == "send_to_group":
        ag = d.get("active_group")
        if not ag or not d.get("groups", {}).get(ag, {}).get("approved"):
            clear_st(c)
            await u.message.reply_text("گپ فعال/تأییدشده نیست.", reply_markup=main_kb(d))
            return
        try:
            await copy_to_chat(c.bot, u.message, ag)
            await u.message.reply_text("✅ ارسال شد. (ادامه بده یا /panel)")
        except Exception as e:
            await u.message.reply_text(f"خطا: {e}")
        return


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("panel", cmd_panel))
    app.add_handler(ChatMemberHandler(on_my_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & ~filters.StatusUpdate.ALL,
        on_group,
    ))
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.COMMAND,
        on_admin_private,
    ))
    # commands already handled; also private media with command filter off above
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.COMMAND,
        on_admin_private,
    ))
    log.info("relay bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
