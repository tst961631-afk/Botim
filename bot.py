# -*- coding: utf-8 -*-
"""
ریکشن‌های سفارشی (پرمیوم) روی پست کانال
خروجی برای هر ریکشن سفارشی:
  [تصویر همان ریکشن]
  کپشن: https://t.me/username/message_id/custom_emoji_id

بات باید ادمین کانال باشد.
محدودیت تلگرام (Bot API):
  لیست ریکشن‌های کانال فقط با آپدیت message_reaction_count می‌آید
  (وقتی تعداد ریکشن عوض شود). متد «برو الان همه ریکشن‌های قدیم این پست را بده» وجود ندارد.
"""
from __future__ import annotations
import json
import os
import re
import logging
import time

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ReactionTypeType

BOT_TOKEN = "8727762178:AAGrdb5XFjhkcdoOEIFy1s8U71idRpN0DX8"
ADMIN_ID = 7530457395
DATA = "channel_react_emoji.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("creact")


def D():
    return {"posts": {}}


def load():
    if os.path.exists(DATA):
        try:
            with open(DATA, "r", encoding="utf-8") as f:
                d = json.load(f)
            d.setdefault("posts", {})
            return d
        except Exception as e:
            log.error(e)
    return D()


def save(d):
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def is_admin(uid):
    return int(uid) == ADMIN_ID


def pkey(chat_id, msg_id):
    return "%s:%s" % (chat_id, msg_id)


def parse_link(text):
    text = (text or "").strip()
    # https://t.me/name/81  یا  https://t.me/c/123/81
    m = re.search(r"(?:https?://)?t\.me/(c/)?([A-Za-z0-9_]+)/(\d+)", text)
    if not m:
        return None
    private = bool(m.group(1))
    part = m.group(2)
    msg_id = int(m.group(3))
    if private:
        return {
            "chat_id": int("-100" + part),
            "username": None,
            "message_id": msg_id,
            "raw": text,
        }
    return {
        "chat_id": None,
        "username": part,
        "message_id": msg_id,
        "raw": text,
    }


def build_emoji_link(username, chat_id, msg_id, custom_emoji_id):
    """
    فرمت درخواستی:
    https://t.me/v8xnem/81/5449590964865741724
    """
    if username:
        return "https://t.me/%s/%s/%s" % (username, msg_id, custom_emoji_id)
    # کانال خصوصی: از chat_id عددی
    cid = str(chat_id).replace("-100", "")
    return "https://t.me/c/%s/%s/%s" % (cid, msg_id, custom_emoji_id)


async def send_one_custom(bot, admin_id, username, chat_id, msg_id, custom_emoji_id):
    link = build_emoji_link(username, chat_id, msg_id, custom_emoji_id)
    try:
        stickers = await bot.get_custom_emoji_stickers(
            custom_emoji_ids=[str(custom_emoji_id)]
        )
    except Exception as e:
        await bot.send_message(
            admin_id,
            "آیدی: <code>%s</code>\n%s\n(استیکر گرفته نشد: %s)"
            % (custom_emoji_id, link, e),
            parse_mode="HTML",
        )
        return

    if not stickers:
        await bot.send_message(
            admin_id,
            "آیدی: <code>%s</code>\n%s\n(استیکری برنگشت)" % (custom_emoji_id, link),
            parse_mode="HTML",
        )
        return

    st = stickers[0]
    try:
        await bot.send_sticker(admin_id, sticker=st.file_id)
    except Exception:
        pass
    await bot.send_message(admin_id, link)


async def flush_customs(bot, post, custom_ids):
    """هر آیدی سفارشی جدا: عکس ریکشن + لینک"""
    username = post.get("username")
    chat_id = post.get("chat_id")
    msg_id = post.get("message_id")
    # تکراری نفرست
    sent = set(post.get("sent_ids") or [])
    new_ids = [str(i) for i in custom_ids if str(i) not in sent]
    if not new_ids:
        return

    await bot.send_message(
        ADMIN_ID,
        "یافت شد %d ریکشن سفارشی برای پست %s" % (len(new_ids), post.get("link") or ""),
    )
    for cid in new_ids:
        await send_one_custom(bot, ADMIN_ID, username, chat_id, msg_id, cid)
        sent.add(str(cid))
        post["sent_ids"] = list(sent)
        time.sleep(0.3)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(
        "بات را ادمین کانال کن، بعد لینک پست را بفرست.\n"
        "مثال:\nhttps://t.me/v8xnem/81\n\n"
        "برای هر ریکشن سفارشی می‌فرستد:\n"
        "• تصویر همان ریکشن\n"
        "• لینک: https://t.me/کانال/شماره‌پست/آیدی‌ایموجی"
    )


async def on_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not is_admin(update.effective_user.id):
        return
    parsed = parse_link(update.message.text or "")
    if not parsed:
        return

    bot = context.bot
    chat_id = parsed["chat_id"]
    username = parsed["username"]
    msg_id = parsed["message_id"]

    try:
        if username:
            chat = await bot.get_chat("@" + username)
            chat_id = chat.id
            title = chat.title or username
        else:
            chat = await bot.get_chat(chat_id)
            title = chat.title or str(chat_id)
            username = chat.username  # ممکن است None باشد
    except Exception as e:
        await update.message.reply_text("کانال در دسترس نیست:\n%s" % e)
        return

    try:
        await bot.forward_message(
            chat_id=update.effective_chat.id,
            from_chat_id=chat_id,
            message_id=msg_id,
        )
    except Exception as e:
        await update.message.reply_text("پست خوانده نشد (ادمین بودن لازم است):\n%s" % e)
        return

    d = load()
    key = pkey(chat_id, msg_id)
    d["posts"][key] = {
        "chat_id": chat_id,
        "message_id": msg_id,
        "username": username,
        "title": title,
        "link": parsed["raw"],
        "customs": {},
        "sent_ids": d.get("posts", {}).get(key, {}).get("sent_ids") or [],
        "added_at": time.time(),
    }
    save(d)

    await update.message.reply_text(
        "✅ پست ثبت شد: %s\n<code>%s</code>\n\n"
        "الان منتظر آپدیت ریکشن کانال می‌مانم.\n"
        "به محض تغییر تعداد ریکشن‌های سفارشی، برای هرکدام تصویر + لینک می‌فرستم.\n\n"
        "نکته تلگرام: خواندن یک‌جای ریکشن‌های قدیمی بدون تغییر جدید، در Bot API نیست."
        % (title, key),
        parse_mode="HTML",
    )


async def on_reaction_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mrc = update.message_reaction_count
    if not mrc:
        return

    key = pkey(mrc.chat.id, mrc.message_id)
    d = load()
    post = (d.get("posts") or {}).get(key)
    if not post:
        return

    custom_ids = []
    for rc in mrc.reactions or []:
        rt = rc.type
        kind = getattr(rt, "type", None)
        if kind == ReactionTypeType.CUSTOM_EMOJI or kind == "custom_emoji":
            cid = str(rt.custom_emoji_id)
            custom_ids.append(cid)
            post.setdefault("customs", {})[cid] = {
                "id": cid,
                "count": getattr(rc, "total_count", None),
                "ts": time.time(),
            }

    if not custom_ids:
        save(d)
        return

    save(d)
    await flush_customs(context.bot, post, custom_ids)
    save(d)


def main():
    if not BOT_TOKEN or BOT_TOKEN == "TOKEN_HERE":
        raise SystemExit("توکن را بگذار")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("t\\.me/"), on_link))

    try:
        from telegram.ext import MessageReactionCountHandler

        app.add_handler(MessageReactionCountHandler(on_reaction_count))
    except Exception as e:
        log.error("MessageReactionCountHandler: %s", e)

    log.info("channel custom reaction bot up")
    app.run_polling(allowed_updates=["message", "message_reaction_count"])


if __name__ == "__main__":
    main()
