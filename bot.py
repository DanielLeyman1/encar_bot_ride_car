# -*- coding: utf-8 -*-
import os
import uuid
from pathlib import Path
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

from encar_report import extract_carid, fetch_report_pdf

# ===== Настройки =====
ADMIN_ID = 377261863  # твой Telegram ID
STORAGE_DIR = Path("pdf_storage")
STORAGE_DIR.mkdir(exist_ok=True)

# ===== Команды =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Можешь:\n"
        "• Отправить PDF — сохраню и дам ID для поста.\n"
        "• Написать ID машины или ссылку Encar — скачаю отчёт, переведу на русский, соберу PDF и дам ссылку."
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("Ты не имеешь права загружать файлы.")
        return

    doc = update.message.document
    if doc.mime_type != "application/pdf":
        await update.message.reply_text("Только PDF разрешено!")
        return

    file_id = str(uuid.uuid4())
    file_path = STORAGE_DIR / f"{file_id}.pdf"

    await doc.get_file().download_to_drive(file_path)
    await update.message.reply_text(f"PDF сохранен. Ссылка/ID для поста: `{file_id}`", parse_mode="Markdown")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        return

    text = (update.message.text or "").strip()
    carid = extract_carid(text)
    if not carid:
        return  # не ID и не ссылка — игнорируем

    status = await update.message.reply_text(
        f"Запрашиваю отчёт Encar для carid={carid}, перевожу на русский…"
    )
    try:
        file_id = str(uuid.uuid4())
        file_path = STORAGE_DIR / f"{file_id}.pdf"
        ok = await fetch_report_pdf(carid, file_path)
        if not ok:
            await status.edit_text("Не удалось сформировать PDF (страница не открылась или нет отчёта).")
            return
        with open(file_path, "rb") as f:
            await update.message.reply_document(
            document=f, filename=f"encar_report_{carid}_ru.pdf"
        )
        await status.edit_text(
            f"Отчёт по carid={carid} переведён на русский и сохранён.\nСсылка/ID для поста: `{file_id}`",
            parse_mode="Markdown",
        )
    except Exception as e:
        await status.edit_text(f"Ошибка: {e}")


# ===== Запуск бота =====
async def main():
    TOKEN = "8596627705:AAFHUS6_b3jqhBm1NyLGsEARFhxHL0PJ4Go"
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Бот запущен…")
    await app.run_polling()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
