# -*- coding: utf-8 -*-
"""
Получение PDF-отчёта Encar по carid или ссылке на машину.
Загрузка страницы → перевод текста на русский → сборка PDF.
"""
import asyncio
import re
import time
from pathlib import Path

REPORT_URL = "https://www.encar.com/md/sl/mdsl_regcar.do?carid={carid}&method=inspectionViewNew"

# Паттерны для извлечения carid
CARID_PATTERN = re.compile(r"carid=(\d+)", re.I)
CARID_ONLY_PATTERN = re.compile(r"^\s*(\d{6,})\s*$")  # только цифры, минимум 6

# Корейские символы (Хангул) — переводим только такой текст
HANGUL_RE = re.compile(r"[\uAC00-\uD7A3]+")

# Пауза между запросами к переводчику (снижает риск блокировки)
TRANSLATE_DELAY = 0.4


def _has_hangul(s: str) -> bool:
    return bool(HANGUL_RE.search(s))


def _translate_texts_sync(texts: list[str]) -> list[str]:
    """Переводит список строк с корейского на русский (синхронно)."""
    if not texts:
        return []
    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        raise ImportError("Установите deep-translator: pip install deep-translator")

    translator = GoogleTranslator(source="ko", target="ru")
    result = []
    for i, text in enumerate(texts):
        try:
            t = text.strip()
            if not t or not _has_hangul(t):
                result.append(text)
            else:
                result.append(translator.translate(t) or text)
        except Exception:
            result.append(text)
        if (i + 1) % 10 == 0:
            time.sleep(TRANSLATE_DELAY)
    return result


def _translate_html(html: str, base_url: str = "https://www.encar.com") -> str:
    """
    Находит в HTML (body) текст с корейским, переводит на русский, подставляет обратно.
    Добавляет <base href="..."> чтобы при set_content подгружались CSS/картинки.
    Скрипты и стили не трогаем.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    head = soup.find("head")
    if head:
        base = soup.new_tag("base", href=base_url.rstrip("/") + "/")
        head.insert(0, base)
    body = soup.find("body")
    if not body:
        return html

    # Собираем текстовые узлы в порядке обхода (пропускаем script, style)
    skip_tags = {"script", "style", "noscript"}
    nodes_to_translate: list[tuple] = []  # (NavigableString, original_text)

    for el in body.descendants:
        if not isinstance(el, str) or el.parent.name in skip_tags:
            continue
        s = str(el).strip()
        if s and _has_hangul(s):
            nodes_to_translate.append((el, s))

    if not nodes_to_translate:
        return html

    # Переводим все фразы (в потоке — может быть долго, но надёжно)
    texts = [t for _, t in nodes_to_translate]
    translated = _translate_texts_sync(texts)

    for (node, _), new_text in zip(nodes_to_translate, translated, strict=True):
        node.replace_with(new_text)

    return str(soup)


def extract_carid(text: str) -> str | None:
    """
    Извлекает carid из текста: ссылка Encar или просто ID (число).
    Возвращает строку с ID или None.
    """
    if not text or not text.strip():
        return None
    text = text.strip()
    # Ссылка encar с carid=
    if "encar" in text.lower() or "carid=" in text.lower():
        m = CARID_PATTERN.search(text)
        return m.group(1) if m else None
    # Только число (ID)
    m = CARID_ONLY_PATTERN.match(text)
    return m.group(1) if m else None


async def fetch_report_pdf(carid: str, save_path: Path, translate_to_russian: bool = True) -> bool:
    """
    Открывает страницу отчёта Encar, при необходимости переводит текст на русский,
    сохраняет результат в PDF. Возвращает True при успехе, False при ошибке.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise ImportError("Установите playwright: pip install playwright && playwright install chromium")

    url = REPORT_URL.format(carid=carid)
    base_url = "https://www.encar.com"
    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    viewport={"width": 900, "height": 1200},
                    locale="ko-KR",
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                )
                page = await context.new_page()
                await page.goto(url, wait_until="networkidle", timeout=25000)
                await page.wait_for_timeout(2000)

                if translate_to_russian:
                    html = await page.content()
                    translated_html = await asyncio.to_thread(_translate_html, html, base_url)
                    await page.set_content(translated_html, wait_until="networkidle", timeout=15000)
                    await page.wait_for_timeout(1000)

                await page.pdf(path=str(save_path), format="A4", print_background=True)
                return True
            finally:
                await browser.close()
    except Exception:
        return False
