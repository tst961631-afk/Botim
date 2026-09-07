import json, time, requests, random, re

# ================= تنظیمات =================
COORDINATOR_TOKEN = "8761842258:AAGp6cxmqrlbXZTFiF6RU4EOr-gsXbwZK4g"   # بات هماهنگ‌کننده

FOLLOWER_TOKENS = [
    "8890505737:AAHCt8FmiDlaT5hGIiXxa3pj11_0R5GVjac",
    "8958427977:AAGFPWYql56_cgZ0wsPgqSMeWrytUPT6vqM",
    "8980339158:AAH8MtFbMQavxEr0HsdY4kUNfYc8L2yWRuY",
    "8851361308:AAFmtRXBMAW7OHH0v-pnMeVj8Y2_F4ENH2E",
    "8919174930:AAHhdvSqNzgP1UiEJJGhmS6HmayVtKsKvK4",
    "8827103356:AAHbs0A7a9venSypA-CjibTmZN7oW4R5Mxk",
    "8760978965:AAEOhh0r9mc1ZV64BPXQFQcuJO3_fOmr9zI",
    "8601112865:AAEuqxcHFp0fZHB5BaRGJhjRj5rjkR-uhGE",
    "8583677941:AAFyCteQ2pv-A1TRDN0GvUuTPtpXNVV5aFg",
    "8884368754:AAE-7XxQe6ecfEuNE1XKXPU6ZSQBot2tBNc",
    "8820388007:AAHW63oqgv8A9WcUBBfQZhhJSyi_sgBl7T8",
    "8644116503:AAEfmzcNmEKIJUgk7T9cPe7Ul_MzPvpwUEg",
    "8940252712:AAFa9oVyWMq974b2G3-oNA_xUCJmtPVLBPw",
    "8441644287:AAHu_Iij3q9qRtb8cFIjM5T18TgQfJ1gjIM",
    "8952792178:AAEH127z6DhkiqD0NPGVrmTjCohGhNZeM8M",
    "8830714039:AAFFFUZbtmyYBGSjOzyg1zeetZ2QZ5YpYGk",
    "8855009435:AAGuhHjjHmBmj3vn6znVEOM41bGr7S2gEg4",
    "8626927371:AAG8t6lOuhtBAHhBm6eH-nF7ld1bCq5aDW0",
    "8842772718:AAHdhctPjjVLBWw_cpyvFKLjkuo0vHt3j6w",
    
]

FLOOD_DELAY = 0.15
# ===========================================

API = "https://api.telegram.org/bot{token}/{method}"
TOTAL_BOTS = 1 + len(FOLLOWER_TOKENS)

# {user_id: {"admin_chat":, "target_chat":, "target_msg":,
#            "plan": [(emoji, count یا None), ...], "panel_msg_id":}}
SESSIONS = {}

def call(token, method, **params):
    try:
        r = requests.post(API.format(token=token, method=method), data=params, timeout=30)
        return r.json()
    except Exception as e:
        return {"ok": False, "description": str(e)}

def send(chat_id, text, reply_to=None, keyboard=None):
    p = {"chat_id": chat_id, "text": text}
    if reply_to: p["reply_to_message_id"] = reply_to
    if keyboard: p["reply_markup"] = json.dumps(keyboard)
    return call(COORDINATOR_TOKEN, "sendMessage", **p)

def set_reaction(token, chat_id, msg_id, emoji):
    reaction = json.dumps([{"type": "emoji", "emoji": emoji}])
    return call(token, "setMessageReaction",
                chat_id=chat_id, message_id=msg_id,
                reaction=reaction, is_big=False)

# ---------------- پنل ----------------

def plan_text(st):
    if not st["plan"]:
        return "هیچ ریاکشنی ثبت نشده."
    lines = []
    for i, (e, c) in enumerate(st["plan"], 1):
        lines.append(f"{i}. {e} → {'همه‌ی باقی‌مانده' if c is None else str(c) + ' بات'}")
    return "\n".join(lines)

def reserved_count(st):
    return sum(c for _, c in st["plan"] if c is not None)

def panel_keyboard():
    return {"inline_keyboard": [
        [{"text": "▶️ اجرا", "callback_data": "run"},
         {"text": "🗑 پاک کردن", "callback_data": "clear"}],
    ]}

def refresh_panel(st):
    if "panel_msg_id" not in st:
        return
    used = reserved_count(st)
    remain = TOTAL_BOTS - used
    text = (f"🎯 پنل ریاکشن\n\n"
            f"پست: #{st['target_msg']}\n"
            f"بات‌ها: {used} رزرو شده | {remain} باقی‌مانده\n\n"
            f"{plan_text(st)}\n\n"
            "دستور جدید: /react ❤️ 5  یا  /react 🔥⚡ (رندوم)")
    call(COORDINATOR_TOKEN, "editMessageText",
         chat_id=st["admin_chat"], message_id=st["panel_msg_id"],
         text=text, reply_markup=json.dumps(panel_keyboard()))

# ---------------- اجرا ----------------

def build_jobs(st):
    """ساخت لیست (توکن، ایموجی) از صف"""
    tokens = [COORDINATOR_TOKEN] + FOLLOWER_TOKENS
    jobs = []
    idx = 0
    leftovers = []   # ایموجیهای بدون تعداد -> رندوم بین بقیه

    for emoji, count in st["plan"]:
        if count is not None:
            for _ in range(count):
                if idx < len(tokens):
                    jobs.append((tokens[idx], emoji))
                    idx += 1
        else:
            leftovers.append(emoji)

    # باتهای باقی‌مانده بین ایموجیهای بدون تعداد تقسیم میشه
    while idx < len(tokens) and leftovers:
        jobs.append((tokens[idx], random.choice(leftovers)))
        idx += 1

    random.shuffle(jobs)   # ترتیب باتها رندوم
    return jobs

def run_all(st, chat_id, reply_to=None):
    jobs = build_jobs(st)
    if not jobs:
        send(chat_id, "هیچ ریاکشنی برای اجرا نیست.", reply_to=reply_to)
        return
    send(chat_id, "در حال اجرا... ⏳", reply_to=reply_to)

    ok, fail = 0, []
    for token, emoji in jobs:
        res = set_reaction(token, st["target_chat"], st["target_msg"], emoji)
        tag = token[-6:]
        if res.get("ok"):
            ok += 1
            print(f"[OK]   bot ...{tag} -> {emoji}")
        else:
            fail.append(tag)
            print(f"[FAIL] bot ...{tag} -> {res.get('description','')[:120]}")
        time.sleep(FLOOD_DELAY)

    from collections import Counter
    summary = " | ".join(f"{e} ×{c}" for e, c in Counter(e for _, e in jobs).items())
    send(chat_id,
         f"تمام شد ✅ {ok} بات ریاکشن زدند.\n{summary}"
         + (f"\nناموفق: {', '.join(fail)}" if fail else ""),
         reply_to=reply_to)

# ---------------- دستورها ----------------

def handle_message(msg):
    chat_id = msg["chat"]["id"]
    uid = msg["from"]["id"]
    text = msg.get("text", "").strip()

    if text.startswith("/start"):
        send(chat_id,
             "سلام 👋 راهنما:\n\n"
             "۱) پست کانال رو فوروارد کن اینجا.\n"
             "۲) روی همون پیام /panel بفرست.\n"
             "۳) بعد دستورهای ریاکشن رو بفرست:\n"
             "   /react 👾 5  ← فقط ۵ بات\n"
             "   /react 💯    ← همه‌ی بات‌های باقی‌مانده\n"
             "   /react 🔥⚡❤️ ← باقی‌مانده‌ها رندوم بین اینا تقسیم می‌شن\n"
             "۴) تو پنل دکمه‌ی «▶️ اجرا» رو بزن.")
        return

    # --- ثبت پنل ---
    if text.startswith("/panel"):
        reply = msg.get("reply_to_message")
        if not reply or "forward_from_chat" not in reply:
            send(chat_id, "اول پست کانال رو فوروارد کن، بعد روی همون پیام /panel بفرست.",
                 reply_to=msg["message_id"])
            return
        fmsg = reply.get("forward_from_message_id")
        if not fmsg:
            send(chat_id, "این فوروارد از کانال نیست.", reply_to=msg["message_id"])
            return
        st = SESSIONS.setdefault(uid, {"plan": []})
        st.update({"admin_chat": chat_id,
                   "target_chat": reply["forward_from_chat"].get("id"),
                   "target_msg": fmsg,
                   "panel_msg_id": None})
        r = send(chat_id, "پنل ریاکشن ✅", reply_to=msg["message_id"],
                 keyboard=panel_keyboard())
        if r.get("ok"):
            st["panel_msg_id"] = r["result"]["message_id"]
        return

    # --- افزودن به صف ---
    if text.startswith("/react"):
        st = SESSIONS.get(uid)
        if not st or "target_chat" not in st:
            send(chat_id, "اول با /panel روی پست فورواردشده، پنل رو باز کن.",
                 reply_to=msg["message_id"])
            return
        argstr = text[len("/react"):].strip()
        if not argstr:
            send(chat_id, "مثلاً: /react 👾 5", reply_to=msg["message_id"])
            return

        tokens_list = argstr.split()
        added = []
        pending = None
        for tok in tokens_list:
            m = re.match(r"^(.*?)(\d+)$", tok, re.S)
            if m and m.group(1):                      # ایموجی+عدد مثل 👾5
                added.append((m.group(1), int(m.group(2))))
                pending = None
            elif m and not m.group(1) and pending:    # عددِ تنها بعد از ایموجی قبلی
                added.append((pending, int(m.group(2))))
                pending = None
            else:                                     # فقط ایموجی
                added.append((tok, None))
                pending = tok

        # چک سقف باتها
        free = TOTAL_BOTS - reserved_count(st)
        need = sum(c for _, c in added if c is not None)
        if need > free:
            send(chat_id, f"⚠️ فقط {free} بات خالیه ولی {need} بات خواستی.",
                 reply_to=msg["message_id"])
            return

        st["plan"].extend(added)
        send(chat_id, "ثبت شد ✅", reply_to=msg["message_id"])
        refresh_panel(st)
        return

def handle_callback(cb):
    uid = cb["from"]["id"]
    data = cb.get("data", "")
    msg = cb.get("message", {})
    chat_id = msg["chat"]["id"]

    st = SESSIONS.get(uid)
    if not st or "target_chat" not in st:
        call(COORDINATOR_TOKEN, "answerCallbackQuery",
             callback_query_id=cb["id"], text="اول /panel رو روی پست بزن.")
        return

    if data == "clear":
        st["plan"] = []
        refresh_panel(st)
        call(COORDINATOR_TOKEN, "answerCallbackQuery",
             callback_query_id=cb["id"], text="پاک شد ✅")
    elif data == "run":
        call(COORDINATOR_TOKEN, "answerCallbackQuery", callback_query_id=cb["id"])
        run_all(st, chat_id, reply_to=msg["message_id"])

def main():
    offset = 0
    print(f"هماهنگ‌کننده فعال — {TOTAL_BOTS} بات آماده. (Ctrl+C برای خروج)")
    while True:
        try:
            r = call(COORDINATOR_TOKEN, "getUpdates",
                     offset=offset, timeout=30,
                     allowed_updates=json.dumps(["message", "callback_query"]))
            if not r.get("ok"):
                print("getUpdates error:", r.get("description"))
                time.sleep(2)
                continue
            for upd in r.get("result", []):
                offset = upd["update_id"] + 1
                if "callback_query" in upd:
                    try: handle_callback(upd["callback_query"])
                    except Exception as e: print("cb error:", e)
                elif "message" in upd:
                    m = upd["message"]
                    if m.get("text", "").startswith("/"):
                        try: handle_message(m)
                        except Exception as e: print("error:", e)
        except KeyboardInterrupt:
            print("\nخروج.")
            break
        except Exception as e:
            print("polling error:", e)
            time.sleep(2)

if __name__ == "__main__":
    main()
