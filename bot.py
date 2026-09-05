import requests
import json
import time
import threading
import os
from datetime import datetime

# ==============================
# تنظیمات
# ==============================

TOKEN = "8975007734:AAFGsTyR56CLHJnr7ZFgz8DMAs2INlg1Qfc"
ADMIN_ID = 7530457395

API = f"https://api.telegram.org/bot{TOKEN}"
DATA_FILE = "settings.json"

# ==============================
# تنظیمات پیش‌فرض
# ==============================

settings = {
    "text": "🎉 رویداد ما به‌زودی شروع می‌شود!",
    "start_time": None,
    "end_time": None
}

# ==============================
# بارگذاری تنظیمات
# ==============================

def load_settings():
    global settings

    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                settings = json.load(f)
        except:
            pass


def save_settings():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(
            settings,
            f,
            ensure_ascii=False,
            indent=4
        )


# ==============================
# Telegram API
# ==============================

def api(method, data=None):

    try:
        r = requests.post(
            f"{API}/{method}",
            data=data,
            timeout=30
        )

        return r.json()

    except Exception as e:
        print("API Error:", e)
        return None


# ==============================
# ارسال پیام
# ==============================

def send_message(chat_id, text):

    result = api(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text
        }
    )

    if result and result.get("ok"):
        return result["result"]["message_id"]

    return None


# ==============================
# ویرایش پیام
# ==============================

def edit_message(chat_id, message_id, text):

    return api(
        "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text
        }
    )


# ==============================
# ساخت نوار پیشرفت
# ==============================

def progress_bar(percent, size=20):

    percent = max(
        0,
        min(100, percent)
    )

    filled = int(
        size * percent / 100
    )

    empty = size - filled

    return "█" * filled + "░" * empty


# ==============================
# محاسبه زمان باقی‌مانده
# ==============================

def get_time_left():

    if not settings["end_time"]:
        return None

    end = datetime.fromtimestamp(
        settings["end_time"]
    )

    now = datetime.now()

    seconds = int(
        (end - now).total_seconds()
    )

    return seconds


# ==============================
# ساخت متن اصلی
# ==============================

def make_text():

    start = settings.get(
        "start_time"
    )

    end = settings.get(
        "end_time"
    )

    custom_text = settings.get(
        "text",
        ""
    )

    if not end:

        return (
            f"{custom_text}\n\n"
            "⏳ زمان هنوز تنظیم نشده است."
        )

    now = time.time()

    # ==========================
    # قبل از شروع
    # ==========================

    if start and now < start:

        remaining = int(
            start - now
        )

        return (
            f"{custom_text}\n\n"
            "⏳ زمان شروع:\n"
            f"{format_time(remaining)}"
        )

    # ==========================
    # بعد از پایان
    # ==========================

    if now >= end:

        return (
            f"{custom_text}\n\n"
            "✅ زمان به پایان رسید!\n\n"
            "████████████████████ 100%"
        )

    # ==========================
    # محاسبه درصد
    # ==========================

    if start:
        total = end - start
        passed = now - start

        percent = (
            passed / total
        ) * 100

    else:
        percent = 0

    remaining = int(
        end - now
    )

    bar = progress_bar(
        percent
    )

    return (
        f"{custom_text}\n\n"
        "⏳ زمان باقی‌مانده:\n"
        f"{format_time(remaining)}\n\n"
        f"{bar} {int(percent)}%"
    )


# ==============================
# فرمت زمان
# ==============================

def format_time(seconds):

    seconds = max(
        0,
        int(seconds)
    )

    days = seconds // 86400
    seconds %= 86400

    hours = seconds // 3600
    seconds %= 3600

    minutes = seconds // 60
    seconds %= 60

    result = []

    if days:
        result.append(
            f"{days} روز"
        )

    if hours:
        result.append(
            f"{hours} ساعت"
        )

    if minutes:
        result.append(
            f"{minutes} دقیقه"
        )

    if seconds or not result:
        result.append(
            f"{seconds} ثانیه"
        )

    return " و ".join(result)


# ==============================
# اجرای شمارش معکوس
# ==============================

def countdown(
    chat_id,
    message_id
):

    while True:

        text = make_text()

        edit_message(
            chat_id,
            message_id,
            text
        )

        remaining = get_time_left()

        if remaining is not None and remaining <= 0:
            break

        time.sleep(1)


# ==============================
# راهنمای ادمین
# ==============================

def admin_help(chat_id):

    send_message(
        chat_id,

        "⚙️ پنل تنظیمات ادمین\n\n"

        "📝 تغییر متن:\n"
        "/text متن دلخواه\n\n"

        "⏰ تنظیم زمان:\n"
        "/time YYYY-MM-DD HH:MM\n\n"

        "مثال:\n"
        "/time 2026-09-10 20:30\n\n"

        "▶️ شروع شمارش از همین لحظه:\n"
        "/starttime\n\n"

        "📌 مشاهده تنظیمات:\n"
        "/settings\n\n"

        "❌ پاک کردن زمان:\n"
        "/clear"
    )


# ==============================
# پردازش پیام
# ==============================

def handle_message(message):

    chat_id = message["chat"]["id"]

    text = message.get(
        "text",
        ""
    ).strip()

    if not text:
        return

    # ==========================
    # START
    # ==========================

    if text == "/start":

        message_id = send_message(
            chat_id,
            make_text()
        )

        if message_id:

            thread = threading.Thread(
                target=countdown,
                args=(
                    chat_id,
                    message_id
                ),
                daemon=True
            )

            thread.start()

        return

    # ==========================
    # فقط ادمین
    # ==========================

    if chat_id != ADMIN_ID:
        return

    # ==========================
    # راهنما
    # ==========================

    if text == "/admin":

        admin_help(
            chat_id
        )

        return

    # ==========================
    # تغییر متن
    # ==========================

    if text.startswith("/text "):

        new_text = text[6:].strip()

        if not new_text:

            send_message(
                chat_id,
                "❌ متن خالی است."
            )

            return

        settings["text"] = new_text

        save_settings()

        send_message(
            chat_id,
            "✅ متن با موفقیت تغییر کرد."
        )

        return

    # ==========================
    # تنظیم زمان پایان
    # ==========================

    if text.startswith("/time "):

        value = text[6:].strip()

        try:

            dt = datetime.strptime(
                value,
                "%Y-%m-%d %H:%M"
            )

            settings["end_time"] = (
                dt.timestamp()
            )

            save_settings()

            send_message(
                chat_id,

                "✅ زمان پایان تنظیم شد.\n\n"
                f"📅 {value}"
            )

        except:

            send_message(
                chat_id,

                "❌ فرمت اشتباه است.\n\n"
                "مثال:\n"
                "/time 2026-09-10 20:30"
            )

        return

    # ==========================
    # شروع از همین لحظه
    # ==========================

    if text == "/starttime":

        if not settings.get(
            "end_time"
        ):

            send_message(
                chat_id,
                "❌ اول زمان پایان را تنظیم کن."
            )

            return

        settings["start_time"] = time.time()

        save_settings()

        send_message(
            chat_id,
            "▶️ شمارش معکوس از همین لحظه شروع شد."
        )

        return

    # ==========================
    # نمایش تنظیمات
    # ==========================

    if text == "/settings":

        start = settings.get(
            "start_time"
        )

        end = settings.get(
            "end_time"
        )

        start_text = (
            datetime.fromtimestamp(start)
            .strftime("%Y-%m-%d %H:%M")
            if start
            else "تنظیم نشده"
        )

        end_text = (
            datetime.fromtimestamp(end)
            .strftime("%Y-%m-%d %H:%M")
            if end
            else "تنظیم نشده"
        )

        send_message(
            chat_id,

            "⚙️ تنظیمات فعلی:\n\n"
            f"📝 متن:\n{settings['text']}\n\n"
            f"▶️ شروع:\n{start_text}\n\n"
            f"🏁 پایان:\n{end_text}"
        )

        return

    # ==========================
    # پاک کردن
    # ==========================

    if text == "/clear":

        settings["start_time"] = None
        settings["end_time"] = None

        save_settings()

        send_message(
            chat_id,
            "🗑 تنظیمات زمان پاک شد."
        )

        return


# ==============================
# دریافت آپدیت
# ==============================

def get_updates(offset=None):

    data = {
        "timeout": 30
    }

    if offset is not None:
        data["offset"] = offset

    return api(
        "getUpdates",
        data
    )


# ==============================
# MAIN
# ==============================

def main():

    load_settings()

    print("🤖 Bot Started")

    offset = None

    while True:

        try:

            result = get_updates(
                offset
            )

            if not result:
                time.sleep(2)
                continue

            if not result.get("ok"):
                time.sleep(3)
                continue

            for update in result.get(
                "result",
                []
            ):

                offset = (
                    update["update_id"] + 1
                )

                if "message" in update:

                    try:

                        handle_message(
                            update["message"]
                        )

                    except Exception as e:

                        print(
                            "Message error:",
                            e
                        )

        except KeyboardInterrupt:

            print("Bot stopped.")
            break

        except Exception as e:

            print(
                "Main error:",
                e
            )

            time.sleep(3)


# ==============================
# START
# ==============================

if __name__ == "__main__":
    main()
