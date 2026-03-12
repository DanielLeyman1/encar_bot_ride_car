# -*- coding: utf-8 -*-
"""
Получение PDF-отчёта Encar по carid или ссылке на машину.
Загрузка страницы → перевод текста на русский → сборка PDF.
"""
import asyncio
import json
import os
import re
import time
from pathlib import Path

# #region agent log
def _debug_log(location: str, message: str, data: dict | None = None, hypothesis_id: str = ""):
    try:
        log_path = Path(__file__).resolve().parent.parent / "debug-29cbeb.log"
        payload = {"sessionId": "29cbeb", "location": location, "message": message, "timestamp": int(time.time() * 1000)}
        if data:
            payload["data"] = data
        if hypothesis_id:
            payload["hypothesisId"] = hypothesis_id
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
# #endregion

# Прокси для доступа к Encar (Корея). Выключить: REPORT_PROXY=0
def _report_proxy():
    if os.environ.get("REPORT_PROXY", "1") == "0":
        return None
    server = os.environ.get("REPORT_PROXY_SERVER", "http://geo.floppydata.com:10080")
    if not server.startswith("http"):
        server = "http://" + server
    return {
        "server": server,
        "username": os.environ.get("REPORT_PROXY_USER", "UciwZyfTPlvUn4OS"),
        "password": os.environ.get("REPORT_PROXY_PASSWORD", "FDsAIHONGvLKdUlN"),
    }


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# Точный формат как на сайте (method и carid)
REPORT_URL = "https://www.encar.com/md/sl/mdsl_regcar.do?method=inspectionViewNew&carid={carid}"

# Паттерны для извлечения carid (query carid=... или путь /detail/123/)
CARID_PATTERN = re.compile(r"carid=(\d+)", re.I)
CARID_PATH_PATTERN = re.compile(r"encar\.com[^/]*/.*?/(?:detail/)?(\d{6,})(?:\?|/|$)", re.I)
CARID_ONLY_PATTERN = re.compile(r"^\s*(\d{6,})\s*$")  # только цифры, минимум 6

# Корейские символы (Хангул) — переводим только такой текст
HANGUL_RE = re.compile(r"[\uAC00-\uD7A3]+")

# Сколько кусков переводить одновременно (ускорение)
PARALLEL_CHUNKS = 6
# Пауза между "волнами" параллельных запросов (сек)
TRANSLATE_BATCH_DELAY = 0.15
# Лимит символов на один запрос (Google ~5000)
TRANSLATE_MAX_CHARS = 4000
# Разделитель (Private Use — не встречается в тексте)
SEP = "\uE000"
# Таймаут перевода (сек); при превышении — PDF без перевода
TRANSLATE_TIMEOUT = 90


def _has_hangul(s: str) -> bool:
    return bool(HANGUL_RE.search(s))


def _translate_chunk_sync(chunk: str) -> list[str]:
    """Переводит один кусок (вызывается из потока). Возвращает список фраз по SEP."""
    try:
        from deep_translator import GoogleTranslator
        tr = GoogleTranslator(source="ko", target="ru").translate(chunk) or chunk
        return tr.split(SEP)
    except Exception:
        return chunk.split(SEP)


def _translate_ko_ru_sync(text: str) -> str:
    """Один запрос корейский → русский для самообучения маппинга."""
    if not text or not text.strip():
        return text
    try:
        from deep_translator import GoogleTranslator
        return GoogleTranslator(source="ko", target="ru").translate(text.strip()) or text
    except Exception:
        return text


async def _translate_texts_async(texts: list[str]) -> list[str]:
    """
    Переводит тексты асинхронно: куски отправляются параллельно (по PARALLEL_CHUNKS),
    чтобы уложиться в ~1 минуту.
    """
    if not texts:
        _log("TRANSLATE: нет текста")
        return []
    indices_need = [j for j, t in enumerate(texts) if t.strip() and _has_hangul(t.strip())]
    if not indices_need:
        _log("TRANSLATE: нет корейского")
        return list(texts)

    strings_need = [texts[j].strip() for j in indices_need]
    combined = SEP.join(strings_need)
    _log(f"TRANSLATE: сегментов={len(strings_need)}, символов={len(combined)}")

    segments = combined.split(SEP)
    chunks = []
    current = []
    current_len = 0
    for s in segments:
        add_len = len(s) + len(SEP)
        if current_len + add_len > TRANSLATE_MAX_CHARS and current:
            chunks.append(SEP.join(current))
            current = [s]
            current_len = add_len
        else:
            current.append(s)
            current_len += add_len
    if current:
        chunks.append(SEP.join(current))

    _log(f"TRANSLATE: кусков={len(chunks)}, параллельно по {PARALLEL_CHUNKS}...")
    all_parts = []
    for i in range(0, len(chunks), PARALLEL_CHUNKS):
        batch = chunks[i : i + PARALLEL_CHUNKS]
        tasks = [asyncio.to_thread(_translate_chunk_sync, ch) for ch in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for idx, r in enumerate(results):
            if isinstance(r, Exception):
                _log(f"  ошибка куска: {r}")
                all_parts.extend(batch[idx].split(SEP))
            else:
                all_parts.extend(r)
        if i + len(batch) < len(chunks):
            await asyncio.sleep(TRANSLATE_BATCH_DELAY)

    if len(all_parts) != len(strings_need):
        _log(f"TRANSLATE: несовпадение {len(all_parts)} != {len(strings_need)}, без перевода")
        return list(texts)

    trans_idx = 0
    result = []
    for j in range(len(texts)):
        if j in indices_need:
            result.append((all_parts[trans_idx] or texts[j]).strip() if trans_idx < len(all_parts) else texts[j])
            trans_idx += 1
        else:
            result.append(texts[j])
    _log("TRANSLATE: готово")
    return result


def _translate_texts_sync(texts: list[str]) -> list[str]:
    """
    Переводит все строки с корейского на русский за несколько запросов:
    собираем все фразы в один текст, режем по ~4000 символов, переводим каждый кусок.
    """
    if not texts:
        _log("TRANSLATE: нет текста")
        return []
    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        raise ImportError("Установите deep-translator: pip install deep-translator")

    indices_need = [j for j, t in enumerate(texts) if t.strip() and _has_hangul(t.strip())]
    if not indices_need:
        _log("TRANSLATE: нет корейского")
        return list(texts)

    strings_need = [texts[j].strip() for j in indices_need]
    combined = SEP.join(strings_need)
    _log(f"TRANSLATE: сегментов={len(strings_need)}, всего символов={len(combined)}")

    # Режем на куски по TRANSLATE_MAX_CHARS по границам SEP
    segments = combined.split(SEP)
    chunks = []
    current = []
    current_len = 0
    for s in segments:
        add_len = len(s) + len(SEP)
        if current_len + add_len > TRANSLATE_MAX_CHARS and current:
            chunks.append(SEP.join(current))
            current = [s]
            current_len = add_len
        else:
            current.append(s)
            current_len += add_len
    if current:
        chunks.append(SEP.join(current))

    _log(f"TRANSLATE: кусков={len(chunks)}, перевожу...")
    translator = GoogleTranslator(source="ko", target="ru")
    all_parts = []
    for i, ch in enumerate(chunks):
        t0 = time.time()
        for attempt in range(3):
            try:
                tr = translator.translate(ch) or ch
                all_parts.extend(tr.split(SEP))
                _log(f"  кусок {i+1}/{len(chunks)} ок, {time.time()-t0:.1f}s")
                break
            except Exception as e:
                _log(f"  кусок {i+1}/{len(chunks)} попытка {attempt+1}/3: {e}")
                if attempt == 2:
                    all_parts.extend(ch.split(SEP))
                else:
                    time.sleep(1.0)
        time.sleep(TRANSLATE_DELAY)

    # Если разделители исказились — не переводим по одной (это 10+ мин), отдаём как есть
    if len(all_parts) != len(strings_need):
        _log(f"TRANSLATE: несовпадение {len(all_parts)} != {len(strings_need)}, возвращаю без перевода")
        return list(texts)

    trans_idx = 0
    result = []
    for j in range(len(texts)):
        if j in indices_need:
            result.append((all_parts[trans_idx] or texts[j]).strip() if trans_idx < len(all_parts) else texts[j])
            trans_idx += 1
        else:
            result.append(texts[j])
    _log("TRANSLATE: готово")
    return result


def _parse_html_and_get_texts(html: str, base_url: str = "https://www.encar.com"):
    """Парсит HTML, добавляет <base>, возвращает (soup, list of (node, text))."""
    from bs4 import BeautifulSoup
    _log("HTML: парсинг...")
    soup = BeautifulSoup(html, "html.parser")
    head = soup.find("head")
    if head:
        base = soup.new_tag("base", href=base_url.rstrip("/") + "/")
        head.insert(0, base)
    body = soup.find("body")
    if not body:
        return None, []
    skip_tags = {"script", "style", "noscript"}
    nodes_to_translate = []
    for el in body.descendants:
        if not isinstance(el, str) or el.parent.name in skip_tags:
            continue
        s = str(el).strip()
        if s and _has_hangul(s):
            nodes_to_translate.append((el, s))
    return soup, nodes_to_translate


async def _translate_html_async(html: str, base_url: str = "https://www.encar.com") -> str:
    """
    Парсит HTML, переводит текст асинхронно (параллельные куски), подставляет обратно.
    """
    soup, nodes_to_translate = _parse_html_and_get_texts(html, base_url)
    if not soup or not nodes_to_translate:
        _log("HTML: нет узлов для перевода")
        return html
    _log(f"HTML: узлов с корейским={len(nodes_to_translate)}, перевод...")
    texts = [t for _, t in nodes_to_translate]
    translated = await _translate_texts_async(texts)
    _log("HTML: подставляю в разметку...")
    for (node, _), new_text in zip(nodes_to_translate, translated, strict=True):
        node.replace_with(new_text)
    return str(soup)


def extract_carid(text: str) -> str | None:
    """
    Извлекает carid из текста: ссылка Encar (fem.encar.com, www.encar.com и т.д.) или просто ID (число).
    Возвращает строку с ID или None.
    """
    if not text or not text.strip():
        return None
    text = text.strip()
    # Ссылка encar: сначала carid= в query, иначе ID из пути (/detail/123 или /cars/detail/123)
    if "encar" in text.lower():
        m = CARID_PATTERN.search(text)
        if m:
            return m.group(1)
        m = CARID_PATH_PATTERN.search(text)
        if m:
            return m.group(1)
        return None
    if "carid=" in text.lower():
        m = CARID_PATTERN.search(text)
        return m.group(1) if m else None
    # Только число (ID)
    m = CARID_ONLY_PATTERN.match(text)
    return m.group(1) if m else None


# Имена файлов логотипа и схем для диагностики и подстановки
LOGO_NAMES = ("logo.png", "logo.svg")
DIAGRAM_OUTER_NAMES = ("diagram_outer.png", "diagram_outer.png.png")
DIAGRAM_INNER_NAMES = ("diagram_inner.png", "diagram_inner.png.png")

# Иконки схемы: код → имя файла в templates/images
DIAGRAM_CODE_ICONS = {
    "CHANGE": "Exchange.svg",
    "METAL": "Painted.svg",
    "CORROSION": "corrosion.svg",
    "SCRATCH": "Scratch.svg",
    "DENT": "Repair.svg",
    "DAMAGE": "Damage.svg",
}
# Подписи легенды (код → рус.)
DIAGRAM_LEGEND_LABELS = {
    "CHANGE": "Замена",
    "METAL": "Окрас/сварка",
    "CORROSION": "Коррозия",
    "SCRATCH": "Царапина",
    "DENT": "Вмятина",
    "DAMAGE": "Повреждение",
}
DIAGRAM_VIEWBOX = "0 0 400 200"
# Канонические размеры схем (один вид сверху на каждое изображение), для масштабирования
DIAGRAM_OUTER_CANONICAL = (400, 200)
DIAGRAM_INNER_CANONICAL = (400, 200)
DIAGRAM_ICON_SIZE = 24


def _load_diagram_zone_data(data_dir: Path) -> tuple[dict, dict]:
    """Загружает зоны из JSON. Возвращает (outer_data, inner_data):
    каждый элемент — zone_id -> {"d": path_d, "cx": cx, "cy": cy}."""
    def points_to_d(points: list) -> str:
        if not points or len(points) < 2:
            return ""
        parts = [f"M {points[0][0]} {points[0][1]}"]
        for p in points[1:]:
            parts.append(f"L {p[0]} {p[1]}")
        parts.append("Z")
        return " ".join(parts)

    def centroid(points: list) -> tuple[float, float]:
        if not points:
            return (0, 0)
        n = len(points)
        cx = sum(p[0] for p in points) / n
        cy = sum(p[1] for p in points) / n
        return (cx, cy)

    def load_zones(path: Path) -> dict:
        out = {}
        if not path.exists():
            return out
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for zone_id, pts in raw.items():
                if isinstance(pts, list) and len(pts) >= 2:
                    d = points_to_d(pts)
                    cx, cy = centroid(pts)
                    if d:
                        out[zone_id] = {"d": d, "cx": cx, "cy": cy}
        except Exception:
            pass
        return out

    data_dir = data_dir or Path(__file__).resolve().parent / "data"
    outer = load_zones(data_dir / "diagram_outer_zones.json")
    inner = load_zones(data_dir / "diagram_inner_zones.json")
    return outer, inner


def run_report_diagnostics(base_dir: Path | None) -> dict:
    """
    Самодиагностика: ищет папку templates/images и проверяет наличие логотипа и схем.
    Возвращает: images_dir (Path или None), template_dir, found { logo, diagram_outer, diagram_inner }, log_lines.
    """
    script_dir = Path(__file__).resolve().parent
    cwd = Path.cwd()
    candidates = [
        ("base_dir", base_dir),
        ("script_dir", script_dir),
        ("cwd", cwd),
        ("cwd/encar_bot_ride_car", cwd / "encar_bot_ride_car"),
    ]
    log_lines = []
    for label, base in candidates:
        if base is None or not base:
            continue
        template_dir = base / "templates"
        images_dir = template_dir / "images"
        if not template_dir.exists():
            continue
        if not images_dir.exists():
            log_lines.append(f"DIAG: {label} -> {images_dir} (images не найдена)")
            continue
        logo_path = None
        for n in LOGO_NAMES:
            p = images_dir / n
            if p.exists():
                logo_path = p
                break
        outer_path = None
        for n in DIAGRAM_OUTER_NAMES:
            p = images_dir / n
            if p.exists():
                outer_path = p
                break
        inner_path = None
        for n in DIAGRAM_INNER_NAMES:
            p = images_dir / n
            if p.exists():
                inner_path = p
                break
        log_lines.append(f"DIAG: {label} -> {images_dir.resolve()}")
        log_lines.append(f"  logo: {'да (' + logo_path.name + ')' if logo_path else 'нет'}")
        log_lines.append(f"  diagram_outer: {'да (' + outer_path.name + ')' if outer_path else 'нет'}")
        log_lines.append(f"  diagram_inner: {'да (' + inner_path.name + ')' if inner_path else 'нет'}")
        return {
            "template_dir": template_dir,
            "images_dir": images_dir,
            "logo_path": logo_path,
            "diagram_outer_path": outer_path,
            "diagram_inner_path": inner_path,
            "log_lines": log_lines,
        }
    log_lines.append("DIAG: ни одна папка templates/images не найдена")
    return {"template_dir": script_dir / "templates", "images_dir": None, "logo_path": None, "diagram_outer_path": None, "diagram_inner_path": None, "log_lines": log_lines}


def _get_template_dirs(base_dir: Path | None):
    diag = run_report_diagnostics(base_dir)
    template_dir = diag["template_dir"]
    images_dir = diag["images_dir"]
    if images_dir is None:
        script_dir = Path(__file__).resolve().parent
        template_dir = script_dir / "templates"
        images_dir = template_dir / "images"
    return template_dir, images_dir


def _render_report_template(data_ru: dict, base_dir: Path | None = None, use_file_url: bool = False, diag: dict | None = None) -> str:
    """Рендерит HTML отчёта. diag — результат run_report_diagnostics, если передан — используем найденные пути к картинкам."""
    import os
    from jinja2 import Environment, FileSystemLoader
    if diag is None:
        diag = run_report_diagnostics(base_dir)
    template_dir = diag["template_dir"]
    images_dir = diag["images_dir"] or (template_dir / "images")
    for line in diag.get("log_lines", []):
        _log(line)

    if "company_name" not in data_ru:
        data_ru["company_name"] = os.environ.get("REPORT_COMPANY_NAME", "World Ride Auto")

    # Нормализация блока «Общее состояние»: пробег «Много» только от 70 000 км; в ячейке «Значение» — только выбранный вариант
    import re
    MILEAGE_HIGH_KM = 70_000
    OPTION_PAIRS = ("хорошо плохо", "плохо хорошо", "Нет Да", "Да Нет")
    for row in data_ru.get("summary", []):
        if row.get("label") == "Пробег":
            raw = re.sub(r"[^\d]", "", str(row.get("value", "")))
            try:
                km = int(raw) if raw else 0
                if km < MILEAGE_HIGH_KM and row.get("status") == "Много":
                    row["status"] = "Норма"
                if km >= 0:
                    row["value"] = f"{km:,}".replace(",", " ") + " км"
            except ValueError:
                pass
        val = (row.get("value") or "").strip()
        val_actual = (row.get("value_actual") or "").strip()
        if val_actual and val in OPTION_PAIRS:
            row["value"] = val_actual
            row["value_actual"] = ""

    logo_path = diag.get("logo_path")
    outer_path = diag.get("diagram_outer_path")
    inner_path = diag.get("diagram_inner_path")
    diagram_outer_size = None
    diagram_inner_size = None

    if use_file_url:
        if "logo_src" not in data_ru and logo_path:
            data_ru["logo_src"] = f"images/{logo_path.name}"
            _log(f"REPORT: логотип (file): {data_ru['logo_src']}")
        if "diagram_outer_src" not in data_ru and outer_path:
            data_ru["diagram_outer_src"] = f"images/{outer_path.name}"
        if "diagram_inner_src" not in data_ru and inner_path:
            data_ru["diagram_inner_src"] = f"images/{inner_path.name}"
    else:
        import base64
        def _embed_resized(path: Path | None, mime: str, max_size: int = 520) -> tuple[str | None, int | None, int | None]:
            """Возвращает (data_uri, width, height). Для SVG или ошибки — (src, None, None)."""
            if path is None or not path.exists():
                return (None, None, None)
            try:
                raw = path.read_bytes()
                if mime == "image/svg+xml":
                    b64 = base64.b64encode(raw).decode("ascii")
                    return (f"data:{mime};base64,{b64}", None, None)
                try:
                    from PIL import Image
                    import io
                    img = Image.open(io.BytesIO(raw))
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGBA")
                    else:
                        img = img.convert("RGB")
                    w, h = img.size
                    if w > max_size or h > max_size:
                        try:
                            resample = Image.Resampling.LANCZOS
                        except AttributeError:
                            resample = getattr(Image, "LANCZOS", 1)
                        if w > h:
                            img = img.resize((max_size, int(h * max_size / w)), resample)
                        else:
                            img = img.resize((int(w * max_size / h), max_size), resample)
                    w, h = img.size
                    buf = io.BytesIO()
                    img.save(buf, format="PNG", optimize=True)
                    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                    return (f"data:image/png;base64,{b64}", w, h)
                except Exception:
                    b64 = base64.b64encode(raw).decode("ascii")
                    return (f"data:{mime};base64,{b64}", None, None)
            except Exception:
                return (None, None, None)
        if "logo_src" not in data_ru:
            if logo_path:
                mime = "image/svg+xml" if logo_path.suffix.lower() == ".svg" else "image/png"
                src, _, _ = _embed_resized(logo_path, mime, max_size=160)
                if src:
                    data_ru["logo_src"] = src
                    _log(f"REPORT: логотип встроен ({logo_path.name})")
            if "logo_src" not in data_ru:
                _log("REPORT: логотип не найден")
        if "diagram_outer_src" not in data_ru and outer_path:
            src, w, h = _embed_resized(outer_path, "image/png", max_size=520)
            if src:
                data_ru["diagram_outer_src"] = src
                if w is not None and h is not None:
                    diagram_outer_size = (w, h)
                _log(f"REPORT: схема встроена ({outer_path.name})")
        if "diagram_inner_src" not in data_ru and inner_path:
            src, w, h = _embed_resized(inner_path, "image/png", max_size=520)
            if src:
                data_ru["diagram_inner_src"] = src
                if w is not None and h is not None:
                    diagram_inner_size = (w, h)
                _log(f"REPORT: схема встроена ({inner_path.name})")

    # Иконки для легенды и схемы (код → data URI SVG)
    import base64 as _b64
    diagram_code_icons = {}
    for code, filename in DIAGRAM_CODE_ICONS.items():
        icon_path = images_dir / filename
        if icon_path.exists():
            try:
                raw = icon_path.read_bytes()
                diagram_code_icons[code] = f"data:image/svg+xml;base64,{_b64.b64encode(raw).decode('ascii')}"
            except Exception:
                pass
    data_ru["diagram_code_icons"] = diagram_code_icons
    data_ru["diagram_legend_labels"] = DIAGRAM_LEGEND_LABELS
    data_ru["diagram_icon_size"] = DIAGRAM_ICON_SIZE

    # Координаты зон: каноническое пространство из JSON (outer 600×200, inner 600×200), масштабируем под размер картинки
    data_dir = Path(__file__).resolve().parent / "data"
    diagram_outer_data, diagram_inner_data = _load_diagram_zone_data(data_dir)
    def _scale_zone_data(zone_data: dict, size: tuple[int, int] | None, canonical: tuple[int, int]) -> tuple[dict, str]:
        if not size:
            cw, ch = canonical
            return zone_data, f"0 0 {cw} {ch}"
        w, h = size
        cw, ch = canonical
        scaled = {}
        for zid, v in zone_data.items():
            scaled[zid] = {
                "d": v["d"],
                "cx": v["cx"] * w / cw,
                "cy": v["cy"] * h / ch,
            }
        return scaled, f"0 0 {w} {h}"
    diagram_outer_data, diagram_outer_viewbox = _scale_zone_data(
        diagram_outer_data, diagram_outer_size, DIAGRAM_OUTER_CANONICAL
    )
    diagram_inner_data, diagram_inner_viewbox = _scale_zone_data(
        diagram_inner_data, diagram_inner_size, DIAGRAM_INNER_CANONICAL
    )
    data_ru["diagram_outer_data"] = diagram_outer_data
    data_ru["diagram_inner_data"] = diagram_inner_data
    data_ru["diagram_outer_viewbox"] = diagram_outer_viewbox
    data_ru["diagram_inner_viewbox"] = diagram_inner_viewbox

    env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=True)
    template = env.get_template("report_ru.html")
    return template.render(**data_ru)


async def _learn_missing_after_report(missing: dict) -> None:
    """Фоновая задача: перевести неизвестные слова и сохранить для следующих отчётов."""
    if not missing or (not missing.get("labels") and not missing.get("status_words")):
        return
    try:
        from report_parser import save_learned_mapping
        new_entries = {"labels": {}, "status_words": {}}
        for ko in list(missing.get("labels", {}))[:30]:
            try:
                ru = await asyncio.wait_for(asyncio.to_thread(_translate_ko_ru_sync, ko), timeout=10)
                if ru and ru != ko:
                    new_entries["labels"][ko] = ru
            except (asyncio.TimeoutError, Exception):
                pass
        for ko in list(missing.get("status_words", {}))[:30]:
            try:
                ru = await asyncio.wait_for(asyncio.to_thread(_translate_ko_ru_sync, ko), timeout=10)
                if ru and ru != ko:
                    new_entries["status_words"][ko] = ru
            except (asyncio.TimeoutError, Exception):
                pass
        if new_entries["labels"] or new_entries["status_words"]:
            save_learned_mapping(new_entries)
            _log(f"LEARNED (для след. отчётов): +{len(new_entries['labels'])} подписей, +{len(new_entries['status_words'])} статусов")
    except Exception as e:
        _log(f"LEARNED: ошибка {e}")


async def fetch_report_pdf_mapped(
    carid: str,
    save_path: Path,
    on_status=None,
    base_dir: Path | None = None,
) -> tuple[bool, Path | None, bool]:
    """
    Режим «парсинг + маппинг + шаблон». Возвращает (успех, путь_к_HTML, картинки_загружены).
    """
    async def _status(msg: str):
        if on_status:
            await on_status(msg)

    try:
        from playwright.async_api import async_playwright
        from report_parser import parse_report_html, load_mapping, apply_mapping
    except ImportError as e:
        _log(f"REPORT_MAPPED: импорт {e}")
        return (False, None, False)

    url = REPORT_URL.format(carid=carid)
    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    report_base = base_dir if base_dir is not None else Path(__file__).resolve().parent
    TIMEOUT_MS = 240000  # 4 минуты (Encar может грузиться долго)

    # #region agent log
    _debug_log("encar_report.py:fetch_report_pdf_mapped", "entry", {"carid": carid, "timeout_ms": TIMEOUT_MS}, "H5")
    # #endregion
    phase = "start"
    try:
        _log("REPORT_MAPPED: старт")
        await _status("Открываю страницу Encar…")
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",  # часто нужно на сервере/Docker
                    "--disable-gpu",
                ],
            )
            try:
                proxy = _report_proxy()
                # #region agent log
                _debug_log("encar_report.py:before_context", "proxy", {"proxy_on": proxy is not None}, "H2")
                # #endregion
                if proxy:
                    _log("REPORT_MAPPED: прокси включён")
                context = await browser.new_context(
                    viewport={"width": 900, "height": 1200},
                    locale="ko-KR",
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    proxy=proxy,
                )
                context.set_default_navigation_timeout(TIMEOUT_MS)
                context.set_default_timeout(TIMEOUT_MS)
                page = await context.new_page()
                phase = "goto"
                # #region agent log
                _debug_log("encar_report.py:before_goto", "before page.goto", {"url": url}, "H1")
                # #endregion
                await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                # #region agent log
                _debug_log("encar_report.py:after_goto", "page.goto ok", {}, "H1")
                # #endregion
                try:
                    await page.wait_for_selector(".inspec_carinfo, #bodydiv", timeout=20000)
                except Exception:
                    pass
                await page.wait_for_timeout(1500)
                _log("REPORT_MAPPED: страница загружена")
                await _status("Извлекаю данные, формирую отчёт на русском…")
                phase = "parse"
                html = await page.content()
                data = parse_report_html(html)
                mapping = load_mapping()
                data_ru, missing = apply_mapping(data, mapping, return_missing=True)
                phase = "diag"
                diag = run_report_diagnostics(report_base)
                await _status("Собираю отчёт (логотип и схемы)…")
                phase = "render"
                rendered = _render_report_template(data_ru, base_dir=report_base, use_file_url=False, diag=diag)
                phase = "write_html"
                html_path = save_path.with_suffix(".html")
                html_path.write_text(rendered, encoding="utf-8")
                _log(f"REPORT: HTML сохранён для резерва: {html_path}")
                await _status("Формирую PDF…")
                phase = "set_content"
                # #region agent log
                _debug_log("encar_report.py:before_set_content", "before set_content", {"html_len": len(rendered)}, "H3")
                # #endregion
                await page.set_content(rendered, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                # #region agent log
                _debug_log("encar_report.py:after_set_content", "set_content ok", {}, "H3")
                # #endregion
                await page.wait_for_timeout(3000)
                imgs_ok = False
                try:
                    imgs_ok = await page.evaluate("""() => {
                        const imgs = document.querySelectorAll('img');
                        if (!imgs.length) return false;
                        return Array.from(imgs).every(i => i.complete && i.naturalWidth > 0);
                    }""")
                except Exception:
                    pass
                _log(f"REPORT: картинки в странице загружены: {imgs_ok}")
                phase = "pdf"
                # #region agent log
                _debug_log("encar_report.py:before_pdf", "before page.pdf", {}, "H4")
                # #endregion
                await page.pdf(path=str(save_path), format="A4", print_background=True)
                # #region agent log
                _debug_log("encar_report.py:after_pdf", "page.pdf ok", {}, "H4")
                # #endregion
                _log("REPORT_MAPPED: готово")
                if missing and (missing.get("labels") or missing.get("status_words")):
                    asyncio.create_task(_learn_missing_after_report(missing))
                return (True, html_path, imgs_ok)
            finally:
                await browser.close()
    except asyncio.TimeoutError as e:
        # #region agent log
        _debug_log("encar_report.py:except", "TimeoutError", {"type": "TimeoutError", "msg": str(e), "phase": phase}, "H1")
        # #endregion
        _log(f"REPORT_MAPPED: таймаут (asyncio) phase={phase} {e}")
        _log(f"REPORT_MAPPED: FAIL phase={phase} type=TimeoutError msg={e}")
        return (False, None, False)
    except Exception as e:
        import traceback
        # #region agent log
        _debug_log("encar_report.py:except", "Exception", {"type": type(e).__name__, "msg": str(e), "phase": phase}, "H2,H3,H4,H5")
        # #endregion
        is_timeout = "timeout" in type(e).__name__.lower() or "timeout" in str(e).lower()
        if is_timeout:
            _log(f"REPORT_MAPPED: таймаут (playwright/сеть) phase={phase} {e}")
        else:
            _log(f"REPORT_MAPPED: ошибка phase={phase} {e}")
        _log(f"REPORT_MAPPED: FAIL phase={phase} type={type(e).__name__} msg={e}")
        traceback.print_exc()
        return (False, None, False)


async def fetch_report_pdf(
    carid: str,
    save_path: Path,
    translate_to_russian: bool = True,
    on_status=None,
    base_dir: Path | None = None,
) -> tuple[bool, Path | None, bool]:
    """
    Возвращает (успех, путь_к_HTML_резерва, картинки_в_PDF_ок).
    """
    for attempt in range(2):
        if attempt > 0:
            _log("REPORT: повторная попытка…")
            if on_status:
                await on_status("Повторная попытка…")
        result = await fetch_report_pdf_mapped(carid, save_path, on_status=on_status, base_dir=base_dir)
        if result[0]:
            return result
    return result
