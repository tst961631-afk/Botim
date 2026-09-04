# -*- coding: utf-8 -*-
"""اسکن امنیتی شبیه‌سازی‌شده — فقط ظاهر جدی، بدون اسکن واقعی"""
import asyncio
import logging
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ChatType

BOT_TOKEN = "8975007734:AAFGsTyR56CLHJnr7ZFgz8DMAs2INlg1Qfc"
ADMIN_ID = 7530457395

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("scanbot")

# انیمیشن فعال per chat
ACTIVE = {}

LOG_LINES = [
    "Initializing security audit module...",
    "Loading threat intelligence signatures...",
    "Enumerating active chat sessions...",
    "Analyzing message transport integrity...",
    "Checking member privilege matrix...",
    "Scanning for anomalous activity patterns...",
    "Validating endpoint trust chain...",
    "Inspecting media metadata exposure risks...",
    "Running simulated intrusion test vectors...",
    "Evaluating lateral movement possibilities...",
    "Checking session token hygiene...",
    "Auditing admin permission surface...",
    "Correlating behavioral risk indicators...",
    "Building encrypted incident buffer...",
    "Compiling confidential threat summary...",
    "Finalizing owner-only report package...",
]


def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID


def bar(pct: int) -> str:
    pct = max(0, min(100, int(pct)))
    filled = pct // 5
    empty = 20 - filled
    return "█" * filled + "░" * empty


def btn(text, data, style=None):
    kwargs = {"text": text, "callback_data": data}
    if style in ("danger", "success", "primary"):
        kwargs["style"] = style
    try:
        return InlineKeyboardButton(**kwargs)
    except TypeError:
        kwargs.pop("style", None)
        return InlineKeyboardButton(**kwargs)


def scan_confirm_kb():
    return InlineKeyboardMarkup([
        [
            btn("✅ تأیید شروع اسکن", "scan_yes", "success"),
            btn("❌ لغو", "scan_no", "danger"),
        ]
    ])


async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if is_admin(u.effective_user.id):
        await u.message.reply_text(
            "ماژول امنیتی آماده است.\n"
            "دستور: /scan\n"
            "فقط ادمین مجاز است."
        )
    else:
        await u.message.reply_text("دسترسی محدود.")


async def cmd_scan(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_admin(u.effective_user.id):
        await u.message.reply_text("دسترسی غیرمجاز.")
        return
    if u.effective_chat.id in ACTIVE and ACTIVE[u.effective_chat.id].get("running"):
        await u.message.reply_text("یک عملیات اسکن در این گفتگو در حال اجرا است.")
        return
    await u.message.reply_text(
        "⚠️ SECURITY AUDIT\n\n"
        "درخواست اسکن امنیتی برای این گفتگو ثبت شد.\n"
        "با تأیید، فرآیند ارزیابی آغاز می‌شود.\n\n"
        "ادامه می‌دهید؟",
        reply_markup=scan_confirm_kb(),
    )


async def run_scan(bot, chat_id: int, status_msg_id: int):
    state = ACTIVE.setdefault(chat_id, {"running": True, "stop": False})
    state["running"] = True
    state["stop"] = False

    lines = LOG_LINES[:]
    random.shuffle(lines)
    # keep order somewhat progressive: use fixed list for seriousness
    lines = LOG_LINES[:]

    total_steps = 20
    log_i = 0
    try:
        for step in range(0, total_steps + 1):
            if state.get("stop"):
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=status_msg_id,
                        text="عملیات توسط اپراتور متوقف شد.",
                    )
                except Exception:
                    pass
                break

            pct = int(step / total_steps * 100)
            # pick log line progressively
            if step < total_steps:
                line = lines[min(log_i, len(lines) - 1)]
                log_i = min(log_i + 1, len(lines) - 1)
                body = (
                    f"SECURITY SCAN IN PROGRESS\n\n"
                    f"[{bar(pct)}] {pct}%\n\n"
                    f"> {line}\n"
                    f"> module: audit-core\n"
                    f"> channel: restricted\n"
                )
            else:
                body = (
                    f"SECURITY SCAN COMPLETED\n\n"
                    f"[{bar(100)}] 100%\n\n"
                    f"اسکن تمام شد.\n"
                    f"نتیجه به پیوی مالک ارسال شد.\n\n"
                    f"status: sealed\n"
                    f"access: owner-only"
                )

            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_msg_id,
                    text=body,
                )
            except Exception as e:
                log.warning("edit: %s", e)

            if step < total_steps:
                await asyncio.sleep(0.85)

        # report to owner PM
        if not state.get("stop"):
            report = (
                "SECURITY REPORT (CONFIDENTIAL)\n\n"
                f"target_chat_id: {chat_id}\n"
                "result: assessment finished\n"
                "severity: multiple soft indicators recorded\n"
                "recommendation: manual review by owner\n"
                "note: simulation module — no live exploit executed\n"
            )
            try:
                await bot.send_message(ADMIN_ID, report)
            except Exception as e:
                log.error("pm owner: %s", e)
    finally:
        state["running"] = False


async def on_cb(u: Update, c: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query
    await q.answer()
    user = q.from_user
    if not is_admin(user.id):
        await q.answer("دسترسی غیرمجاز", show_alert=True)
        return

    if q.data == "scan_no":
        try:
            await q.edit_message_text("عملیات لغو شد.")
        except Exception:
            pass
        return

    if q.data == "scan_yes":
        chat_id = q.message.chat_id
        if ACTIVE.get(chat_id, {}).get("running"):
            await q.answer("اسکن در حال اجراست", show_alert=True)
            return
        try:
            await q.edit_message_text(
                "SECURITY SCAN IN PROGRESS\n\n"
                f"[{bar(0)}] 0%\n\n"
                "> bootstrapping audit engine...\n"
                "> channel: restricted"
            )
        except Exception:
            pass
        ACTIVE[chat_id] = {"running": True, "stop": False}
        asyncio.create_task(run_scan(c.bot, chat_id, q.message.message_id))
        return


async def cmd_stop(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if not is_admin(u.effective_user.id):
        return
    st = ACTIVE.get(u.effective_chat.id)
    if st and st.get("running"):
        st["stop"] = True
        await u.message.reply_text("درخواست توقف ثبت شد.")
    else:
        await u.message.reply_text("اسکن فعالی نیست.")


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CallbackQueryHandler(on_cb))
    log.info("scan bot up")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
