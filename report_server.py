# -*- coding: utf-8 -*-
"""
HTTP-сервер для раздачи отчётов по короткой ссылке /r/<token>.
Запускается в отдельном потоке из bot.py.
"""
import base64
import os
from pathlib import Path

from flask import Flask, send_file

from report_cache import get_report_path

app = Flask(__name__)
# Каталоги задаются при старте через init_report_server()
_REPORTS_DIR = None
_DATA_DIR = None
_EXPIRED_HTML = None


def _build_expired_html(template_dir: Path, data_dir: Path) -> str:
    """Собирает HTML страницы «отчёт устарел» с логотипом в стиле сайта."""
    from jinja2 import Environment, FileSystemLoader
    logo_src = ""
    logo_path = template_dir / "images" / "logo.svg"
    if logo_path.exists():
        try:
            raw = logo_path.read_bytes()
            logo_src = f"data:image/svg+xml;base64,{base64.b64encode(raw).decode('ascii')}"
        except Exception:
            pass
    env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=True)
    template = env.get_template("expired.html")
    return template.render(logo_src=logo_src, og_image=logo_src)


def init_report_server(reports_dir: Path, data_dir: Path, template_dir: Path) -> None:
    global _REPORTS_DIR, _DATA_DIR, _EXPIRED_HTML
    _REPORTS_DIR = Path(reports_dir)
    _DATA_DIR = Path(data_dir)
    _EXPIRED_HTML = _build_expired_html(template_dir, data_dir)


@app.route("/r/<token>")
def serve_report(token: str):
    if _DATA_DIR is None:
        return _EXPIRED_HTML or "Отчёт устарел.", 404, {"Content-Type": "text/html; charset=utf-8"}
    report_path, expired = get_report_path(token, _DATA_DIR)
    if expired or report_path is None or not report_path.exists():
        return _EXPIRED_HTML or "Отчёт устарел.", 200, {"Content-Type": "text/html; charset=utf-8"}
    return send_file(
        report_path,
        mimetype="text/html; charset=utf-8",
        as_attachment=False,
        download_name=None,
    )


def run_server(port: int = None, reports_dir: Path = None, data_dir: Path = None, template_dir: Path = None) -> None:
    port = port or int(os.environ.get("REPORT_SERVER_PORT", "9090"))
    base = Path(__file__).resolve().parent
    reports_dir = reports_dir or base / "reports"
    data_dir = data_dir or base / "data"
    template_dir = template_dir or base / "templates"
    init_report_server(reports_dir, data_dir, template_dir)
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
