# -*- coding: utf-8 -*-
import logging
import os
import sys
import threading
import traceback
import time
import uuid
from pathlib import Path
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

from encar_report import extract_carid, fetch_report_pdf, run_report_diagnostics
from report_cache import save_report
from report_server import run_server

# ===== Настройки =====
ADMIN_ID = int(os.environ.get("TELEGRAM_ADMIN_ID", "377261863"))
STORAGE_DIR = Path("pdf_storage")
REPORTS_DIR = Path(os.environ.get("REPORTS_DIR", "reports"))
DATA_DIR = Path(__file__).resolve().parent / "data"
# Публичный URL для ссылок /r/<token> (nginx проксирует на этот хост). Пустая строка в env отключает выдачу ссылки.
BASE_URL = os.environ.get("REPORT_BASE_URL", "https://www.wrideauto.ru").rstrip("/")
# Порт Flask для /r/<token>. По умолчанию 9090 — чтобы не пересекаться с API каталога на 8080.
REPORT_SERVER_PORT = int(os.environ.get("REPORT_SERVER_PORT", "9090"))

# Сообщение для обычных пользователей (только админ запрашивает отчёты)
NON_ADMIN_MESSAGE = (
    "Все отчёты формируются автоматически, подробнее с каждым отчётом вы можете ознакомиться "
    "в объявлениях в нашем канале World Ride Auto — https://t.me/worldrideauto\n\n"
    "Спасибо, что выбираете нас!"
)

STORAGE_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _run_report_http_thread() -> None:
    """Flask /r/<token> в отдельном потоке; ошибки не должны пропадать незамеченными."""
    bot_dir = Path(__file__).resolve().parent
    template_dir = bot_dir / "templates"
    try:
        run_server(port=REPORT_SERVER_PORT, reports_dir=REPORTS_DIR, data_dir=DATA_DIR, template_dir=template_dir)
    except OSError as e:
        logger.error("Сервер отчётов: порт %s недоступен: %s", REPORT_SERVER_PORT, e)
        print(
            f"ОШИБКА: не удалось запустить HTTP отчётов на 0.0.0.0:{REPORT_SERVER_PORT} — {e}\n"
            "Проверьте, свободен ли порт (ss -tlnp) и совпадает ли он с nginx proxy_pass.",
            flush=True,
            file=sys.stderr,
        )
    except Exception:
        logger.exception("Сервер отчётов: сбой при запуске")
        traceback.print_exc()


def _inject_og_url(html_path: Path, report_url: str) -> None:
    """Добавляет og:url в <head> сохранённого HTML для превью в мессенджерах."""
    try:
        text = html_path.read_text(encoding="utf-8")
        meta = f'<meta property="og:url" content="{report_url}">'
        if "og:url" in text:
            return
        text = text.replace("</head>", meta + "\n  </head>", 1)
        html_path.write_text(text, encoding="utf-8")
    except Exception:
        pass


# ===== Команды =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text(NON_ADMIN_MESSAGE)
        return
    await update.message.reply_text(
        "Привет! Можешь:\n"
        "• Отправить PDF — сохраню и дам ID для поста.\n"
        "• Написать ID машины или ссылку Encar — сформирую отчёт и дам ссылку для объявления (HTML, ссылка 7 дней).\n"
        "• /myid — твой Telegram ID.\n"
        "• /report_diag — диагностика логотипа и схем."
    )


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text(NON_ADMIN_MESSAGE)
        return
    is_admin = user_id == ADMIN_ID
    await update.message.reply_text(
        f"Твой Telegram ID: `{user_id}`\n"
        f"ADMIN_ID в боте: `{ADMIN_ID}`\n"
        f"Ты админ: {'да' if is_admin else 'нет'}",
        parse_mode="Markdown",
    )


async def cmd_report_diag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text(NON_ADMIN_MESSAGE)
        return
    bot_dir = Path(__file__).resolve().parent
    diag = run_report_diagnostics(bot_dir)
    lines = diag.get("log_lines", ["Диагностика не выполнена."])
    text = "📋 Диагностика отчёта (логотип и схемы):\n\n" + "\n".join(lines)
    await update.message.reply_text(text[:4000])


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text(NON_ADMIN_MESSAGE)
        return
    doc = update.message.document
    if doc.mime_type != "application/pdf":
        await update.message.reply_text("Только PDF разрешено!")
        return
    file_id = str(uuid.uuid4())
    file_path = STORAGE_DIR / f"{file_id}.pdf"
    await doc.get_file().download_to_drive(file_path)
    await update.message.reply_text(f"PDF сохранен. Ссылка/ID для поста: `{file_id}`", parse_mode="Markdown")


def _looks_like_encar_or_id(text: str) -> bool:
    if not text:
        return False
    t = text.strip().lower()
    if "encar" in t or "carid=" in t:
        return True
    if t.isdigit() and len(t) >= 6:
        return True
    return False


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = (update.message.text or "").strip()
    carid_extracted = extract_carid(text)

    print(f"[{time.strftime('%H:%M:%S')}] [Текст] user_id={user_id} len={len(text)} carid={carid_extracted}", flush=True)
    logger.info("Текст от %s, carid=%s", user_id, carid_extracted)

    if user_id != ADMIN_ID:
        await update.message.reply_text(NON_ADMIN_MESSAGE)
        return

    carid = carid_extracted
    if not carid:
        if _looks_like_encar_or_id(text):
            await update.message.reply_text(
                "Не удалось извлечь ID машины из ссылки. Проверь, что в ссылке есть carid=ЧИСЛО или путь вида .../detail/ЧИСЛО."
            )
        return

    bot_dir = Path(__file__).resolve().parent
    status = await update.message.reply_text(f"Запрашиваю отчёт Encar для carid={carid}…")

    async def report_status(msg: str):
        try:
            await status.edit_text(f"{msg}\n\ncarid={carid}")
        except Exception:
            pass

    try:
        file_id = str(uuid.uuid4())
        file_path = STORAGE_DIR / f"{file_id}.pdf"
        print(f"[{time.strftime('%H:%M:%S')}] [BOT] вызов fetch_report_pdf carid={carid}", flush=True)
        ok, html_path, images_ok = await fetch_report_pdf(carid, file_path, on_status=report_status, base_dir=bot_dir)
        print(f"[{time.strftime('%H:%M:%S')}] [BOT] fetch_report_pdf ok={ok} html_path={html_path}", flush=True)
        if not ok:
            await status.edit_text(
                "Не удалось сформировать отчёт (таймаут, страница Encar недоступна или ошибка).\n"
                "Проверьте логи бота. Команда /report_diag — диагностика логотипа и схем."
            )
            return

        if not BASE_URL:
            await status.edit_text(
                "Отчёт сформирован, но REPORT_BASE_URL не задан — ссылку для объявлений выдать нельзя. "
                "Задайте переменную окружения REPORT_BASE_URL (например https://ваш-домен.com)."
            )
            if html_path and html_path.exists():
                with open(html_path, "rb") as f:
                    await update.message.reply_document(document=f, filename=f"encar_report_{carid}_ru.html")
            return

        html_content = html_path.read_text(encoding="utf-8")
        token = save_report(carid, html_content, REPORTS_DIR, DATA_DIR)
        report_file = REPORTS_DIR / f"{token}.html"
        report_url = f"{BASE_URL}/r/{token}"
        _inject_og_url(report_file, report_url)

        await status.edit_text(f"Отчёт по carid={carid} готов. Ссылка действительна 7 дней.")
        await update.message.reply_text(f"Посмотреть отчёт об истории авто: {report_url}")
    except Exception as e:
        await status.edit_text(f"Ошибка: {e}")


def main():
    pid_file = Path(__file__).resolve().parent / "bot.pid"
    # Один экземпляр бота (избегаем Conflict от Telegram)
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text(encoding="utf-8").strip())
            os.kill(old_pid, 0)  # проверка: процесс жив?
            print(f"Уже запущен другой экземпляр бота (PID {old_pid}). Остановите его или удалите {pid_file}.", flush=True)
            sys.exit(1)
        except (ValueError, OSError):
            try:
                pid_file.unlink()
            except Exception:
                pass
    try:
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass

    server_thread = threading.Thread(target=_run_report_http_thread, daemon=True, name="report-http")
    server_thread.start()
    print(f"Запуск HTTP отчётов на 0.0.0.0:{REPORT_SERVER_PORT} (поток {server_thread.name})…", flush=True)
    print(f"Сервер отчётов: http://0.0.0.0:{REPORT_SERVER_PORT}/r/<token>", flush=True)

    try:
        TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8596627705:AAFHUS6_b3jqhBm1NyLGsEARFhxHL0PJ4Go")
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("myid", cmd_myid))
        app.add_handler(CommandHandler("report_diag", cmd_report_diag))
        app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
        print("Бот запущен…")
        app.run_polling()
    finally:
        try:
            if pid_file.exists():
                pid_file.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    main()
