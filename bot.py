# -*- coding: utf-8 -*-
"""
ربات رله گپ — فقط استیکر/گیف از گپ فعال
- گپ یک‌بار انتخاب و تأیید می‌شود
- دریافت روشن/خاموش
- جواب و ریکشن از پیوی
- چند ادمین (ادمین اصلی اضافه/حذف می‌کند)
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
ADMIN_ID = 7530457395
DATA = "relay_data.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("relay")


def D():
    return {
        "groups": {},
        "active_group": None,
        "admins": [ADMIN_ID],
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
            ads = [int(x) for x in d.get("admins", [])]
            if ADMIN_ID not in ads:
                ads.insert(0, ADMIN_ID)
            d["admins"] = ads
            return d
        except Exception as e:
            log.error(e)
    return D()


def save(d):
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def is_admin(uid):
    d = load()
    return int(uid) in set(int(x) for x in d.get("admins", [ADMIN_ID]))


def is_main_admin(uid):
    return int(uid) == ADMIN_ID


def admin_ids(d):
    ids = {ADMIN_ID}
    for x in d.get("admins", []):
        try:
            ids.add(int(x))
        except Exception:
            pass
    return list(ids)


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
    relay_on = False
    if ag and ag in d.get("groups", {}):
        g = d["groups"][ag]
        title = g.get("title") or ag
        if not g.get("approved"):
            title = f"(تأییدنشده) {title}"
        relay_on = bool(g.get("relay_on", True)) and bool(g.get("approved"))
    relay_btn = (
        btn("🟢 دریافت پیام: روشن", "relay_off", "success")
        if relay_on
        else btn("🔴 دریافت پیام: خاموش", "relay_on", "danger")
    )
    return InlineKeyboardMarkup([
        [btn(f"📢 گپ فعال: {str(title)[:22]}", "g_list", "primary")],
        [relay_btn],
        [btn("📥 ۲۰ پیام اخیر", "g_hist", "success")],
        [btn("✍️ ارسال به گپ فعال", "g_send", "success")],
        [btn("📋 لیست گپ‌ها", "g_list", "primary")],
        [btn("👤 ادمین‌ها", "adm_list", "primary")],
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
    now = time.time()
    d["bridges"] = {k: v for k, v in d["bridges"].items() if now - v.get("ts", 0) < 86400 * 3}


def bridge_get(d, pm_msg_id):
    return d.get("bridges", {}).get(str(pm_msg_id))


def is_sticker_or_gif(message) -> bool:
    if message.sticker:
        return True
    if message.animation:
        return True
    if message.document:
        mime = (message.document.mime_type or "").lower()
        name = (message.document.file_name or "").lower()
        if mime == "image/gif" or name.endswith(".gif"):
            return True
    return False


async def copy_to_chat(bot, message, chat_id, reply_to=None):
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
        log.error("copy: %s", e)
        text = message.text or message.caption or "[رسانه]"
        return await bot.send_message(
            chat_id=int(chat_id),
            text=text,
            reply_to_message_id=int(reply_to) if reply_to else None,
        )


async def forward_group_msg_to_admin(bot, message, d):
    """فقط فوروارد خام پیام — بدون هدر اضافه؛ اسم فرد از تلگرام مشخص است"""
    last = None
    for aid in admin_ids(d):
        try:
            sent = await bot.forward_message(
                chat_id=aid,
                from_chat_id=message.chat_id,
                message_id=message.message_id,
            )
            bridge_put(d, sent.message_id, message.chat_id, message.message_id)
            last = sent
        except Exception as e:
            log.error("fwd %s: %s", aid, e)
            # اگر فوروارد بسته بود، کپی + یک خط کوتاه
            try:
                user = message.from_user
                name = user.full_name if user else "?"
                sent = await bot.copy_message(
                    chat_id=aid,
                    from_chat_id=message.chat_id,
                    message_id=message.message_id,
                )
                bridge_put(d, sent.message_id, message.chat_id, message.message_id)
                last = sent
            except Exception as e2:
                log.error("copy fallback %s: %s", aid, e2)
    save(d)
    return last



def push_recent(d, message):
    cid = str(message.chat_id)
    g = d.setdefault("groups", {}).setdefault(
        cid, {"title": message.chat.title or cid, "approved": False, "relay_on": True, "recent": []}
    )
    if message.chat.title:
        g["title"] = message.chat.title
    who = message.from_user.full_name if message.from_user else "?"
    preview = message.text or message.caption or ""
    if not preview:
        if message.sticker:
            preview = "[استیکر]"
        elif message.animation:
            preview = "[گیف]"
        elif message.photo:
            preview = "[عکس]"
        elif message.voice:
            preview = "[ویس]"
        elif message.video:
            preview = "[ویدیو]"
        else:
            preview = "[رسانه]"
    g.setdefault("recent", []).append({
        "message_id": message.message_id,
        "from": who,
        "preview": preview[:200],
        "ts": time.time(),
    })
    g["recent"] = g["recent"][-40:]


def extract_reaction_emoji(text: str):
    if not text:
        return None
    t = text.strip()
    low = t.lower()
    for prefix in ("ریکشن", "ریکش", "reaction", "react"):
        if low.startswith(prefix):
            rest = t[len(prefix):].strip()
            if rest:
                return rest.split()[0]
    return None


# ---------- commands ----------
async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_admin(u.effective_user.id):
        await u.message.reply_text("این ربات فقط برای ادمین‌هاست.")
        return
    if u.effective_chat.type != ChatType.PRIVATE:
        return
    clear_st(c)
    await u.message.reply_text("🎛 پنل رله گپ\nگپ فعال یک‌بار انتخاب می‌شود.", reply_markup=main_kb())


async def cmd_panel(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_admin(u.effective_user.id) or u.effective_chat.type != ChatType.PRIVATE:
        return
    clear_st(c)
    await u.message.reply_text("🎛 پنل رله گپ", reply_markup=main_kb())


async def on_my_member(u: Update, c: ContextTypes.DEFAULT_TYPE):
    r = u.my_chat_member
    chat = r.chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if r.new_chat_member.status not in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
        return
    d = load()
    cid = str(chat.id)
    prev = d.get("groups", {}).get(cid, {})
    d.setdefault("groups", {})[cid] = {
        "title": chat.title or cid,
        "approved": prev.get("approved", False),
        "relay_on": prev.get("relay_on", True),
        "recent": prev.get("recent", []),
    }
    save(d)
    kb = InlineKeyboardMarkup([
        [btn("✅ تأیید + فعال‌سازی این گپ", f"approve:{cid}", "success")],
        [btn("🎯 فقط فعال کردن", f"activate:{cid}", "primary")],
    ])
    for aid in admin_ids(d):
        try:
            await c.bot.send_message(
                aid,
                f"گپ: {chat.title}\n<code>{chat.id}</code>\n"
                f"با تأیید، این گپ فعال می‌ماند تا خودت عوض کنی.",
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception:
            pass


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
            f"گپ فعال: {g.get('title', '-')}\n"
            f"<code>{ag}</code>\n"
            f"تأیید: {'بله' if g.get('approved') else 'خیر'}\n"
            f"دریافت: {'روشن 🟢' if g.get('relay_on', True) else 'خاموش 🔴'}\n"
            f"نوع: همه پیام‌ها (متن/استیکر/گیف/...)\n"
            f"ادمین‌ها: {len(admin_ids(d))}"
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
            rows = [[btn("گپی نیست", "home", "danger")]]
        rows.append([btn("🔙", "home", "danger")])
        await q.edit_message_text("گپ‌ها (🔹 = فعال فعلی):", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("g_menu:"):
        cid = data.split(":", 1)[1]
        info = d.get("groups", {}).get(cid, {})
        txt = (
            f"گپ: {info.get('title')}\n<code>{cid}</code>\n"
            f"تأیید: {'بله' if info.get('approved') else 'خیر'}\n"
            f"فعال فعلی: {'بله' if cid == d.get('active_group') else 'خیر'}"
        )
        kb = InlineKeyboardMarkup([
            [btn("🎯 فعال‌سازی (می‌ماند تا عوض کنی)", f"activate:{cid}", "success")],
            [btn("✅ تأیید گپ", f"approve:{cid}", "success")],
            [btn("❌ رد تأیید", f"unapprove:{cid}", "danger")],
            [btn("📥 اخیر", f"hist:{cid}", "primary")],
            [btn("🔙", "g_list", "primary")],
        ])
        await q.edit_message_text(txt, parse_mode="HTML", reply_markup=kb)
        return

    if data.startswith("approve:"):
        cid = data.split(":", 1)[1]
        d.setdefault("groups", {}).setdefault(cid, {"title": cid, "approved": False, "relay_on": True, "recent": []})
        d["groups"][cid]["approved"] = True
        d["groups"][cid]["relay_on"] = True
        d["active_group"] = cid  # یک‌بار انتخاب
        save(d)
        await q.edit_message_text(
            "✅ گپ تأیید و فعال شد.\nتا وقتی از لیست عوض نکنی، همین گپ می‌ماند.\nدریافت همه پیام‌ها روشن است.",
            reply_markup=main_kb(d),
        )
        return

    if data.startswith("unapprove:"):
        cid = data.split(":", 1)[1]
        if cid in d.get("groups", {}):
            d["groups"][cid]["approved"] = False
            d["groups"][cid]["relay_on"] = False
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
        await q.edit_message_text(
            f"✅ گپ فعال: {d['groups'][cid].get('title')}\nتا عوض نکنی همین می‌ماند.",
            reply_markup=main_kb(d),
        )
        return

    if data == "relay_on":
        ag = d.get("active_group")
        if not ag or ag not in d.get("groups", {}):
            await q.answer("اول گپ را فعال کن", show_alert=True)
            return
        d["groups"][ag]["relay_on"] = True
        save(d)
        await q.edit_message_text("🟢 دریافت همه پیام‌ها روشن شد.", reply_markup=main_kb(d))
        return

    if data == "relay_off":
        ag = d.get("active_group")
        if not ag or ag not in d.get("groups", {}):
            await q.answer("گپ فعال نیست", show_alert=True)
            return
        d["groups"][ag]["relay_on"] = False
        save(d)
        await q.edit_message_text("🔴 دریافت خاموش شد.", reply_markup=main_kb(d))
        return

    if data == "adm_list":
        lines = ["👤 ادمین‌ها\n"]
        rows = []
        for a in admin_ids(d):
            tag = " (اصلی)" if a == ADMIN_ID else ""
            lines.append(f"• <code>{a}</code>{tag}")
            if a != ADMIN_ID and is_main_admin(q.from_user.id):
                rows.append([btn(f"🗑 حذف {a}", f"adm_del:{a}", "danger")])
        if is_main_admin(q.from_user.id):
            rows.insert(0, [btn("➕ افزودن ادمین", "adm_add", "success")])
        rows.append([btn("🔙", "home", "primary")])
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data == "adm_add":
        if not is_main_admin(q.from_user.id):
            await q.answer("فقط ادمین اصلی", show_alert=True)
            return
        set_st(c, "adm_add")
        await q.edit_message_text("آیدی عددی ادمین جدید:", reply_markup=InlineKeyboardMarkup([[btn("انصراف", "home", "danger")]]))
        return

    if data.startswith("adm_del:"):
        if not is_main_admin(q.from_user.id):
            await q.answer("فقط ادمین اصلی", show_alert=True)
            return
        aid = int(data.split(":")[1])
        if aid == ADMIN_ID:
            await q.answer("اصلی پاک نمی‌شود", show_alert=True)
            return
        d["admins"] = [x for x in d.get("admins", []) if int(x) != aid]
        save(d)
        await q.edit_message_text("حذف شد.", reply_markup=main_kb(d))
        return

    if data == "g_send":
        ag = d.get("active_group")
        if not ag or not d.get("groups", {}).get(ag, {}).get("approved"):
            await q.edit_message_text("اول یک گپ تأییدشده را فعال کن.", reply_markup=main_kb(d))
            return
        set_st(c, "send_to_group")
        await q.edit_message_text(
            "هر چیزی بفرست → می‌رود به گپ فعال.\n/panel برای پایان",
            reply_markup=InlineKeyboardMarkup([[btn("تمام", "home", "danger")]]),
        )
        return

    if data == "g_hist" or data.startswith("hist:"):
        cid = data.split(":", 1)[1] if data.startswith("hist:") else d.get("active_group")
        if not cid:
            await q.edit_message_text("گپ فعال نیست.", reply_markup=main_kb(d))
            return
        cache = d.get("groups", {}).get(str(cid), {}).get("recent", [])
        if not cache:
            await q.edit_message_text(
                "هنوز استیکر/گیفی کش نشده. چندتا در گپ فرستاده شود بعد دوباره بزن.",
                reply_markup=main_kb(d),
            )
            return
        await q.edit_message_text(f"ارسال {min(20, len(cache))} مورد اخیر...")
        for it in cache[-20:]:
            try:
                sent = await c.bot.copy_message(ADMIN_ID, int(cid), int(it["message_id"]))
                # also to current admin
                if q.from_user.id != ADMIN_ID:
                    sent = await c.bot.copy_message(q.from_user.id, int(cid), int(it["message_id"]))
                bridge_put(d, sent.message_id, cid, it["message_id"])
            except Exception:
                sent = await c.bot.send_message(
                    q.from_user.id,
                    f"👤 {it.get('from')}\n{it.get('preview')}\n— ریپلای = جواب —",
                )
                bridge_put(d, sent.message_id, cid, it["message_id"])
        save(d)
        await c.bot.send_message(q.from_user.id, "✅ تمام.", reply_markup=main_kb(d))
        return


async def on_group(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    chat = u.effective_chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if u.message.from_user and u.message.from_user.is_bot:
        me = await c.bot.get_me()
        if u.message.from_user.id == me.id:
            return

    d = load()
    cid = str(chat.id)
    d.setdefault("groups", {}).setdefault(
        cid, {"title": chat.title or cid, "approved": False, "relay_on": True, "recent": []}
    )
    if chat.title:
        d["groups"][cid]["title"] = chat.title
    push_recent(d, u.message)
    save(d)

    g = d["groups"][cid]
    if not g.get("approved") or not g.get("relay_on", True):
        return
    # فقط گپ فعال
    if str(d.get("active_group")) != cid:
        return
    # همه پیام‌ها: متن، استیکر، گیف، عکس، ویس، ...

    await forward_group_msg_to_admin(c.bot, u.message, d)


async def on_admin_private(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or u.effective_chat.type != ChatType.PRIVATE:
        return
    if not is_admin(u.effective_user.id):
        return

    d = load()
    text = (u.message.text or "").strip()

    if text in ("/panel", "پنل", "/start"):
        clear_st(c)
        await u.message.reply_text("🎛 پنل رله گپ", reply_markup=main_kb(d))
        return

    st = get_st(c)
    if st and st.get("kind") == "adm_add" and text:
        if not is_main_admin(u.effective_user.id):
            clear_st(c)
            return
        if not text.lstrip("-").isdigit():
            await u.message.reply_text("آیدی عددی")
            return
        aid = int(text)
        if aid not in d.get("admins", []):
            d.setdefault("admins", [ADMIN_ID]).append(aid)
            save(d)
        clear_st(c)
        await u.message.reply_text(f"✅ ادمین: {aid}", reply_markup=main_kb(d))
        try:
            await c.bot.send_message(aid, "ادمین رله شدی. /panel")
        except Exception:
            pass
        return

    if u.message.reply_to_message:
        b = bridge_get(d, u.message.reply_to_message.message_id)
        if b:
            emoji = extract_reaction_emoji(text) if text else None
            if emoji:
                try:
                    try:
                        from telegram import ReactionTypeEmoji
                        reaction = [ReactionTypeEmoji(emoji=emoji)]
                    except Exception:
                        reaction = [{"type": "emoji", "emoji": emoji}]
                    await c.bot.set_message_reaction(
                        chat_id=b["chat_id"], message_id=b["reply_to"], reaction=reaction
                    )
                    await u.message.reply_text(f"✅ ریکشن {emoji}")
                except Exception as e:
                    await u.message.reply_text(f"خطا ریکشن: {e}")
                return
            try:
                await copy_to_chat(c.bot, u.message, b["chat_id"], reply_to=b["reply_to"])
                await u.message.reply_text("✅ در گپ ارسال شد.")
            except Exception as e:
                await u.message.reply_text(f"خطا: {e}")
            return

    if st and st.get("kind") == "send_to_group":
        ag = d.get("active_group")
        if not ag or not d.get("groups", {}).get(ag, {}).get("approved"):
            clear_st(c)
            await u.message.reply_text("گپ فعال نیست.", reply_markup=main_kb(d))
            return
        try:
            await copy_to_chat(c.bot, u.message, ag)
            await u.message.reply_text("✅ ارسال شد.")
        except Exception as e:
            await u.message.reply_text(f"خطا: {e}")
        return


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("panel", cmd_panel))
    app.add_handler(ChatMemberHandler(on_my_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.StatusUpdate.ALL, on_group))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE, on_admin_private))
    log.info("relay bot up")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
