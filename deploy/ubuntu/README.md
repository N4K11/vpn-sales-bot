# Ubuntu install

Этот набор файлов нужен, чтобы быстро поднять бота на Ubuntu по IP без домена.

## Самый быстрый сценарий

1. Скопируй проект на сервер, например в `/opt/vpn-sales-bot`.
2. Зайди в папку проекта.
3. Запусти:

```bash
chmod +x deploy/ubuntu/*.sh
sudo ./deploy/ubuntu/install.sh
```

Скрипт сам:
- установит Docker и Docker Compose plugin, если их ещё нет;
- создаст или обновит `.env`;
- спросит основные параметры бота;
- откроет порт для общей ссылки и резервного кабинета;
- поднимет стек через `docker-compose.ip.yml`.

## Что спросит installer

- `BOT_TOKEN`
- `ADMIN_IDS`
- `BOT_USERNAME`
- внешний IP сервера
- порт общей ссылки и резервного кабинета
- ссылку на поддержку
- ссылку на канал
- ссылку на правила

## После установки

Полезные команды:

```bash
./deploy/ubuntu/doctor.sh
./deploy/ubuntu/update.sh
docker compose -f docker-compose.ip.yml logs -f bot
```

## Что должно работать после старта

- бот отвечает в Telegram;
- админка открывается;
- общая ссылка подписки открывается по `http://IP:PORT/sub/...`;
- резервный кабинет открывается по `http://IP:PORT/access/...`.
