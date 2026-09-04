import os
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from openai import OpenAI


# دریافت اطلاعات از Railway Variables
TELEGRAM_TOKEN = os.getenv("8975007734:AAFGsTyR56CLHJnr7ZFgz8DMAs2INlg1Qfc")
OPENAI_API_KEY = os.getenv("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")

if not TELEGRAM_TOKEN:
    raise ValueError("❌ BOT_TOKEN در Railway تنظیم نشده!")

if not OPENAI_API_KEY:
    raise ValueError("❌ OPENAI_API_KEY در Railway تنظیم نشده!")

client = OpenAI(api_key=OPENAI_API_KEY)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 سلام!\n\n"
        "من به هوش مصنوعی متصل هستم.\n"
        "هر چیزی می‌خوای بپرس."
    )


async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    try:
        response = client.responses.create(
            model="gpt-5-mini",
            input=text
        )

        answer = response.output_text

        await update.message.reply_text(answer)

    except Exception as e:
        print("OpenAI ERROR:", e)
        await update.message.reply_text(
            "❌ خطایی هنگام دریافت پاسخ رخ داد."
        )


def main():
    print("🤖 Bot starting...")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            chat
        )
    )

    print("✅ Bot is online!")

    app.run_polling()


if __name__ == "__main__":
    main()
