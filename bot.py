# -*- coding: utf-8 -*-
"""
ربات دسته رسانه + تشخیص هوشمند + همگانی
- دسته با کلید (مثلاً عکس بده) + آپلود عکس/ویدیو/متن
- رندوم بدون تکرار تا ته دور
- تشخیص: عکس بفرست ≈ عکس بده ؛ اگر «عکس دختر بده» هم باشد، «عکس بفرست» → عکس بده
- همگانی به همه گپ‌ها (متن/عکس/ویدیو با کپشن)
"""
from __future__ import annotations
import json, os, re, random, logging, time
from difflib import SequenceMatcher

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ChatMemberHandler, ContextTypes, filters,
)
from telegram.constants import ChatType, ChatMemberStatus

BOT_TOKEN = "8727762178:AAGrdb5XFjhkcdoOEIFy1s8U71idRpN0DX8"
ADMIN_ID = 7530457395
DATA = "media_bank.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mediabank")


def D():
    return {
        "admins": [ADMIN_ID],
        "groups": [],  # chat ids as int
        "categories": {},  # id -> {name, trigger, items:[{type,file_id,text}], cursor_ids:[]}
    }


def load():
    if os.path.exists(DATA):
        try:
            with open(DATA, "r", encoding="utf-8") as f:
                d = json.load(f)
            b = D()
            for k, v in b.items():
                d.setdefault(k, v)
            if ADMIN_ID not in [int(x) for x in d.get("admins", [])]:
                d.setdefault("admins", []).insert(0, ADMIN_ID)
            return d
        except Exception as e:
            log.error(e)
    return D()


def save(d):
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def is_admin(uid):
    d = load()
    return int(uid) in {int(x) for x in d.get("admins", [ADMIN_ID])}


def is_main(uid):
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


def set_st(c, kind, extra=None):
    c.user_data["st"] = {"kind": kind, "extra": extra or {}, "ts": time.time()}


def get_st(c):
    st = c.user_data.get("st")
    if not st:
        return None
    if time.time() - st.get("ts", 0) > 600:
        c.user_data.pop("st", None)
        return None
    return st


def clear_st(c):
    c.user_data.pop("st", None)


def new_id():
    return f"c{int(time.time())}{random.randint(10,99)}"


# ---------- smart match ----------
def normalize(text: str) -> str:
    t = (text or "").strip().lower()
    t = t.replace("‌", " ")
    # unify request verbs
    reps = {
        "بفرست": "بده",
        "بفرستید": "بده",
        "بفرستین": "بده",
        "میخوام": "",
        "می‌خوام": "",
        "می خواهم": "",
        "لطفا": "",
        "لطفاً": "",
        "یه": "",
        "یک": "",
        "رو": "",
        "را": "",
    }
    for a, b in reps.items():
        t = t.replace(a, b)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def tokens(text: str):
    return [w for w in normalize(text).split() if w]


def match_score(user_text: str, trigger: str) -> float:
    """
    امتیاز بالاتر = تطبیق بهتر.
    اگر کاربر کلمات اضافهٔ دستهٔ خاص‌تر را نگفته باشد، دستهٔ کلی‌تر برنده می‌شود.
    """
    u = normalize(user_text)
    tr = normalize(trigger)
    if not u or not tr:
        return 0.0
    ut, tt = set(tokens(u)), set(tokens(tr))
    if not tt:
        return 0.0
    # همه کلمات کلیدی تریگر در پیام باشد؟
    overlap = len(ut & tt) / len(tt)
    # شباهت رشته
    ratio = SequenceMatcher(None, u, tr).ratio()
    # جریمه: کلمات تریگر که در پیام کاربر نیست (دسته خاص‌تر بدون ذکر آن کلمه)
    missing = len(tt - ut)
    penalty = missing * 0.35
    # پاداش پوشش
    score = overlap * 0.7 + ratio * 0.3 - penalty
    # اگر هیچ همپوشانی کلمه‌ای نباشد صفر
    if len(ut & tt) == 0 and ratio < 0.55:
        return 0.0
    return score


def find_best_category(d, user_text: str):
    best_id, best_score = None, 0.0
    for cid, cat in d.get("categories", {}).items():
        sc = match_score(user_text, cat.get("trigger") or cat.get("name") or "")
        if sc > best_score:
            best_score = sc
            best_id = cid
    # آستانه
    if best_score < 0.35:
        return None, 0.0
    return best_id, best_score


def pick_item(cat: dict):
    """رندوم بدون تکرار تا ته دور"""
    items = cat.get("items") or []
    if not items:
        return None
    used = set(cat.get("used_ids") or [])
    pool = [it for it in items if it.get("id") not in used]
    if not pool:
        cat["used_ids"] = []
        pool = list(items)
    it = random.choice(pool)
    used = set(cat.get("used_ids") or [])
    used.add(it["id"])
    cat["used_ids"] = list(used)
    return it


# ---------- keyboards ----------
def admin_kb():
    return InlineKeyboardMarkup([
        [btn("📁 دسته‌ها", "cats", "primary")],
        [btn("➕ دسته جدید", "cat_new", "success")],
        [btn("📢 پیام همگانی", "bcast", "success")],
        [btn("👤 ادمین‌ها", "admins", "primary")],
        [btn("❌ بستن", "close", "danger")],
    ])


def cat_list_kb(d):
    rows = []
    for cid, cat in d.get("categories", {}).items():
        n = len(cat.get("items") or [])
        rows.append([btn(f"{cat.get('trigger', cid)} ({n})", f"cat:{cid}", "primary")])
    rows.append([btn("🔙", "home", "danger")])
    return InlineKeyboardMarkup(rows)


# ---------- handlers ----------
async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_chat.type == ChatType.PRIVATE and is_admin(u.effective_user.id):
        clear_st(c)
        await u.message.reply_text("🎛 پنل ادمین", reply_markup=admin_kb())
        return
    if u.effective_chat.type == ChatType.PRIVATE:
        await u.message.reply_text("سلام. یکی از کلیدها را بفرست (مثلاً عکس بده).")


async def cmd_admin(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if u.effective_chat.type != ChatType.PRIVATE or not is_admin(u.effective_user.id):
        return
    clear_st(c)
    await u.message.reply_text("🎛 پنل ادمین", reply_markup=admin_kb())


async def on_my_member(u: Update, c: ContextTypes.DEFAULT_TYPE):
    r = u.my_chat_member
    chat = r.chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    d = load()
    cid = chat.id
    if r.new_chat_member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
        if cid not in d.get("groups", []):
            d.setdefault("groups", []).append(cid)
            save(d)
    elif r.new_chat_member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
        d["groups"] = [g for g in d.get("groups", []) if g != cid]
        save(d)


async def send_item(bot, chat_id, item, reply_to=None):
    kw = {"chat_id": chat_id}
    if reply_to:
        kw["reply_to_message_id"] = reply_to
    t = item.get("type")
    if t == "photo":
        await bot.send_photo(photo=item["file_id"], caption=item.get("caption") or None, **kw)
    elif t == "video":
        await bot.send_video(video=item["file_id"], caption=item.get("caption") or None, **kw)
    elif t == "animation":
        await bot.send_animation(animation=item["file_id"], caption=item.get("caption") or None, **kw)
    elif t == "document":
        await bot.send_document(document=item["file_id"], caption=item.get("caption") or None, **kw)
    else:
        await bot.send_message(text=item.get("text") or "—", **kw)


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
        await q.edit_message_text("🎛 پنل ادمین", reply_markup=admin_kb())
        return

    if data == "cats":
        if not d.get("categories"):
            await q.edit_message_text("دسته‌ای نیست.", reply_markup=admin_kb())
            return
        await q.edit_message_text("دسته‌ها:", reply_markup=cat_list_kb(d))
        return

    if data == "cat_new":
        set_st(c, "new_trigger")
        await q.edit_message_text(
            "کلید دسته را بفرست (مثلاً: عکس بده)",
            reply_markup=InlineKeyboardMarkup([[btn("انصراف", "home", "danger")]]),
        )
        return

    if data.startswith("cat:"):
        cid = data.split(":", 1)[1]
        cat = d.get("categories", {}).get(cid)
        if not cat:
            await q.answer("نیست", show_alert=True)
            return
        n = len(cat.get("items") or [])
        txt = (
            f"📁 <b>{cat.get('trigger')}</b>\n"
            f"تعداد رسانه: {n}\n"
            f"آیدی: <code>{cid}</code>"
        )
        kb = InlineKeyboardMarkup([
            [btn("➕ افزودن رسانه", f"addm:{cid}", "success")],
            [btn("🔄 ریست دور رندوم", f"reset:{cid}", "primary")],
            [btn("🗑 حذف دسته", f"delc:{cid}", "danger")],
            [btn("🔙", "cats", "primary")],
        ])
        await q.edit_message_text(txt, parse_mode="HTML", reply_markup=kb)
        return

    if data.startswith("addm:"):
        cid = data.split(":", 1)[1]
        set_st(c, "add_media", {"cid": cid})
        await q.edit_message_text(
            "عکس / ویدیو / گیف / متن را بفرست.\nچندتا پشت‌سرهم می‌توانی.\nوقتی تمام شد /panel بزن.",
            reply_markup=InlineKeyboardMarkup([[btn("تمام", "home", "success")]]),
        )
        return

    if data.startswith("reset:"):
        cid = data.split(":", 1)[1]
        if cid in d.get("categories", {}):
            d["categories"][cid]["used_ids"] = []
            save(d)
        await q.answer("دور رندوم از نو")
        await q.edit_message_text("ریست شد.", reply_markup=admin_kb())
        return

    if data.startswith("delc:"):
        cid = data.split(":", 1)[1]
        d.get("categories", {}).pop(cid, None)
        save(d)
        await q.edit_message_text("دسته حذف شد.", reply_markup=admin_kb())
        return

    if data == "bcast":
        set_st(c, "bcast")
        await q.edit_message_text(
            "پیام همگانی را بفرست (متن / عکس / ویدیو با کپشن).\nبه همه گپ‌هایی که بات عضو است می‌رود.",
            reply_markup=InlineKeyboardMarkup([[btn("انصراف", "home", "danger")]]),
        )
        return

    if data == "admins":
        lines = ["👤 ادمین‌ها\n"]
        rows = []
        for a in d.get("admins", []):
            tag = " (اصلی)" if int(a) == ADMIN_ID else ""
            lines.append(f"• <code>{a}</code>{tag}")
            if int(a) != ADMIN_ID and is_main(q.from_user.id):
                rows.append([btn(f"🗑 {a}", f"adel:{a}", "danger")])
        if is_main(q.from_user.id):
            rows.insert(0, [btn("➕ افزودن", "aadd", "success")])
        rows.append([btn("🔙", "home", "primary")])
        await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data == "aadd":
        if not is_main(q.from_user.id):
            await q.answer("فقط ادمین اصلی", show_alert=True)
            return
        set_st(c, "add_admin")
        await q.edit_message_text("آیدی عددی ادمین:")
        return

    if data.startswith("adel:"):
        if not is_main(q.from_user.id):
            return
        aid = int(data.split(":")[1])
        d["admins"] = [x for x in d.get("admins", []) if int(x) != aid]
        save(d)
        await q.edit_message_text("حذف شد.", reply_markup=admin_kb())
        return


async def on_private_admin(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """پیوی: پنل ادمین یا درخواست کاربر"""
    if not u.message or u.effective_chat.type != ChatType.PRIVATE:
        return
    user = u.effective_user
    d = load()
    text = (u.message.text or u.message.caption or "").strip()

    if is_admin(user.id):
        st = get_st(c)
        if text in ("/panel", "/admin", "پنل"):
            clear_st(c)
            await u.message.reply_text("🎛 پنل", reply_markup=admin_kb())
            return

        if st and st.get("kind") == "new_trigger":
            if not text:
                await u.message.reply_text("متن کلید را بفرست")
                return
            cid = new_id()
            d.setdefault("categories", {})[cid] = {
                "trigger": text,
                "name": text,
                "items": [],
                "used_ids": [],
            }
            save(d)
            clear_st(c)
            set_st(c, "add_media", {"cid": cid})
            await u.message.reply_text(
                f"✅ دسته «{text}» ساخته شد.\nالان رسانه بفرست (عکس/ویدیو/متن).",
                reply_markup=InlineKeyboardMarkup([[btn("تمام", "home", "success")]]),
            )
            return

        if st and st.get("kind") == "add_media":
            cid = (st.get("extra") or {}).get("cid")
            cat = d.get("categories", {}).get(cid)
            if not cat:
                clear_st(c)
                await u.message.reply_text("دسته نیست", reply_markup=admin_kb())
                return
            item = media_from_message(u.message)
            if not item:
                await u.message.reply_text("عکس/ویدیو/گیف/متن بفرست")
                return
            cat.setdefault("items", []).append(item)
            save(d)
            await u.message.reply_text(f"✅ اضافه شد. جمع: {len(cat['items'])}")
            return

        if st and st.get("kind") == "bcast":
            clear_st(c)
            groups = d.get("groups") or []
            # also try known from get_chat? only stored groups
            if not groups:
                await u.message.reply_text("گپی ثبت نشده. بات را به گپ‌ها اضافه کن.", reply_markup=admin_kb())
                return
            ok, fail = 0, 0
            for gid in list(groups):
                try:
                    await broadcast_one(c.bot, gid, u.message)
                    ok += 1
                except Exception as e:
                    log.error("bcast %s: %s", gid, e)
                    fail += 1
            await u.message.reply_text(f"همگانی: ✅{ok} ❌{fail}", reply_markup=admin_kb())
            return

        if st and st.get("kind") == "add_admin" and text:
            if not text.lstrip("-").isdigit():
                await u.message.reply_text("آیدی عددی")
                return
            aid = int(text)
            if aid not in [int(x) for x in d.get("admins", [])]:
                d.setdefault("admins", []).append(aid)
                save(d)
            clear_st(c)
            await u.message.reply_text(f"✅ ادمین {aid}", reply_markup=admin_kb())
            return

    # user or admin trigger in PM
    if text:
        await try_serve(u, c, d, text)


async def on_group(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not u.message:
        return
    chat = u.effective_chat
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    d = load()
    # track group
    if chat.id not in d.get("groups", []):
        d.setdefault("groups", []).append(chat.id)
        save(d)
    text = (u.message.text or u.message.caption or "").strip()
    if not text:
        return
    await try_serve(u, c, d, text)


async def try_serve(u, c, d, text):
    cid, score = find_best_category(d, text)
    if not cid:
        return
    cat = d["categories"][cid]
    item = pick_item(cat)
    if not item:
        await u.message.reply_text("این دسته هنوز رسانه ندارد.")
        return
    save(d)
    try:
        await send_item(c.bot, u.effective_chat.id, item, reply_to=u.message.message_id)
    except Exception as e:
        log.error(e)
        await u.message.reply_text(f"خطا در ارسال: {e}")


def media_from_message(msg):
    iid = f"i{int(time.time()*1000)}{random.randint(10,99)}"
    if msg.photo:
        return {"id": iid, "type": "photo", "file_id": msg.photo[-1].file_id, "caption": msg.caption or ""}
    if msg.video:
        return {"id": iid, "type": "video", "file_id": msg.video.file_id, "caption": msg.caption or ""}
    if msg.animation:
        return {"id": iid, "type": "animation", "file_id": msg.animation.file_id, "caption": msg.caption or ""}
    if msg.document:
        return {"id": iid, "type": "document", "file_id": msg.document.file_id, "caption": msg.caption or ""}
    if msg.text:
        return {"id": iid, "type": "text", "text": msg.text}
    return None


async def broadcast_one(bot, chat_id, message):
    if message.photo:
        await bot.send_photo(chat_id, message.photo[-1].file_id, caption=message.caption or None)
    elif message.video:
        await bot.send_video(chat_id, message.video.file_id, caption=message.caption or None)
    elif message.animation:
        await bot.send_animation(chat_id, message.animation.file_id, caption=message.caption or None)
    elif message.document:
        await bot.send_document(chat_id, message.document.file_id, caption=message.caption or None)
    elif message.text:
        await bot.send_message(chat_id, message.text)
    else:
        await bot.copy_message(chat_id, message.chat_id, message.message_id)


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("panel", cmd_admin))
    app.add_handler(ChatMemberHandler(on_my_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE, on_private_admin))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.TEXT, on_group))
    log.info("media bank bot up")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
