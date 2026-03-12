# -*- coding: utf-8 -*-
"""
Регистрация отчётов по token: ссылка действительна LINK_DAYS дней;
после истечения показывается страница «отчёт устарел». Кэширование по carid отключено.
"""
import json
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path

LINK_DAYS = int(os.environ.get("REPORT_LINK_DAYS", "7"))


def _cache_path(data_dir: Path) -> Path:
    return data_dir / "report_cache.json"


def _load(cache_path: Path) -> dict:
    if not cache_path.exists():
        return {"by_token": {}}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {"by_token": data.get("by_token", {})}
    except Exception:
        return {"by_token": {}}


def _save(cache_path: Path, data: dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_report(carid: str, html_content: str, reports_dir: Path, data_dir: Path) -> str:
    """
    Генерирует token, сохраняет HTML в reports_dir/<token>.html, регистрирует token для раздачи.
    Возвращает token.
    """
    token = secrets.token_urlsafe(12)
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_file = reports_dir / f"{token}.html"
    report_file.write_text(html_content, encoding="utf-8")

    now = datetime.now()
    expires_at = now + timedelta(days=LINK_DAYS)
    path_str = str(report_file.resolve())

    cache_path = _cache_path(data_dir)
    data = _load(cache_path)
    by_token = data.setdefault("by_token", {})

    by_token[token] = {
        "carid": carid,
        "path": path_str,
        "expires_at": expires_at.isoformat(),
    }
    _save(cache_path, data)
    return token


def get_report_path(token: str, data_dir: Path) -> tuple[Path | None, bool]:
    """
    По token возвращает (путь к HTML, истёк ли срок).
    Если token не найден или срок истёк: (None, True).
    """
    path = _cache_path(data_dir)
    data = _load(path)
    by_token = data.get("by_token", {})
    entry = by_token.get(token)
    if not entry:
        return (None, True)
    try:
        expires_at = datetime.fromisoformat(entry["expires_at"])
        if datetime.now() > expires_at:
            return (None, True)
        return (Path(entry["path"]), False)
    except Exception:
        return (None, True)
