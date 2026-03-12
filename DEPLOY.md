# Развёртывание бота на сервере (Linux)

После `git pull` на сервере обязательно выполните:

## 1. Зависимости Python

```bash
pip install -r requirements.txt
```

## 2. Браузер для Playwright (обязательно)

Без установленного Chromium отчёты Encar не формируются (ошибка «таймаут / страница недоступна»).

```bash
playwright install chromium
```

На минимальных Linux-серверах могут понадобиться системные библиотеки:

```bash
playwright install-deps chromium
```

(или только для Chromium: `sudo apt install -y libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2` на Debian/Ubuntu)

## 3. Прокси до Encar (Корея)

По умолчанию используется прокси `REPORT_PROXY_SERVER`. Если с сервера он недоступен или даёт таймауты:

- Попробуйте отключить прокси:
  ```bash
  export REPORT_PROXY=0
  ```
- Или задайте свой прокси через переменные: `REPORT_PROXY_SERVER`, `REPORT_PROXY_USER`, `REPORT_PROXY_PASSWORD`.

## 4. Проверка логов при ошибке отчёта

В логах ищите строки:

- `REPORT_MAPPED: FAIL type=... msg=...` — точный тип и текст ошибки
- `REPORT_MAPPED: таймаут` — таймаут загрузки страницы или прокси
- `REPORT_MAPPED: импорт` — не установлен playwright или report_parser

Команда в боте: `/report_diag` — проверка логотипа и схем (шаблоны); если там всё «да», проблема в загрузке Encar или в Playwright/прокси.
