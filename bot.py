# -*- coding: utf-8 -*-
import logging
import re

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.constants import MessageEntityType, ParseMode
from telegram.request import HTTPXRequest

BOT_TOKEN = "8727762178:AAGrdb5XFjhkcdoOEIFy1s8U71idRpN0DX8"

# اگر پروکسی داری، مثلاً:
# PROXY = "http://127.0.0.1:10809"
PROXY = None

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("premium_emoji")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "آیدی عددی ایموجی پرمیوم را بفرست\n"
        "یا یک پیام حاوی ایموجی پرمیوم بفرست."
    )


def extract_ids_from_message(message):
    ids = []
    if not message or not message.entities:
        return ids
    for ent in message.entities:
        if ent.type == MessageEntityType.CUSTOM_EMOJI and ent.custom_emoji_id:
            ids.append(str(ent.custom_emoji_id))
    return ids


async def send_custom_emoji(update, emoji_id, fallback="⭐"):
    html = '<tg-emoji emoji-id="' + str(emoji_id) + '">' + fallback + "</tg-emoji>"
    await update.message.reply_text(
        html + "\n\n<code>" + str(emoji_id) + "</code>",
        parse_mode=ParseMode.HTML,
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    text = (msg.text or "").strip()
    if text.startswith("/"):
        return

    ids = extract_ids_from_message(msg)
    if ids:
        for eid in ids:
            try:
                await send_custom_emoji(update, eid)
            except Exception as e:
                await msg.reply_text("خطا: " + str(e))
        return

    if re.fullmatch(r"\d{5,25}", text):
        try:
            await send_custom_emoji(update, text)
        except Exception as e:
            await msg.reply_text("ارسال نشد: " + str(e))
        return

    await msg.reply_text("آیدی عددی بفرست یا پیام با ایموجی پرمیوم.")


def main():
    if not BOT_TOKEN or BOT_TOKEN == "TOKEN_HERE":
        raise SystemExit("توکن را در BOT_TOKEN بگذار")

    request = HTTPX
