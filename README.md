# VPN Sales Bot

Telegram-бот для продажи VPN-доступа через `3x-ui` с пользовательским кабинетом, оплатой, рефералкой, trial, админкой, общей ссылкой подписки, резервным веб-доступом вне Telegram и деплоем на Ubuntu.

## Что умеет бот

- продаёт VPN-доступ с выдачей ключей и общей subscription-ссылки;
- поддерживает `Telegram Stars`, `YooKassa`, `Crypto Pay` и оплату с внутреннего баланса;
- показывает пользователю профиль, сроки, ключи, продление, реферальный баланс;
- даёт админке управление пользователями, серверами, тарифами, оплатами и текстами;
- умеет ежедневные backup, автоочистку, аналитику и обновление через Watchtower;
- даёт резервный доступ по публичному IP даже без домена и без Telegram.

## Резервный доступ без домена

Если Telegram недоступен, бот может выдать пользователю отдельную аварийную ссылку вида:

```text
http://YOUR_SERVER_IP:8080/access/<token>
```

По этой ссылке открывается веб-кабинет, где пользователь может:

- открыть общую subscription-ссылку;
- скопировать отдельный ключ сервера;
- перевыпустить ключ прямо из браузера;
- работать по публичному IP сервера без домена.

Это особенно полезно, если:

- Telegram блокируется или временно не открывается;
- нужно срочно заменить ключ;
- подписка собрана из серверов на разных панелях или разных хостах.

## Как работает общая ссылка подписки

Есть два режима.

### 1. Нативная ссылка `3x-ui`

Если все ключи подписки находятся на одной панели `3x-ui`, бот автоматически отдаёт именно нативную ссылку панели. Это приоритетный и самый совместимый вариант для одиночного сервера или одной панели. Например:

```text
https://SERVER_IP:2096/sub/17
```

### 2. Общая ссылка самого бота

Если серверы находятся на разных панелях или хостах, бот поднимает свой HTTP endpoint и собирает общую ссылку сам:

```text
http://YOUR_SERVER_IP:8080/sub/<token>
```

Именно этот режим нужен для multi-host подписки и резервного доступа без Telegram.


## Установка на Ubuntu прямо сейчас

Если хочешь быстро поставить бота на Ubuntu по IP без домена, теперь есть готовый installer-flow:

```bash
chmod +x deploy/ubuntu/*.sh
sudo ./deploy/ubuntu/install.sh
```

Что это делает:
- ставит Docker и Docker Compose plugin, если их ещё нет;
- создаёт и заполняет `.env` через понятные вопросы;
- открывает нужный порт для общей ссылки и резервного кабинета;
- поднимает бота, PostgreSQL, Redis и Watchtower;
- оставляет готовые скрипты `update.sh` и `doctor.sh`.

Подробная инструкция: [deploy/ubuntu/README.md](D:/дай%20бог%20заработает/deploy/ubuntu/README.md)

## Быстрый локальный запуск

1. Скопируйте [`.env.example`](D:/дай%20бог%20заработает/.env.example) в `.env`.
2. Заполните минимум:
   - `BOT_TOKEN`
   - `ADMIN_IDS`
   - `BOT_USERNAME`
   - `SUPPORT_CHAT_URL`
   - `CHANNEL_URL`
3. Установите зависимости:

```bash
pip install .
```

4. Запустите бота:

```bash
python -m app.main
```

## Ubuntu без домена: готовый IP-only режим

Для запуска именно по IP без домена используй:

- [`.env.ip.example`](D:/дай%20бог%20заработает/.env.ip.example)
- [`docker-compose.ip.yml`](D:/дай%20бог%20заработает/docker-compose.ip.yml)

### Шаг 1. Подготовь сервер

Нужно:

- Ubuntu 22.04+;
- установленный Docker;
- установленный Docker Compose plugin;
- открытый порт `8080/tcp`;
- публичный IP сервера.

### Шаг 2. Скопируй проект на сервер

Например в:

```bash
/opt/vpn-sales-bot
```

### Шаг 3. Подготовь `.env`

На сервере:

```bash
cp .env.ip.example .env
```

Заполни минимум так:

```env
BOT_TOKEN=...
ADMIN_IDS=123456789
BOT_USERNAME=your_bot_username
DATABASE_URL=postgresql+asyncpg://vpn_bot:vpn_bot@postgres:5432/vpn_bot
REDIS_URL=redis://redis:6379/0
PUBLIC_BASE_URL=http://YOUR_SERVER_IP:8080
SUBSCRIPTION_HOST=0.0.0.0
SUBSCRIPTION_PORT=8080
SUPPORT_CHAT_URL=https://t.me/your_support
CHANNEL_URL=https://t.me/your_channel
TERMS_URL=https://t.me/your_terms
UPDATE_TRIGGER_URL=http://watchtower:8080/v1/update
UPDATE_TRIGGER_TOKEN=change_me_now
WATCHTOWER_HTTP_API_TOKEN=change_me_now
```

Пример для IP:

```env
PUBLIC_BASE_URL=http://87.120.84.217:8080
SUBSCRIPTION_HOST=0.0.0.0
SUBSCRIPTION_PORT=8080
```

Важно:

- `PUBLIC_BASE_URL` должен указывать на внешний IP сервера, который реально доступен извне;
- `SUBSCRIPTION_PORT` должен совпадать с проброшенным портом контейнера;
- если `PUBLIC_BASE_URL` не заполнить, бот не сможет отдавать рабочую multi-host ссылку и резервный веб-кабинет.

### Шаг 4. Открой порт в firewall

Если используешь `ufw`:

```bash
sudo ufw allow 8080/tcp
```

### Шаг 5. Запусти стек

```bash
docker compose -f docker-compose.ip.yml up -d --build
```

После этого:

- бот поднимется;
- PostgreSQL и Redis поднимутся рядом;
- HTTP endpoint подписки и резервного кабинета станет доступен по IP;
- админка и обновление через Watchtower будут работать без домена.

### Шаг 6. Что увидит пользователь

После покупки, продления или в профиле пользователь получит:

- общую ссылку подписки;
- резервную ссылку веб-кабинета;
- кнопки копирования и открытия.

Пользователю лучше сразу:

- сохранить резервную ссылку в заметки;
- добавить её в закладки браузера;
- держать её как аварийный способ восстановления доступа.

## Ubuntu с доменом и HTTPS

Если нужен красивый HTTPS-адрес, используй:

- [`.env.server.example`](D:/дай%20бог%20заработает/.env.server.example)
- [`docker-compose.server.yml`](D:/дай%20бог%20заработает/docker-compose.server.yml)
- [`Caddyfile`](D:/дай%20бог%20заработает/Caddyfile)

В этом режиме `PUBLIC_BASE_URL` будет выглядеть так:

```env
PUBLIC_BASE_URL=https://vpn.example.com
```

## Обновление в один клик

Для серверного обновления через админку уже подготовлена схема с Watchtower.

## GitHub и быстрые обновления

Чтобы дальше обновлять бота без ручной возни на сервере, держи проект в GitHub и публикуй Docker-образ через GitHub Actions.

### 1. Инициализация локального репозитория

```bash
git init
git branch -M main
git add .
git commit -m "Initial bot version"
```

### 2. Создание репозитория на GitHub

Создай пустой репозиторий и привяжи его локально:

```bash
git remote add origin https://github.com/YOUR_NAME/YOUR_REPO.git
git push -u origin main
```

### 3. Автопубликация Docker-образа

В проекте уже есть workflow [`.github/workflows/docker-publish.yml`](D:/дай%20бог%20заработает/.github/workflows/docker-publish.yml). После каждого пуша в `main` он собирает и публикует образ в `ghcr.io`.

На сервере в `.env` укажи:

```env
BOT_IMAGE=ghcr.io/your_name/vpn-sales-bot:latest
```

### 4. Как обновлять сервер

После пуша в `main` есть два пути:

- из админки нажать `Обновить сейчас`;
- или на сервере выполнить:

```bash
./deploy/ubuntu/update.sh
```

### 5. Если пакет GHCR приватный

Тогда на сервере сначала авторизуй Docker:

```bash
docker login ghcr.io
```

Используй GitHub username и Personal Access Token с правом чтения пакетов.

Как это работает:

1. Публикуешь новую версию образа.
2. Бот знает `UPDATE_TRIGGER_URL` и `UPDATE_TRIGGER_TOKEN`.
3. В админке нажимаешь `Обновить сейчас`.
4. Watchtower подтягивает новый образ и перезапускает контейнер.

Если нужен registry-flow, смотри:

- [`docker-compose.server.yml`](D:/дай%20бог%20заработает/docker-compose.server.yml)
- [`docker-compose.ip.yml`](D:/дай%20бог%20заработает/docker-compose.ip.yml)
- [`.github/workflows/docker-publish.yml`](D:/дай%20бог%20заработает/.github/workflows/docker-publish.yml)

## YooKassa

Интеграция использует стандартный API-сценарий:

- создание платежа через `POST /v3/payments`;
- `capture=true`;
- `confirmation.type=redirect`;
- `Idempotence-Key`;
- опрос статуса по `GET /v3/payments/{id}`.

Что нужно заполнить:

```env
YOOKASSA_SHOP_ID=...
YOOKASSA_SECRET_KEY=...
YOOKASSA_RETURN_URL=http://YOUR_SERVER_IP:8080/payment-return
```

Если работаешь через домен, можешь использовать HTTPS URL домена вместо IP.

## Что проверить после запуска

1. Открывается ли бот в Telegram.
2. Работает ли `Админ-панель -> Серверы -> Проверить все`.
3. Проходит ли тестовая покупка.
4. Отдаётся ли общая ссылка подписки.
5. Открывается ли резервный кабинет по ссылке `http://IP:8080/access/...`.
6. Работает ли перевыпуск ключа из резервного кабинета.

## Что важно помнить

- IP тоже могут блокировать, не только домены;
- для максимальной живучести позже лучше сделать второй резервный IP или второй VPS;
- пользователь должен сохранить резервную ссылку заранее, до аварии;
- если Telegram уже недоступен, а ссылка не была сохранена, бот сам по себе уже не поможет.

## Полезные файлы

- [app/main.py](D:/дай%20бог%20заработает/app/main.py)
- [app/bot/controller.py](D:/дай%20бог%20заработает/app/bot/controller.py)
- [app/bot/keyboards.py](D:/дай%20бог%20заработает/app/bot/keyboards.py)
- [app/services/provisioning.py](D:/дай%20бог%20заработает/app/services/provisioning.py)
- [app/services/subscription_links.py](D:/дай%20бог%20заработает/app/services/subscription_links.py)
- [app/services/subscription_server.py](D:/дай%20бог%20заработает/app/services/subscription_server.py)
- [app/services/payments.py](D:/дай%20бог%20заработает/app/services/payments.py)
- [docker-compose.ip.yml](D:/дай%20бог%20заработает/docker-compose.ip.yml)
- [docker-compose.server.yml](D:/дай%20бог%20заработает/docker-compose.server.yml)
- [Caddyfile](D:/дай%20бог%20заработает/Caddyfile)

## Импорт legacy backup

Бот умеет импортировать старую SQLite-базу и snapshot-файлы `x-ui` в текущую схему.

Команда импорта:

```bash
python -m app.legacy_import --legacy-bot-db "C:\Users\weew1\Downloads\Telegram Desktop\backup_2026-04-04\vpn_bot.db" --xui-dir "C:\Users\weew1\Downloads\Telegram Desktop\backup_2026-04-04" --wipe-current
```

Если для сервера найден snapshot `x-ui`, бот пытается восстановить рабочую ссылку ключа автоматически. Если snapshot не найден, ключ импортируется как `нужен перевыпуск`, чтобы его можно было безопасно переиздать уже в новом боте.
