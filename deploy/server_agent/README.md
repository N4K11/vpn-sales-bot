# Ubuntu agent для VPN-бота

Что это даёт:
- health-статус сервера прямо в админке бота;
- uptime/load/disk/services;
- рестарт `x-ui` и `xray` одной кнопкой;
- выполнение своей команды из карточки сервера.

## Установка на Ubuntu

1. Скопируйте папку `deploy/server_agent` на сервер.
2. Выполните:

```bash
cd deploy/server_agent
chmod +x install.sh
sudo ./install.sh --port 8799
```

Можно сразу передать токен:

```bash
sudo ./install.sh --port 8799 --token YOUR_LONG_RANDOM_TOKEN
```

3. После установки агент покажет:
- URL вида `http://SERVER_IP:8799`
- TOKEN

4. В боте откройте:
`Админка -> Серверы -> нужный сервер -> Подключить агент Ubuntu`

И отправьте строку:

```text
http://SERVER_IP:8799|TOKEN
```

## Проверка

```bash
curl -H "X-Agent-Token: TOKEN" http://127.0.0.1:8799/health
```

## Безопасность

- Агент очень мощный: через него можно выполнять команды на сервере.
- Держите длинный случайный token.
- Ограничьте доступ по firewall только для IP сервера, где работает бот.
- Если нужен HTTPS, поставьте reverse proxy перед агентом.
