import os
import uuid
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# ===== Настройки =====
ADMIN_ID = 377261863  # твой Telegram ID
STORAGE_DIR = "pdf_storage"
os.makedirs(STORAGE_DIR, exist_ok=True)

# ===== Команды =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Отправь мне PDF для генерации ссылки.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("Ты не имеешь права загружать файлы.")
        return

    doc = update.message.document
    if doc.mime_type != "application/pdf":
        await update.message.reply_text("Только PDF разрешено!")
        return

    # Генерируем уникальное имя
    file_id = str(uuid.uuid4())
    file_path = os.path.join(STORAGE_DIR, f"{file_id}.pdf")
    
    # Сохраняем PDF на сервер
    await doc.get_file().download_to_drive(file_path)
    
    # Отправляем ID / ссылку администратору
    await update.message.reply_text(f"PDF сохранен. Ссылка/ID для поста: `{file_id}`", parse_mode="Markdown")

# ===== Запуск бота =====
async def main():
    TOKEN = "8596627705:AAFHUS6_b3jqhBm1NyLGsEARFhxHL0PJ4Go"  # вставь сюда токен бота
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    print("Бот запущен…")
    await app.run_polling()

if name == "__main__":
    import asyncio
    asyncio.run(main())
