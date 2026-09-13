# -*- coding: utf-8 -*-
"""
خروجی شبیه ربات ریافت آیدی ریکشن پرمیوم:
بدون فوروارد پست
برای هر ریکشن سفارشی:
  فایل webp
  کپشن:
    Premium Emoji
    Count: N
    https://t.me/channel/msg/emoji_id
"""
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

BOT_TOKEN = "8727762178:AAGrdb5XFjhkcdoOEIFy1s8U71idRpN0DX8"
ADMIN_ID = 7530457395
DATA = "channel_react_emoji.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("creact")


def load():
    if os.path.exists(DATA):
        try:
            with open(DATA, "r", encoding="utf-8") as f:
                d = json.load(f)
            d.setdefault("posts", {})
            return d
        except Exception:
            pass
    return {"posts": {}}


def save(d):
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def is_admin(uid):
    return int(uid) == ADMIN_ID


def pkey(chat_id, msg_id):
    return "%s:%s" % (chat_id, msg_id)


def parse_link(text):
    text = (text or "").strip()
    m = re.search(r"(?:https?://)?t\.me/(c/)?([A-Za-z0-9_]+)/(\d+)", text)
    if not m:
        return None
    private = bool(m.group(1))
    part = m.group(2)
    msg_id = int(m.group(3))
    if private:
        return {"chat_id": int("-100" + part), "username": None, "message_id": msg_id, "raw": text}
    return {"chat_id": None, "username": part, "message_id": msg_id, "raw": text}


def build_link(username, chat_id, msg_id, emoji_id):
    if username:
        return "https://t.me/%s/%s/%s" % (username, msg_id, emoji_id)
    cid = str(chat_id).replace("-100", "")
    return "https://t.me/c/%s/%s/%s" % (cid, msg_id, emoji_id)


async def send_premium_item(bot, admin_id, username, chat_id, msg_id, emoji_id, count):
    link = build_link(username, chat_id, msg_id, emoji_id)
    caption = (
        "🎭 Premium Emoji\n"
        "📊 Count: %s\n\n"
        "%s" % (count if count is not None else "?", link)
    )

    stickers = []
    try:
        stickers = await bot.get_custom_emoji_stickers(custom_emoji_ids=[str(emoji_id)])
    except Exception as e:
        await bot.send_message(admin_id, caption + "\n\n(استیکر: %s)" % e)
        return

    if not stickers:
        await bot.send_message(admin_id, caption + "\n\n(فایلی برنگشت)")
        return

    st = stickers[0]
    # تلاش: فایل webp با اسم آیدی
    try:
        f = await bot.get_file(st.file_id)
        path = "/tmp/%s.webp" % emoji_id
        await f.download_to_drive(path)
        with open(path, "rb") as fp:
            await bot.send_document(
                admin_id,
                document=fp,
                filename="%s.webp" % emoji_id,
                caption=caption,
            )
        try:
            os.remove(path)
        except Exception:
            pass
        return
    except Exception:
        pass

    # فال‌بک: استیکر
    try:
        await bot.send_sticker(admin_id, sticker=st.file_id)
        await bot.send_message(admin_id, caption)
    except Exception as e:
        await bot.send_message(admin_id, caption + "\n\n" + str(e))


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(
        "لینک پست کانال را بفرست (بات ادمین باشد).\n"
        "مثال: https://t.me/v8xnem/81\n\n"
        "برای هر ریکشن سفارشی می‌فرستد:\n"
        "فایل webp + Count + لینک آیدی"
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
            username = getattr(chat, "username", None)
    except Exception as e:
        await update.message.reply_text("کانال در دسترس نیست:\n%s" % e)
        return

    # فقط چک دسترسی — پست را برای کاربر فوروارد نکن
    try:
        await bot.get_chat(chat_id)
    except Exception as e:
        await update.message.reply_text("دسترسی ندارم:\n%s" % e)
        return

    d = load()
    key = pkey(chat_id, msg_id)
    prev = d.get("posts", {}).get(key, {})
    d["posts"][key] = {
        "chat_id": chat_id,
        "message_id": msg_id,
        "username": username,
        "title": title,
        "link": parsed["raw"],
        "sent_ids": prev.get("sent_ids") or [],
        "added_at": time.time(),
    }
    save(d)

    await update.message.reply_text(
        "✅ ثبت شد (بدون فوروارد پست)\n"
        "کانال: %s\n"
        "<code>%s</code>\n\n"
        "با تغییر ریکشن سفارشی، webp + Count + لینک می‌آید."
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

    sent = set(post.get("sent_ids") or [])
    username = post.get("username")
    chat_id = post.get("chat_id")
    msg_id = post.get("message_id")

    for rc in mrc.reactions or []:
        rt = rc.type
        kind = str(getattr(rt, "type", "") or "")
        if kind != "custom_emoji":
            continue
        cid = str(rt.custom_emoji_id)
        count = getattr(rc, "total_count", None)
        # هر بار با count به‌روز بفرست؛ برای جلوگیری از اسپم فقط اگر جدید یا count عوض
        prev_map = post.get("last_counts") or {}
        if cid in sent and prev_map.get(cid) == count:
            continue
        await send_premium_item(
            context.bot, ADMIN_ID, username, chat_id, msg_id, cid, count
        )
        sent.add(cid)
        prev_map[cid] = count
        post["sent_ids"] = list(sent)
        post["last_counts"] = prev_map
        save(d)
        time.sleep(0.25)

    save(d)


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("t\\.me/"), on_link))
    try:
        from telegram.ext import MessageReactionCountHandler
        app.add_handler(MessageReactionCountHandler(on_reaction_count))
    except Exception as e:
        log.error("no MessageReactionCountHandler: %s", e)
    log.info("up")
    app.run_polling(allowed_updates=["message", "message_reaction_count"])


if __name__ == "__main__":
    main()
