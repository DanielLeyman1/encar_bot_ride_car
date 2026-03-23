# -*- coding: utf-8 -*-
"""
Парсер HTML страницы отчёта Encar (mdsl_regcar.do).
Извлекает: основные данные (basic), таблица общего состояния (summary),
секция ремонта (repair), детальная таблица (detail).
"""
import json
import re
from pathlib import Path
from typing import Any

def _strip(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", s).strip()


def _text(el) -> str:
    if el is None:
        return ""
    return _strip(el.get_text() if hasattr(el, "get_text") else str(el))


def _parse_basic_table(soup, out: dict) -> None:
    selectors = (
        ".inspec_carinfo table.ckst",
        ".inspec_carinfo table",
        "div.inspec_carinfo table",
        "table.ckst",
    )
    for sel in selectors:
        table_basic = soup.select_one(sel)
        if not table_basic:
            continue
        rows = table_basic.select("tbody tr")
        if not rows:
            continue
        for tr in rows:
            cells = tr.find_all(["th", "td"])
            i = 0
            while i < len(cells) - 1:
                if cells[i].name == "th":
                    key = _text(cells[i])
                    val = _text(cells[i + 1]) if cells[i + 1].name == "td" else ""
                    if key:
                        out["basic"][key] = val
                    i += 2
                else:
                    i += 1
        if out["basic"]:
            return


def _find_summary_table(soup):
    t = soup.select_one("table.tbl_total") or soup.select_one("table[class*='tbl_total']")
    if t:
        return t
    for table in soup.select("table"):
        classes = " ".join(table.get("class") or []).lower()
        if "tbl" in classes and "total" in classes:
            return table
    return None


def _parse_summary_table(soup, out: dict) -> None:
    table_total = _find_summary_table(soup)
    if not table_total:
        return
    for tr in table_total.select("tbody tr"):
        th = tr.select_one("th[scope=row]") or tr.select_one("th")
        if not th:
            continue
        label = _text(th)
        status_el = tr.select_one(".txt_state.on") or tr.select_one(".txt_state")
        status = _text(status_el) if status_el else ""
        detail_el = tr.select_one(".txt_detail")
        value = _text(detail_el) if detail_el else ""
        value_actual = ""
        if detail_el:
            on_el = detail_el.select_one(".txt_state.on")
            if on_el:
                value_actual = _text(on_el)
        if not value and status:
            for td in tr.select("td.td_left, td"):
                t = _text(td)
                if t and t != status and not t.startswith("span"):
                    value = t
                    break
        out["summary"].append(
            {"label": label, "status": status, "value": value, "value_actual": value_actual}
        )


def _find_repair_table(soup):
    t = soup.select_one(".section_repair table.tbl_repair")
    if t:
        return t
    t = soup.select_one("table.tbl_repair") or soup.select_one("table[class*='tbl_repair']")
    if t:
        return t
    sec = soup.select_one(".section_repair")
    if sec:
        t = sec.select_one("table")
        if t:
            return t
    return None


def _parse_repair_table(soup, out: dict) -> None:
    section_repair = _find_repair_table(soup)
    if not section_repair:
        return
    for tr in section_repair.select("tbody tr"):
        th = tr.select_one("th[scope=row]") or tr.select_one("th")
        if not th:
            continue
        label = _text(th)
        if "자세히" in label or "uibtn" in label:
            label = re.sub(r"\s*자세히보기\s*", "", label)
        status_el = tr.select_one(".txt_state.on") or tr.select_one(".txt_state")
        value = _text(status_el) if status_el else ""
        out["repair"].append({"label": label, "value": value})


def _find_detail_table(soup):
    t = soup.select_one("table.tbl_detail") or soup.select_one("table[class*='tbl_detail']")
    if t:
        return t
    for table in soup.select("table"):
        classes = " ".join(table.get("class") or []).lower()
        if "detail" in classes and "tbl" in classes:
            return table
    return None


def _parse_detail_table(soup, out: dict) -> None:
    table_detail = _find_detail_table(soup)
    if not table_detail:
        return
    current_device = ""
    for tr in table_detail.select("tbody tr"):
        ths = tr.select("th[scope=row]") or tr.select("th")
        status_el = tr.select_one(".txt_state.on") or tr.select_one(".txt_state")
        status = _text(status_el) if status_el else ""
        if ths:
            first = _text(ths[0])
            if first and first not in (
                "양호",
                "불량",
                "없음",
                "적정",
                "부족",
                "미세누유",
                "누유",
                "미세누수",
                "누수",
            ):
                if len(ths) >= 2:
                    current_device = first
                    item = _text(ths[1])
                else:
                    item = first
                out["detail"].append({"device": current_device, "item": item, "status": status})


def _performance_check_anchor(html: str) -> int:
    low = html.lower()
    for key in ("performancecheck.init", "performancecheck"):
        i = low.find(key)
        if i != -1:
            return i
    return -1


def _parse_diagram(html: str, out: dict) -> None:
    out["diagram"] = {"zones": [], "legend_used": []}
    idx = _performance_check_anchor(html)
    if idx == -1:
        return
    start = html.find("data", idx)
    if start == -1:
        return
    colon = html.find(":", start)
    brace = html.find("{", colon) if colon != -1 else -1
    if brace == -1:
        return
    depth = 0
    end = brace
    for i in range(brace, min(brace + 50000, len(html))):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    raw = html[brace : end + 1]
    try:
        zone_data = json.loads(raw)
        legend_used = set()
        for zone_id, value in zone_data.items():
            if value is None or (isinstance(value, list) and len(value) == 0):
                continue
            codes = value if isinstance(value, list) else [str(value)]
            out["diagram"]["zones"].append({"zone": zone_id, "codes": codes})
            for c in codes:
                legend_used.add(c)
        out["diagram"]["legend_used"] = sorted(legend_used)
    except Exception:
        pass


def parse_report_html(html: str) -> dict[str, Any]:
    """
    Парсит HTML отчёта Encar. Возвращает:
    - basic: dict { "차명": "ES300h...", "연식": "2021년", ... }
    - summary: list of { "label": "주행거리", "status": "양호", "value": "95,023km" }
    - repair: list of { "label": "사고이력", "value": "있음" }
    - detail: list of { "device": "원동기", "item": "작동상태", "status": "양호" }
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, Any] = {"basic": {}, "summary": [], "repair": [], "detail": []}

    _parse_basic_table(soup, out)
    _parse_summary_table(soup, out)
    _parse_repair_table(soup, out)
    _parse_detail_table(soup, out)
    _parse_diagram(html, out)

    return out


def _data_dir() -> Path:
    return Path(__file__).resolve().parent / "data"


def load_mapping() -> dict:
    """Загружает маппинг: report_mapping.json + learned_mapping.json (если есть)."""
    data_dir = _data_dir()
    path = data_dir / "report_mapping.json"
    if not path.exists():
        mapping = {"labels": {}, "status_words": {}, "zone_names": {}, "diagram_codes": {}}
    else:
        with open(path, "r", encoding="utf-8") as f:
            mapping = json.load(f)
    learned_path = data_dir / "learned_mapping.json"
    if learned_path.exists():
        try:
            with open(learned_path, "r", encoding="utf-8") as f:
                learned = json.load(f)
            mapping["labels"] = {**mapping.get("labels", {}), **learned.get("labels", {})}
            mapping["status_words"] = {**mapping.get("status_words", {}), **learned.get("status_words", {})}
        except Exception:
            pass
    return mapping


def save_learned_mapping(new_entries: dict) -> None:
    """
    Добавляет новые пары корейский→русский в learned_mapping.json.
    new_entries: {"labels": { "ko": "ru", ... }, "status_words": { "ko": "ru", ... }}
    """
    data_dir = _data_dir()
    learned_path = data_dir / "learned_mapping.json"
    existing = {"labels": {}, "status_words": {}}
    if learned_path.exists():
        try:
            with open(learned_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass
    existing["labels"] = {**existing.get("labels", {}), **new_entries.get("labels", {})}
    existing["status_words"] = {**existing.get("status_words", {}), **new_entries.get("status_words", {})}
    with open(learned_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


def apply_mapping(data: dict, mapping: dict, return_missing: bool = False):
    """
    Применяет маппинг: корейские ключи и значения статусов → русские.
    Если return_missing=True, возвращает (data_ru, missing), где missing — словари
    неизвестных подписей и статусов для самообучения: {"labels": {ko: ru|None}, "status_words": {...}}.
    """
    labels = mapping.get("labels", {})
    status_words = mapping.get("status_words", {})
    missing = {"labels": {}, "status_words": {}} if return_missing else None

    def map_label(k: str) -> str:
        k_clean = _strip(k)
        res = labels.get(k_clean, labels.get(k_clean.replace("\n", " "), k_clean))
        if return_missing and res == k_clean and k_clean and k_clean not in missing["labels"]:
            missing["labels"][k_clean] = None
        return res

    def map_value(v: str) -> str:
        if not v:
            return v
        v_clean = _strip(v)
        res = status_words.get(v_clean, v_clean)
        if return_missing and res == v_clean and v_clean not in missing["status_words"]:
            missing["status_words"][v_clean] = None
        return res

    zone_names = mapping.get("zone_names", {})
    diagram_codes = mapping.get("diagram_codes", {})

    out = {"basic": {}, "summary": [], "repair": [], "detail": [], "diagram": {"zones": [], "legend_used": []}}

    for k, v in data.get("basic", {}).items():
        out["basic"][map_label(k)] = map_value(v) if isinstance(v, str) else v

    for row in data.get("summary", []):
        val = row.get("value", "")
        val_actual = row.get("value_actual", "")
        out["summary"].append({
            "label": map_label(row.get("label", "")),
            "status": map_value(row.get("status", "")),
            "value": map_value(val) if isinstance(val, str) else val,
            "value_actual": map_value(val_actual) if isinstance(val_actual, str) else val_actual,
        })

    for row in data.get("repair", []):
        out["repair"].append({
            "label": map_label(row.get("label", "")),
            "value": map_value(row.get("value", "")),
        })

    for row in data.get("detail", []):
        out["detail"].append({
            "device": map_label(row.get("device", "")),
            "item": map_label(row.get("item", "")),
            "status": map_value(row.get("status", "")),
        })

    for z in data.get("diagram", {}).get("zones", []):
        zone_id = z.get("zone", "")
        codes = z.get("codes", [])
        zone_ru = zone_names.get(zone_id, zone_id)
        codes_ru = [diagram_codes.get(c, c) for c in codes]
        out["diagram"]["zones"].append({
            "zone": zone_ru,
            "zone_id": zone_id,
            "codes": codes_ru,
            "codes_raw": list(codes),
            "code": codes[0] if codes else "",
        })
    for c in data.get("diagram", {}).get("legend_used", []):
        out["diagram"]["legend_used"].append(diagram_codes.get(c, c))

    if return_missing:
        return out, missing
    return out
