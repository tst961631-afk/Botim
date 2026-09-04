# -*- coding: utf-8 -*-
"""ربات انیمیشن ایموجی و متن — ادیت همان پیام + پنل ادمین رنگی"""
import json
import logging
import os
import asyncio
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ChatType

BOT_TOKEN = "8975007734:AAFGsTyR56CLHJnr7ZFgz8DMAs2INlg1Qfc"
ADMIN_ID = 7530457395
DATA_FILE = "emoji_bot_data.json"
STATE_TTL = 120

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("emojibot")


def default_data():
    return {
        "settings": {
            "mode": "replace",  # replace | join
            "frame_sec": 0.7,
            "loops": 3,
        },
        "emojis": {},  # cmd -> {frames: [], style, title}
        "texts": {},   # cmd -> {frames: [], style, title, frame_sec}
    }


def load():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            base = default_data()
            for k, v in base.items():
                d.setdefault(k, v)
            d.setdefault("settings", base["settings"])
            for sk, sv in base["settings"].items():
                d["settings"].setdefault(sk, sv)
            return d
        except Exception as e:
            log.error(e)
    return default_data()


def save(d):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def is_admin(uid):
    return uid == ADMIN_ID


def set_state(c, kind, extra=None):
    c.user_data["st"] = {"kind": kind, "ts": time.time(), "extra": extra or {}}


def get_state(c):
    st = c.user_data.get("st")
    if not st:
        return None
    if time.time() - st.get("ts", 0) > STATE_TTL:
        c.user_data.pop("st", None)
        return None
    return st


def clear_state(c):
    c.user_data.pop("st", None)


def style_of(name):
    name = (name or "").lower().strip()
    if name in ("red", "danger", "قرمز", "r"):
        return "danger"
    if name in ("green", "success", "سبز", "g"):
        return "success"
    if name in ("blue", "primary", "آبی", "ابي", "b"):
        return "primary"
    return "primary"


def style_label(s):
    return {"danger": "🔴 قرمز", "success": "🟢 سبز", "primary": "🔵 آبی"}.get(s, s)


def btn(text, data, style=None):
    kwargs = {"text": text, "callback_data": data}
    if style in ("danger", "success", "primary"):
        kwargs["style"] = style
    try:
        return InlineKeyboardButton(**kwargs)
    except TypeError:
        # نسخه قدیمی python-telegram-bot بدون style
        kwargs.pop("style", None)
        return InlineKeyboardButton(**kwargs)


def cancel_kb():
    return InlineKeyboardMarkup([[btn("❌ انصراف", "cancel", "danger")]])


def main_panel():
    return InlineKeyboardMarkup([
        [btn("😀 ایموجی‌ها", "p_emoji", "primary")],
        [btn("📝 متن‌ها", "p_text", "primary")],
        [btn("📋 دستورات", "p_cmds", "primary")],
        [btn("⚙️ تنظیمات", "p_set", "success")],
        [btn("❌ بستن", "p_close", "danger")],
    ])


# ---------- commands ----------
async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if is_admin(u.effective_user.id):
        await u.message.reply_text(
            "سلام ادمین 👋\nبا دستور <b>پنل</b> منوی مدیریت باز می‌شود.",
            parse_mode="HTML",
            reply_markup=main_panel(),
        )
    else:
        await u.message.reply_text("ربات انیمیشن ایموجی/متن فعال است.")


async def cmd_panel(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_admin(u.effective_user.id):
        return
    clear_state(c)
    await u.message.reply_text("🎛 <b>پنل ادمین</b>", parse_mode="HTML", reply_markup=main_panel())


async def on_panel_word(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.message.text:
        return
    if u.message.text.strip() != "پنل":
        return
    if not is_admin(u.effective_user.id):
        return
    clear_state(c)
    await u.message.reply_text("🎛 <b>پنل ادمین</b>", parse_mode="HTML", reply_markup=main_panel())


# ---------- animation engine ----------
# chat_id -> {"stop": bool, "task": Task}
ACTIVE = {}


async def run_animation(bot, chat_id, reply_to_msg_id, frames, mode, frame_sec, loops):
    if not frames:
        return
    state = ACTIVE.get(chat_id) or {"stop": False}
    ACTIVE[chat_id] = state

    frame_sec = max(0.2, float(frame_sec or 0.7))
    loops = max(1, int(loops or 1))
    mode = mode if mode in ("join", "replace") else "replace"

    try:
        kwargs = {"chat_id": chat_id, "text": frames[0]}
        if reply_to_msg_id:
            kwargs["reply_to_message_id"] = reply_to_msg_id
        msg = await bot.send_message(**kwargs)
    except Exception as e:
        log.error("send anim: %s", e)
        ACTIVE.pop(chat_id, None)
        return

    mid = msg.message_id
    try:
        for _ in range(loops):
            if state.get("stop"):
                break
            built = ""
            for fr in frames:
                if state.get("stop"):
                    break
                if mode == "join":
                    built = (built + fr) if built else fr
                    text = built
                else:
                    text = fr
                try:
                    await bot.edit_message_text(chat_id=chat_id, message_id=mid, text=text)
                except Exception:
                    pass
                try:
                    await asyncio.sleep(frame_sec)
                except asyncio.CancelledError:
                    state["stop"] = True
                    break
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.error("anim loop: %s", e)
    finally:
        if ACTIVE.get(chat_id) is state:
            ACTIVE.pop(chat_id, None)


def stop_animation(chat_id):
    st = ACTIVE.get(chat_id)
    if not st:
        return False
    st["stop"] = True
    t = st.get("task")
    if t and not t.done():
        t.cancel()
    ACTIVE.pop(chat_id, None)
    return True


async def cmd_stop(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_admin(u.effective_user.id):
        return
    ok = stop_animation(u.effective_chat.id)
    if ok:
        await u.message.reply_text("⏹ انیمیشن متوقف شد.")
    else:
        await u.message.reply_text("انیمیشن فعالی نیست.")


# ---------- text / commands from users (admin reply) ----------
async def on_text(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message or not u.message.text:
        return
    user = u.effective_user
    text = u.message.text.strip()
    d = load()

    # admin states
    if is_admin(user.id):
        st = get_state(c)
        if st:
            await handle_admin_state(u, c, d, text, st)
            return
        if text == "پنل":
            clear_state(c)
            await u.message.reply_text("🎛 <b>پنل ادمین</b>", parse_mode="HTML", reply_markup=main_panel())
            return
        # stop without /
        if text.lower() in ("stop", "استاپ", "توقف"):
            ok = stop_animation(u.effective_chat.id)
            await u.message.reply_text("⏹ متوقف شد." if ok else "انیمیشن فعالی نیست.")
            return

    # command execution: /cmd or cmd
    raw = text
    if raw.startswith("/"):
        raw = raw[1:]
    raw = raw.split("@")[0].split()[0].strip().lower()
    if not raw:
        return

    if raw in ("stop", "استاپ"):
        if is_admin(user.id):
            ok = stop_animation(u.effective_chat.id)
            await u.message.reply_text("⏹ متوقف شد." if ok else "انیمیشن فعالی نیست.")
        return

    item = None
    if raw in d.get("emojis", {}):
        item = d["emojis"][raw]
    elif raw in d.get("texts", {}):
        item = d["texts"][raw]
    else:
        return

    # only admin can trigger
    if not is_admin(user.id):
        return

    # اگر ریپلای کرده روی همون فرد؛ وگرنه روی پیام خود ادمین ریپلای کن
    if u.message.reply_to_message:
        reply_id = u.message.reply_to_message.message_id
    else:
        reply_id = u.message.message_id

    frames = item.get("frames") or []
    mode = d["settings"].get("mode", "replace")
    frame_sec = item.get("frame_sec") or d["settings"].get("frame_sec", 0.7)
    loops = d["settings"].get("loops", 3)

    # دستور را پاک نکن اگر قرار است به آن ریپلای شود
    if u.message.reply_to_message:
        try:
            await u.message.delete()
        except Exception:
            pass

    chat_id = u.effective_chat.id
    # توقف قبلی
    if chat_id in ACTIVE:
        ACTIVE[chat_id]["stop"] = True
        old = ACTIVE[chat_id].get("task")
        if old and not old.done():
            old.cancel()
    ACTIVE[chat_id] = {"stop": False, "task": None}

    async def _wrap():
        await run_animation(c.bot, chat_id, reply_id, frames, mode, frame_sec, loops)

    task = asyncio.create_task(_wrap())
    ACTIVE[chat_id]["task"] = task


async def handle_admin_state(u, c, d, text, st):
    kind = st.get("kind")

    # --- emoji add flow ---
    if kind == "em_frames":
        # emojis separated by space or nothing: 🔥 💧 ⚡ or 🔥💧⚡
        parts = text.replace(",", " ").split()
        if not parts:
            # treat each char as frame if no space
            parts = list(text.replace(" ", ""))
        if not parts:
            await u.message.reply_text("حداقل یک ایموجی بفرست", reply_markup=cancel_kb())
            return
        c.user_data["draft"] = {"frames": parts, "type": "emoji"}
        set_state(c, "em_cmd")
        await u.message.reply_text(
            f"✅ {len(parts)} فریم\nحالا <b>دستور</b> را بفرست (بدون /)\nمثال: fire",
            parse_mode="HTML",
            reply_markup=cancel_kb(),
        )
        return

    if kind == "em_cmd":
        cmd = text.strip().lstrip("/").lower().split()[0]
        if not cmd.isalnum() and not all(ch.isalnum() or ch in "_-" for ch in cmd):
            await u.message.reply_text("دستور فقط حروف/عدد/_/-", reply_markup=cancel_kb())
            return
        if cmd in d.get("emojis", {}) or cmd in d.get("texts", {}):
            await u.message.reply_text("این دستور قبلاً هست", reply_markup=cancel_kb())
            return
        c.user_data.setdefault("draft", {})["cmd"] = cmd
        set_state(c, "em_style")
        await u.message.reply_text(
            "رنگ دکمه را انتخاب کن:",
            reply_markup=InlineKeyboardMarkup([
                [btn("🔴 قرمز", "style_danger", "danger")],
                [btn("🟢 سبز", "style_success", "success")],
                [btn("🔵 آبی", "style_primary", "primary")],
                [btn("❌ انصراف", "cancel", "danger")],
            ]),
        )
        return

    # --- text add flow ---
    if kind == "tx_frames":
        # split by +
        parts = [p.strip() for p in text.split("+") if p.strip()]
        if not parts:
            await u.message.reply_text("متن‌ها را با + جدا کن\nمثال: سلام+خوبی+چه خبر", reply_markup=cancel_kb())
            return
        c.user_data["draft"] = {"frames": parts, "type": "text"}
        set_state(c, "tx_sec")
        await u.message.reply_text(
            f"✅ {len(parts)} تکه متن\nثانیه بین هر تکه را بفرست (مثلاً 0.8):",
            reply_markup=cancel_kb(),
        )
        return

    if kind == "tx_sec":
        try:
            sec = float(text.replace(",", "."))
            if sec < 0.2:
                sec = 0.2
        except ValueError:
            await u.message.reply_text("عدد معتبر بفرست", reply_markup=cancel_kb())
            return
        c.user_data.setdefault("draft", {})["frame_sec"] = sec
        set_state(c, "tx_cmd")
        await u.message.reply_text("دستور را بفرست (بدون /)\nمثال: hello", reply_markup=cancel_kb())
        return

    if kind == "tx_cmd":
        cmd = text.strip().lstrip("/").lower().split()[0]
        if not cmd or not all(ch.isalnum() or ch in "_-" for ch in cmd):
            await u.message.reply_text("دستور نامعتبر", reply_markup=cancel_kb())
            return
        if cmd in d.get("emojis", {}) or cmd in d.get("texts", {}):
            await u.message.reply_text("این دستور قبلاً هست", reply_markup=cancel_kb())
            return
        c.user_data.setdefault("draft", {})["cmd"] = cmd
        set_state(c, "tx_style")
        await u.message.reply_text(
            "رنگ دکمه:",
            reply_markup=InlineKeyboardMarkup([
                [btn("🔴 قرمز", "style_danger", "danger")],
                [btn("🟢 سبز", "style_success", "success")],
                [btn("🔵 آبی", "style_primary", "primary")],
                [btn("❌ انصراف", "cancel", "danger")],
            ]),
        )
        return

    # settings numbers
    if kind == "set_sec":
        try:
            sec = float(text.replace(",", "."))
            if sec < 0.2:
                sec = 0.2
        except ValueError:
            await u.message.reply_text("عدد بفرست", reply_markup=cancel_kb())
            return
        d["settings"]["frame_sec"] = sec
        save(d)
        clear_state(c)
        await u.message.reply_text(f"✅ ثانیه فریم: {sec}", reply_markup=main_panel())
        return

    if kind == "set_loops":
        try:
            n = int(text)
            if n < 1:
                n = 1
            if n > 20:
                n = 20
        except ValueError:
            await u.message.reply_text("عدد صحیح بفرست", reply_markup=cancel_kb())
            return
        d["settings"]["loops"] = n
        save(d)
        clear_state(c)
        await u.message.reply_text(f"✅ تعداد لوپ: {n}", reply_markup=main_panel())
        return


def finish_draft(c, d, style):
    draft = c.user_data.get("draft") or {}
    cmd = draft.get("cmd")
    frames = draft.get("frames") or []
    if not cmd or not frames:
        return False, "داده ناقص"
    style = style if style in ("danger", "success", "primary") else "primary"
    title = cmd.capitalize()
    if draft.get("type") == "text":
        d.setdefault("texts", {})[cmd] = {
            "frames": frames,
            "style": style,
            "title": title,
            "frame_sec": draft.get("frame_sec") or d["settings"].get("frame_sec", 0.7),
        }
    else:
        d.setdefault("emojis", {})[cmd] = {
            "frames": frames,
            "style": style,
            "title": title,
        }
    save(d)
    clear_state(c)
    c.user_data.pop("draft", None)
    return True, cmd


# ---------- callbacks ----------
async def on_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    await q.answer()
    user = q.from_user
    if not is_admin(user.id):
        await q.answer("فقط ادمین", show_alert=True)
        return
    cb = q.data
    d = load()

    if cb == "cancel":
        clear_state(c)
        c.user_data.pop("draft", None)
        try:
            await q.edit_message_text("❌ لغو شد", reply_markup=main_panel())
        except Exception:
            await q.message.reply_text("❌ لغو شد", reply_markup=main_panel())
        return

    if cb == "p_close":
        try:
            await q.edit_message_text("پنل بسته شد.")
        except Exception:
            pass
        return

    if cb == "p_home" or cb == "p_back":
        clear_state(c)
        try:
            await q.edit_message_text("🎛 <b>پنل ادمین</b>", parse_mode="HTML", reply_markup=main_panel())
        except Exception:
            await q.message.reply_text("🎛 پنل", reply_markup=main_panel())
        return

    # settings
    if cb == "p_set":
        s = d["settings"]
        mode = s.get("mode", "replace")
        txt = (
            f"⚙️ <b>تنظیمات</b>\n\n"
            f"حالت: <b>{'جایگزین' if mode == 'replace' else 'اضافه‌شونده'}</b>\n"
            f"ثانیه فریم: <b>{s.get('frame_sec', 0.7)}</b>\n"
            f"تعداد لوپ: <b>{s.get('loops', 3)}</b>"
        )
        kb = InlineKeyboardMarkup([
            [btn("🔁 حالت: جایگزین", "set_mode_replace", "primary" if mode != "replace" else "success")],
            [btn("➕ حالت: اضافه‌شونده", "set_mode_join", "primary" if mode != "join" else "success")],
            [btn("⏱ تغییر ثانیه", "set_sec", "primary")],
            [btn("🔄 تغییر لوپ", "set_loops", "primary")],
            [btn("🔙 بازگشت", "p_home", "danger")],
        ])
        await q.edit_message_text(txt, parse_mode="HTML", reply_markup=kb)
        return

    if cb == "set_mode_replace":
        d["settings"]["mode"] = "replace"
        save(d)
        await q.answer("حالت: جایگزین")
        cb = "p_set"
        # fallthrough by re-invoke
        s = d["settings"]
        txt = (
            f"⚙️ <b>تنظیمات</b>\n\n"
            f"حالت: <b>جایگزین</b>\n"
            f"ثانیه فریم: <b>{s.get('frame_sec', 0.7)}</b>\n"
            f"تعداد لوپ: <b>{s.get('loops', 3)}</b>"
        )
        kb = InlineKeyboardMarkup([
            [btn("🔁 حالت: جایگزین", "set_mode_replace", "success")],
            [btn("➕ حالت: اضافه‌شونده", "set_mode_join", "primary")],
            [btn("⏱ تغییر ثانیه", "set_sec", "primary")],
            [btn("🔄 تغییر لوپ", "set_loops", "primary")],
            [btn("🔙 بازگشت", "p_home", "danger")],
        ])
        await q.edit_message_text(txt, parse_mode="HTML", reply_markup=kb)
        return

    if cb == "set_mode_join":
        d["settings"]["mode"] = "join"
        save(d)
        await q.answer("حالت: اضافه‌شونده")
        s = d["settings"]
        txt = (
            f"⚙️ <b>تنظیمات</b>\n\n"
            f"حالت: <b>اضافه‌شونده</b>\n"
            f"ثانیه فریم: <b>{s.get('frame_sec', 0.7)}</b>\n"
            f"تعداد لوپ: <b>{s.get('loops', 3)}</b>"
        )
        kb = InlineKeyboardMarkup([
            [btn("🔁 حالت: جایگزین", "set_mode_replace", "primary")],
            [btn("➕ حالت: اضافه‌شونده", "set_mode_join", "success")],
            [btn("⏱ تغییر ثانیه", "set_sec", "primary")],
            [btn("🔄 تغییر لوپ", "set_loops", "primary")],
            [btn("🔙 بازگشت", "p_home", "danger")],
        ])
        await q.edit_message_text(txt, parse_mode="HTML", reply_markup=kb)
        return

    if cb == "set_sec":
        set_state(c, "set_sec")
        await q.edit_message_text("ثانیه بین فریم‌ها را بفرست (مثلاً 0.5):", reply_markup=cancel_kb())
        return

    if cb == "set_loops":
        set_state(c, "set_loops")
        await q.edit_message_text("تعداد تکرار لوپ را بفرست (۱ تا ۲۰):", reply_markup=cancel_kb())
        return

    # emoji panel
    if cb == "p_emoji":
        rows = [[btn("➕ افزودن ایموجی", "em_add", "success")]]
        for cmd, item in sorted(d.get("emojis", {}).items()):
            st = item.get("style", "primary")
            title = item.get("title") or cmd.capitalize()
            rows.append([btn(f"{title}  /{cmd}", f"em_view_{cmd}", st)])
        rows.append([btn("🔙 بازگشت", "p_home", "danger")])
        await q.edit_message_text("😀 <b>ایموجی‌ها</b>\nروی هر کدام بزن برای جزئیات/حذف", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        return

    if cb == "em_add":
        set_state(c, "em_frames")
        await q.edit_message_text(
            "ایموجی‌ها را پشت‌سرهم بفرست\n"
            "با فاصله یا بدون فاصله:\n"
            "<code>🔥 💧 ⚡ ✨</code>\n"
            "یا <code>🔥💧⚡✨</code>",
            parse_mode="HTML",
            reply_markup=cancel_kb(),
        )
        return

    if cb.startswith("em_view_"):
        cmd = cb[len("em_view_"):]
        item = d.get("emojis", {}).get(cmd)
        if not item:
            await q.answer("نیست", show_alert=True)
            return
        frames = " ".join(item.get("frames") or [])
        txt = (
            f"😀 <b>{item.get('title', cmd)}</b>\n"
            f"دستور: <code>/{cmd}</code>\n"
            f"رنگ: {style_label(item.get('style'))}\n"
            f"فریم‌ها:\n{frames}"
        )
        kb = InlineKeyboardMarkup([
            [btn("🗑 حذف", f"em_del_{cmd}", "danger")],
            [btn("🔙", "p_emoji", "primary")],
        ])
        await q.edit_message_text(txt, parse_mode="HTML", reply_markup=kb)
        return

    if cb.startswith("em_del_"):
        cmd = cb[len("em_del_"):]
        d.get("emojis", {}).pop(cmd, None)
        save(d)
        await q.answer("حذف شد")
        # show list again
        rows = [[btn("➕ افزودن ایموجی", "em_add", "success")]]
        for cmd2, item in sorted(d.get("emojis", {}).items()):
            st = item.get("style", "primary")
            title = item.get("title") or cmd2.capitalize()
            rows.append([btn(f"{title}  /{cmd2}", f"em_view_{cmd2}", st)])
        rows.append([btn("🔙 بازگشت", "p_home", "danger")])
        await q.edit_message_text("😀 <b>ایموجی‌ها</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        return

    # text panel
    if cb == "p_text":
        rows = [[btn("➕ افزودن متن", "tx_add", "success")]]
        for cmd, item in sorted(d.get("texts", {}).items()):
            st = item.get("style", "primary")
            title = item.get("title") or cmd.capitalize()
            rows.append([btn(f"{title}  /{cmd}", f"tx_view_{cmd}", st)])
        rows.append([btn("🔙 بازگشت", "p_home", "danger")])
        await q.edit_message_text("📝 <b>متن‌ها</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        return

    if cb == "tx_add":
        set_state(c, "tx_frames")
        await q.edit_message_text(
            "متن‌ها را با <b>+</b> جدا کن:\n"
            "<code>سلام+خوبی+چه خبر</code>",
            parse_mode="HTML",
            reply_markup=cancel_kb(),
        )
        return

    if cb.startswith("tx_view_"):
        cmd = cb[len("tx_view_"):]
        item = d.get("texts", {}).get(cmd)
        if not item:
            await q.answer("نیست", show_alert=True)
            return
        frames = " + ".join(item.get("frames") or [])
        txt = (
            f"📝 <b>{item.get('title', cmd)}</b>\n"
            f"دستور: <code>/{cmd}</code>\n"
            f"رنگ: {style_label(item.get('style'))}\n"
            f"ثانیه: {item.get('frame_sec', d['settings'].get('frame_sec'))}\n"
            f"متن‌ها:\n{frames}"
        )
        kb = InlineKeyboardMarkup([
            [btn("🗑 حذف", f"tx_del_{cmd}", "danger")],
            [btn("🔙", "p_text", "primary")],
        ])
        await q.edit_message_text(txt, parse_mode="HTML", reply_markup=kb)
        return

    if cb.startswith("tx_del_"):
        cmd = cb[len("tx_del_"):]
        d.get("texts", {}).pop(cmd, None)
        save(d)
        await q.answer("حذف شد")
        rows = [[btn("➕ افزودن متن", "tx_add", "success")]]
        for cmd2, item in sorted(d.get("texts", {}).items()):
            st = item.get("style", "primary")
            title = item.get("title") or cmd2.capitalize()
            rows.append([btn(f"{title}  /{cmd2}", f"tx_view_{cmd2}", st)])
        rows.append([btn("🔙 بازگشت", "p_home", "danger")])
        await q.edit_message_text("📝 <b>متن‌ها</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        return

    # all commands
    if cb == "p_cmds":
        lines = ["📋 <b>همه دستورات</b>\n"]
        rows = []
        for cmd, item in sorted(d.get("emojis", {}).items()):
            lines.append(f"😀 /{cmd} — {style_label(item.get('style'))}")
            rows.append([btn(f"🗑 /{cmd}", f"em_del_{cmd}", "danger")])
        for cmd, item in sorted(d.get("texts", {}).items()):
            lines.append(f"📝 /{cmd} — {style_label(item.get('style'))}")
            rows.append([btn(f"🗑 /{cmd}", f"tx_del_{cmd}", "danger")])
        if len(lines) == 1:
            lines.append("هنوز دستوری نیست.")
        rows.append([btn("🔙 بازگشت", "p_home", "danger")])
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        return

    # style pick for draft
    if cb in ("style_danger", "style_success", "style_primary"):
        st_name = cb.replace("style_", "")
        ok, cmd = finish_draft(c, d, st_name)
        if not ok:
            await q.edit_message_text(f"❌ {cmd}", reply_markup=main_panel())
            return
        await q.edit_message_text(
            f"✅ ذخیره شد\nدستور: <code>/{cmd}</code>\nرنگ: {style_label(st_name)}\n\n"
            f"روی پیام یک کاربر ریپلای کن و <code>/{cmd}</code> بزن.",
            parse_mode="HTML",
            reply_markup=main_panel(),
        )
        return


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("panel", cmd_panel))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    # also catch /commands dynamically via MessageHandler COMMAND
    app.add_handler(MessageHandler(filters.COMMAND, on_text))
    log.info("emoji anim bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
