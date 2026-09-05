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
USERS_FILE = "users.json"

# ==============================
# تنظیمات پیش‌فرض
# ==============================

settings = {
    "text": "🎉 رویداد ما به‌زودی شروع می‌شود!",
    "start_time": None,
    "end_time": None
}

users = {}

# ==============================
# بارگذاری تنظیمات
# ==============================

def load_settings():

    global settings

    if os.path.exists(DATA_FILE):

        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                settings = json.load(f)

        except Exception as e:
            print("Settings error:", e)


# ==============================
# ذخیره تنظیمات
# ==============================

def save_settings():

    with open(DATA_FILE, "w", encoding="utf-8") as f:

        json.dump(
            settings,
            f,
            ensure_ascii=False,
            indent=4
        )


# ==============================
# بارگذاری کاربران
# ==============================

def load_users():

    global users

    if os.path.exists(USERS_FILE):

        try:

            with open(
                USERS_FILE,
                "r",
                encoding="utf-8"
            ) as f:

                users = json.load(f)

        except Exception as e:

            print("Users error:", e)
            users = {}


# ==============================
# ذخیره کاربران
# ==============================

def save_users():

    with open(
        USERS_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            users,
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

def send_message(
    chat_id,
    text,
    reply_markup=None
):

    data = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup:
        data["reply_markup"] = json.dumps(
            reply_markup,
            ensure_ascii=False
        )

    result = api(
        "sendMessage",
        data
    )

    if result and result.get("ok"):

        return result["result"]["message_id"]

    return None


# ==============================
# ویرایش پیام
# ==============================

def edit_message(
    chat_id,
    message_id,
    text
):

    return api(
        "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text
        }
    )


# ==============================
# ثبت کاربر
# ==============================

def register_user(message):

    user = message.get("from", {})

    user_id = user.get("id")

    if not user_id:
        return

    username = user.get("username")
    first_name = user.get("first_name", "")

    users[str(user_id)] = {
        "username": username,
        "first_name": first_name,
        "started_at": time.time()
    }

    save_users()


# ==============================
# نام کاربر
# ==============================

def get_user_name(data):

    username = data.get("username")

    if username:
        return f"@{username}"

    return data.get(
        "first_name",
        "بدون یوزرنیم"
    )


# ==============================
# نوار پیشرفت
# ==============================

def progress_bar(
    percent,
    size=20
):

    percent = max(
        0,
        min(100, percent)
    )

    filled = int(
        size * percent / 100
    )

    empty = size - filled

    return (
        "█" * filled
        +
        "░" * empty
    )


# ==============================
# زمان باقی‌مانده
# ==============================

def get_time_left():

    end = settings.get("end_time")

    if not end:
        return None

    return int(
        end - time.time()
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
# متن اصلی
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

    # زمان تنظیم نشده
    if not end:

        return (
            f"{custom_text}\n\n"
            "⏳ زمان هنوز تنظیم نشده است."
        )

    now = time.time()

    # قبل از شروع
    if start and now < start:

        remaining = int(
            start - now
        )

        return (
            f"{custom_text}\n\n"
            "⏳ شروع بازی:\n"
            f"{format_time(remaining)}"
        )

    # پایان
    if now >= end:

        return (
            f"{custom_text}\n\n"
            "🧟‍♂️ بازی شروع شد!\n\n"
            "████████████████████ 100%"
        )

    # درصد
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
# شمارش معکوس
# هر دقیقه آپدیت می‌شود
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

        if (
            remaining is not None
            and remaining <= 0
        ):

            break

        # 60 ثانیه
        time.sleep(60)


# ==============================
# دکمه‌های پنل ادمین
# ==============================

def admin_keyboard():

    return {

        "inline_keyboard": [

            [
                {
                    "text": "📝 تغییر متن",
                    "callback_data": "set_text",
                    "style": "success"
                },

                {
                    "text": "⏰ تنظیم زمان",
                    "callback_data": "set_time",
                    "style": "success"
                }
            ],

            [
                {
                    "text": "▶️ شروع شمارش",
                    "callback_data": "start_time",
                    "style": "success"
                },

                {
                    "text": "⚙️ تنظیمات",
                    "callback_data": "settings",
                    "style": "success"
                }
            ],

            [
                {
                    "text": "👥 کاربران",
                    "callback_data": "users",
                    "style": "success"
                },

                {
                    "text": "🗑 پاک کردن زمان",
                    "callback_data": "clear",
                    "style": "danger"
                }
            ]

        ]
    }


# ==============================
# پنل ادمین
# ==============================

def show_admin_panel(chat_id):

    text = (
        "⚙️ پنل مدیریت ربات\n\n"
        "از دکمه‌های زیر برای تنظیم ربات استفاده کن:"
    )

    send_message(
        chat_id,
        text,
        admin_keyboard()
    )


# ==============================
# پاسخ به Callback
# ==============================

def answer_callback(callback_id):

    api(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id
        }
    )


# ==============================
# پردازش دکمه‌ها
# ==============================

def handle_callback(callback):

    callback_id = callback["id"]

    message = callback.get(
        "message",
        {}
    )

    chat_id = message.get(
        "chat",
        {}
    ).get(
        "id"
    )

    data = callback.get(
        "data"
    )

    answer_callback(
        callback_id
    )

    if chat_id != ADMIN_ID:
        return

    # تغییر متن
    if data == "set_text":

        send_message(
            chat_id,
            "📝 برای تغییر متن، این دستور را بفرست:\n\n"
            "/text متن دلخواه"
        )

        return

    # تنظیم زمان
    if data == "set_time":

        send_message(
            chat_id,
            "⏰ زمان پایان را این‌طور وارد کن:\n\n"
            "/time YYYY-MM-DD HH:MM\n\n"
            "مثال:\n"
            "/time 2026-09-06 20:00"
        )

        return

    # شروع شمارش
    if data == "start_time":

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
            "▶️ شمارش از همین لحظه شروع شد."
        )

        return

    # تنظیمات
    if data == "settings":

        show_settings(
            chat_id
        )

        return

    # کاربران
    if data == "users":

        send_users_list(
            chat_id
        )

        return

    # پاک کردن
    if data == "clear":

        settings["start_time"] = None
        settings["end_time"] = None

        save_settings()

        send_message(
            chat_id,
            "🗑 زمان با موفقیت پاک شد."
        )

        return


# ==============================
# نمایش تنظیمات
# ==============================

def show_settings(chat_id):

    start = settings.get(
        "start_time"
    )

    end = settings.get(
        "end_time"
    )

    if start:

        start_text = datetime.fromtimestamp(
            start
        ).strftime(
            "%Y-%m-%d %H:%M"
        )

    else:

        start_text = "تنظیم نشده"

    if end:

        end_text = datetime.fromtimestamp(
            end
        ).strftime(
            "%Y-%m-%d %H:%M"
        )

    else:

        end_text = "تنظیم نشده"

    send_message(

        chat_id,

        "⚙️ تنظیمات فعلی\n\n"

        f"📝 متن:\n"
        f"{settings['text']}\n\n"

        f"▶️ شروع:\n"
        f"{start_text}\n\n"

        f"🏁 پایان:\n"
        f"{end_text}\n\n"

        f"👥 کاربران ثبت‌شده:\n"
        f"{len(users)}"
    )


# ==============================
# لیست کاربران
# ==============================

def send_users_list(chat_id):

    if not users:

        send_message(
            chat_id,
            "👥 هنوز کسی ربات را استارت نکرده است."
        )

        return

    user_list = list(
        users.items()
    )

    user_list.sort(
        key=lambda x:
        x[1].get(
            "started_at",
            0
        ),
        reverse=True
    )

    text = (
        f"👥 کاربران استارت‌کننده\n\n"
        f"📊 تعداد: {len(user_list)}\n\n"
    )

    for index, (
        user_id,
        data
    ) in enumerate(
        user_list,
        start=1
    ):

        name = get_user_name(
            data
        )

        text += (
            f"{index}. {name}\n"
        )

        # جلوگیری از رد شدن محدودیت تلگرام
        if len(text) > 3500:

            send_message(
                chat_id,
                text
            )

            text = ""

    if text:

        send_message(
            chat_id,
            text
        )


# ==============================
# پردازش پیام
# ==============================

def handle_message(message):

    chat_id = message[
        "chat"
    ]["id"]

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

        register_user(
            message
        )

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

    # پنل
    if text == "/admin":

        show_admin_panel(
            chat_id
        )

        return

    # آمار/تعداد کاربران
    if text == "/stats":

        send_message(
            chat_id,

            "📊 آمار ربات\n\n"
            f"👥 کل کاربران استارت‌کننده: {len(users)}"
        )

        return

    # لیست کاربران
    if text == "/users":

        send_users_list(
            chat_id
        )

        return

    # ==========================
    # تغییر متن
    # ==========================

    if text.startswith(
        "/text "
    ):

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
    # زمان
    # ==========================

    if text.startswith(
        "/time "
    ):

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
                "/time 2026-09-06 20:00"
            )

        return

    # ==========================
    # شروع شمارش
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
            "▶️ شمارش از همین لحظه شروع شد."
        )

        return

    # ==========================
    # تنظیمات
    # ==========================

    if text == "/settings":

        show_settings(
            chat_id
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
            "🗑 زمان پاک شد."
        )

        return


# ==============================
# دریافت آپدیت
# ==============================

def get_updates(offset=None):

    data = {
        "timeout": 30,
        "allowed_updates": json.dumps([
            "message",
            "callback_query"
        ])
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
    load_users()

    print("🤖 Bot Started")
    print(
        f"👥 Users: {len(users)}"
    )

    offset = None

    while True:

        try:

            result = get_updates(
                offset
            )

            if not result:

                time.sleep(2)
                continue

            if not result.get(
                "ok"
            ):

                time.sleep(3)
                continue

            for update in result.get(
                "result",
                []
            ):

                offset = (
                    update["update_id"] + 1
                )

                # پیام
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

                # دکمه
                if "callback_query" in update:

                    try:

                        handle_callback(
                            update["callback_query"]
                        )

                    except Exception as e:

                        print(
                            "Callback error:",
                            e
                        )

        except KeyboardInterrupt:

            print(
                "Bot stopped."
            )

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
