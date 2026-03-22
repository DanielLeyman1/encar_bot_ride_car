# Развёртывание бота на сервере (Linux)

После `git pull` на сервере обязательно выполните:

## 1. Зависимости Python

После каждого `git pull` переустановите зависимости (иначе возможны ошибки вроде `No module named 'bs4'`):

```bash
cd /root/encar_bot_ride_car   # или ваш путь к боту
source venv/bin/activate     # если используете venv
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

- `REPORT_MAPPED: FAIL phase=... type=... msg=...` — на каком этапе упало и текст ошибки
- Этапы: `goto` (загрузка Encar), `parse` (парсинг/маппинг), `diag`/`render` (шаблон), `set_content` (HTML в браузер), `pdf` (генерация PDF)
- Если `phase=pdf` — часто не хватает зависимостей Chromium на сервере: `playwright install-deps chromium` или шрифты
- `REPORT_MAPPED: таймаут` — таймаут загрузки страницы или прокси
- `REPORT_MAPPED: импорт` — не установлен playwright или report_parser

Команда в боте: `/report_diag` — проверка логотипа и схем (шаблоны); если там всё «да», проблема в загрузке Encar или в Playwright/прокси.

## 5. Один экземпляр бота (Conflict)

Ошибка `Conflict: terminated by other getUpdates request` значит, что запущено два процесса бота с одним токеном. Перед новым запуском остановите старый процесс:

```bash
# если бот запущен вручную — найдите PID и завершите:
cat bot.pid   # показать PID
kill $(cat bot.pid)   # остановить
# затем запускайте бота снова
```

При старте бот пишет свой PID в `bot.pid` и проверяет, не запущен ли уже другой экземпляр; если да — выходит с сообщением.

## 6. Nginx: 502 Bad Gateway по ссылке `/r/...`

**502** значит: nginx не смог подключиться к процессу за `proxy_pass` (часто порт пустой или занят другим сервисом).

### Проверка на сервере

1. **Бот запущен?** В логе при старте должно быть: `Сервер отчётов: http://0.0.0.0:9090/r/<token>` (или ваш порт из `REPORT_SERVER_PORT`).

2. **Слушается ли порт** (подставьте свой порт, по умолчанию в коде **9090**):

   ```bash
   ss -tlnp | grep 9090
   curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:9090/r/nosuch
   ```

   Ожидается ответ **200** или **404**, но не «Connection refused».

3. **Точная причина в логе nginx:**

   ```bash
   sudo tail -30 /var/log/nginx/error.log
   ```

   Типично: `connect() failed (111: Connection refused) while connecting to upstream` — бэкенд не слушает этот адрес/порт.

4. **Конфликт с API каталога:** если на том же сервере Encar API слушает **8080**, боту задайте другой порт, например **9090**:

   ```bash
   export REPORT_SERVER_PORT=9090
   ```

   И в nginx `proxy_pass` укажите **тот же** порт.

### Фрагмент nginx для отчётов бота

Внутри `server { ... }` для `www.wrideauto.ru` (или вашего домена) добавьте **до** общего `location /`:

```nginx
    location /r/ {
        proxy_pass http://127.0.0.1:9090/r/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
```

Число **9090** должно совпадать с `REPORT_SERVER_PORT` у запущенного бота.

Затем:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### Переменные, которые должны совпасть

| Что | Значение |
|-----|----------|
| `REPORT_BASE_URL` в боте | Публичный URL с тем же хостом, что в браузере (`https://www.wrideauto.ru`) |
| `REPORT_SERVER_PORT` в боте | Тот же порт, что в `proxy_pass` nginx |
| Процесс `python bot.py` | Должен быть запущен постоянно (systemd / screen / tmux) |
