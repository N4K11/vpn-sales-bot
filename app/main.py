from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.controller import BotController
from app.bot.keyboards import access_result_keyboard, update_notice_keyboard
from app.config import settings
from app.db.session import init_db
from app.logging_config import setup_logging
from app.services.backup import BackupService
from app.services.payments import PaymentService
from app.services.provisioning import ProvisioningService
from app.services.server_agents import ServerAgentClient, ServerAgentError
from app.services.store import Store
from app.services.subscription_links import build_reserve_access_url, build_subscription_url, subscription_server_names
from app.services.subscription_server import create_subscription_web_app
from app.services.updater import UpdateService

logger = logging.getLogger(__name__)

SERVER_ALERT_INTERVAL_SECONDS = 300
SERVER_BILLING_REMINDER_INTERVAL_SECONDS = 21600
CLEANUP_INTERVAL_SECONDS = 21600
UPDATE_CHECK_INTERVAL_SECONDS = 3600
CPU_ALERT_THRESHOLD = 85
RAM_ALERT_THRESHOLD = 85
AGENT_MEMORY_ALERT_THRESHOLD = 85
AGENT_DISK_ALERT_THRESHOLD = 90
PROVISIONING_ALERT_WINDOW_MINUTES = 10
PROVISIONING_ALERT_TOTAL_THRESHOLD = 5
PROVISIONING_ALERT_PER_SERVER_THRESHOLD = 3


def build_subscription_action_rows(subscription) -> list[tuple[str, str]]:
    return [('🌐 Открыть доступ', f'sub:show:{subscription.id}:profile')]


def render_subscription_link_lines(subscription) -> list[str]:
    url = build_subscription_url(subscription)
    if url:
        return [
            'Одна ссылка уже собрала внутри все активные серверы вашего доступа.',
            url,
        ]
    return [
        '⚠️ Общая ссылка пока недоступна.',
        'Администратору нужно настроить PUBLIC_BASE_URL у бота, если серверы находятся на разных панелях или хостах.',
    ]


def render_payment_activation_message(subscription, vpn_keys: list, extended: bool = False, reserve_url: str = '') -> str:
    if extended:
        title = '🕒 Доступ продлён'
    elif getattr(subscription, 'is_trial', False):
        title = '🧪 Пробный доступ активирован'
    else:
        title = '✅ Доступ активирован'

    server_names = subscription_server_names(subscription)
    lines = [
        title,
        '',
        f"📦 Формат: {subscription.tariff.name if getattr(subscription, 'tariff', None) else ('Пробный доступ' if getattr(subscription, 'is_trial', False) else 'Ручной доступ')}",
        f'⏳ Действует до: {subscription.ends_at:%d.%m.%Y %H:%M}',
        f'🌐 Серверов в доступе: {len(server_names) or len(vpn_keys)}',
    ]
    if server_names:
        preview = ', '.join(server_names[:4])
        if len(server_names) > 4:
            preview += f' и ещё {len(server_names) - 4}'
        lines.append(f'🖥️ Доступно: {preview}')
    lines.extend(['', *render_subscription_link_lines(subscription)])
    if reserve_url:
        lines.extend(['', '🌍 Резервный кабинет сохраните заранее:'])
        lines.append(reserve_url)
    return '\n'.join(lines)


def render_expiry_warning_message(subscription) -> str:
    now = datetime.utcnow()
    remaining = max(subscription.ends_at - now, timedelta())
    hours = int(remaining.total_seconds() // 3600)
    remains = f'примерно {hours // 24} дн.' if hours >= 24 else f'примерно {max(hours, 1)} ч.'
    lines = [
        '⏳ Скоро закончится доступ',
        '',
        f'Осталось: {remains}',
        f'Действует до: {subscription.ends_at:%d.%m.%Y %H:%M}',
        '',
        'Продлите доступ заранее, чтобы не потерять подключение.',
        'После продления бот обновит все активные ключи внутри этого доступа.',
    ]
    return '\n'.join(lines)


def _provisioning_stage_title(stage: str) -> str:
    return {
        'provision': 'новая выдача',
        'rotate': 'обновление действующего ключа',
        'replace': 'ручная замена ключа',
        'server_check': 'проверка сервера',
    }.get(stage, stage)


def render_provisioning_alert_message(snapshot: dict[str, object]) -> str:
    server_lines = [
        f"• {item['server_name']} — {item['count']} сбоев"
        for item in snapshot.get('server_breakdown', [])[:5]
    ] or ['• Сервер пока не определён.']
    stage_lines = [
        f"• {_provisioning_stage_title(str(item['stage']))} — {item['count']}"
        for item in snapshot.get('stage_breakdown', [])
    ] or ['• Детализация по этапам пока пустая.']
    error_lines = [
        f"• {item['count']}x — {item['error']}"
        for item in snapshot.get('top_errors', [])
    ] or ['• Сообщения ошибок пока не накопились.']
    return '\n'.join([
        '🚨 Массовые сбои выдачи ключей',
        '',
        f"🕒 Окно наблюдения: {snapshot.get('window_minutes', PROVISIONING_ALERT_WINDOW_MINUTES)} мин.",
        f"❌ Неудачных выдач: {snapshot.get('total_failures', 0)}",
        '',
        'По серверам:',
        *server_lines,
        '',
        'По этапам:',
        *stage_lines,
        '',
        'Частые ошибки:',
        *error_lines,
        '',
        'Проверьте панели 3x-ui, inbound_id и доступность Ubuntu-серверов.',
    ])


async def reserve_access_url_for(store: Store, user) -> str:
    if not user:
        return ''
    if not await store.get_toggle('section_reserve_access', default=True):
        return ''
    return build_reserve_access_url(user)


async def notify_admins(
    bot: Bot,
    store: Store,
    text: str,
    *,
    reply_markup=None,
    parse_mode: ParseMode | None = None,
) -> None:
    targets = set(settings.admin_ids)
    try:
        admin_users = await store.list_admin_users()
        targets.update(user.telegram_id for user in admin_users if getattr(user, 'telegram_id', None))
    except Exception as exc:
        logger.warning('Failed to load admin recipients from DB: %s', exc)
    for admin_id in sorted(targets):
        try:
            await bot.send_message(admin_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception as exc:
            logger.warning('Failed to notify admin %s: %s', admin_id, exc)


async def activate_paid_payment(bot: Bot, store: Store, provisioning: ProvisioningService, payment_id: int) -> bool:
    payment, user, _ = await store.get_payment_bundle(payment_id)
    if not payment or payment.status != 'paid':
        return False
    subscription, vpn_keys, extended = await provisioning.activate_payment(payment_id)
    if not user or not subscription:
        return False
    reserve_url = await reserve_access_url_for(store, getattr(subscription, 'user', None))
    await bot.send_message(
        user.telegram_id,
        render_payment_activation_message(subscription, vpn_keys, extended=extended, reserve_url=reserve_url),
        reply_markup=access_result_keyboard(build_subscription_action_rows(subscription), reserve_url=reserve_url),
    )
    return True


async def payment_polling_loop(bot: Bot, store: Store, payments: PaymentService, provisioning: ProvisioningService) -> None:
    while True:
        try:
            paid_ids = await payments.poll_pending()
            for payment_id in paid_ids:
                await activate_paid_payment(bot, store, provisioning, payment_id)
        except Exception as exc:
            logger.exception('Payment polling loop failed: %s', exc)
        await asyncio.sleep(max(settings.poll_payments_every_seconds, 30))


async def expiry_notifications_loop(bot: Bot, store: Store) -> None:
    while True:
        try:
            subscriptions = await store.list_expiring_subscriptions(within_hours=24)
            for subscription in subscriptions:
                if not subscription.user:
                    await store.mark_expiry_notice_sent(subscription.id)
                    continue
                reserve_url = await reserve_access_url_for(store, getattr(subscription, 'user', None))
                await bot.send_message(
                    subscription.user.telegram_id,
                    render_expiry_warning_message(subscription),
                    reply_markup=access_result_keyboard(build_subscription_action_rows(subscription), reserve_url=reserve_url),
                )
                await store.mark_expiry_notice_sent(subscription.id)
        except Exception as exc:
            logger.exception('Expiry notification loop failed: %s', exc)
        await asyncio.sleep(1800)


async def maintenance_loop(provisioning: ProvisioningService) -> None:
    while True:
        try:
            await provisioning.refresh_servers()
            await provisioning.refresh_key_usage()
        except Exception as exc:
            logger.exception('Maintenance loop failed: %s', exc)
        await asyncio.sleep(300)


async def server_alerts_loop(bot: Bot, store: Store, provisioning: ProvisioningService) -> None:
    last_states: dict[int, str] = {}
    last_provisioning_state = 'ok'
    while True:
        try:
            servers = await store.list_servers()
            for server in servers:
                issue_codes: list[str] = []
                issue_lines: list[str] = []

                if server.health_status != 'online':
                    issue_codes.append('panel_offline')
                    issue_lines.append(f'🔴 Панель 3x-ui недоступна: {server.last_error or "нет ответа"}')
                if server.cpu_percent >= CPU_ALERT_THRESHOLD:
                    issue_codes.append('cpu_high')
                    issue_lines.append(f'🧠 Высокая нагрузка CPU: {server.cpu_percent}%')
                if server.ram_percent >= RAM_ALERT_THRESHOLD:
                    issue_codes.append('ram_high')
                    issue_lines.append(f'🧮 Высокая загрузка RAM: {server.ram_percent}%')

                agent_cfg = await store.get_server_agent_config(server.id)
                if agent_cfg.get('url') and agent_cfg.get('token'):
                    try:
                        agent_status = await ServerAgentClient(agent_cfg['url'], agent_cfg['token']).fetch_status()
                        if agent_status.memory_percent >= AGENT_MEMORY_ALERT_THRESHOLD:
                            issue_codes.append('agent_memory_high')
                            issue_lines.append(f'🤖 RAM Ubuntu-agent: {agent_status.memory_percent}%')
                        if agent_status.disk_percent >= AGENT_DISK_ALERT_THRESHOLD:
                            issue_codes.append('agent_disk_high')
                            issue_lines.append(f'💽 Диск Ubuntu-agent: {agent_status.disk_percent}%')
                    except (ServerAgentError, Exception) as exc:
                        issue_codes.append('agent_offline')
                        issue_lines.append(f'🤖 Ubuntu-agent недоступен: {str(exc).strip() or repr(exc)}')

                state_key = 'ok' if not issue_codes else '|'.join(sorted(set(issue_codes)))
                previous_state = last_states.get(server.id)
                last_states[server.id] = state_key

                if previous_state is None:
                    if state_key != 'ok':
                        await notify_admins(
                            bot,
                            store,
                            '\n'.join([
                                '🚨 Алерт по серверу',
                                '',
                                f'🖥️ Сервер: {server.name}',
                                f'🔗 Панель: {server.base_url}',
                                '',
                                *issue_lines,
                            ]),
                        )
                    continue

                if previous_state == state_key:
                    continue

                if state_key == 'ok':
                    await notify_admins(
                        bot,
                        store,
                        '\n'.join([
                            '✅ Сервер восстановился',
                            '',
                            f'🖥️ Сервер: {server.name}',
                            'Все контрольные показатели снова в норме.',
                        ]),
                    )
                else:
                    await notify_admins(
                        bot,
                        store,
                        '\n'.join([
                            '🚨 Алерт по серверу',
                            '',
                            f'🖥️ Сервер: {server.name}',
                            f'🔗 Панель: {server.base_url}',
                            '',
                            *issue_lines,
                        ]),
                    )

            provisioning_snapshot = provisioning.get_provisioning_alert_snapshot(
                window_minutes=PROVISIONING_ALERT_WINDOW_MINUTES,
                total_threshold=PROVISIONING_ALERT_TOTAL_THRESHOLD,
                per_server_threshold=PROVISIONING_ALERT_PER_SERVER_THRESHOLD,
            )
            provisioning_state = str(provisioning_snapshot.get('state_key', 'ok'))
            if provisioning_state != last_provisioning_state:
                if provisioning_state == 'ok':
                    if last_provisioning_state != 'ok':
                        await notify_admins(
                            bot,
                            store,
                            '\n'.join([
                                '✅ Выдача ключей стабилизировалась',
                                '',
                                'За последнее окно наблюдения массовых ошибок выдачи больше не видно.',
                            ]),
                        )
                else:
                    await notify_admins(bot, store, render_provisioning_alert_message(provisioning_snapshot))
                last_provisioning_state = provisioning_state
        except Exception as exc:
            logger.exception('Server alerts loop failed: %s', exc)
        await asyncio.sleep(SERVER_ALERT_INTERVAL_SECONDS)


async def server_billing_reminders_loop(bot: Bot, store: Store) -> None:
    while True:
        try:
            today = datetime.utcnow().date()
            for item in await store.list_server_billing_items():
                if not item.get('configured'):
                    continue
                next_due = item.get('next_due')
                if next_due is None:
                    continue
                remind_days = int(item.get('remind_days', 3))
                days_left = (next_due - today).days
                if days_left < 0:
                    stage = 'overdue'
                elif days_left <= remind_days:
                    stage = 'due'
                else:
                    continue
                token = f"{next_due.isoformat()}:{stage}"
                if item.get('last_notice') == token:
                    continue
                if stage == 'overdue':
                    text = '\n'.join([
                        '🚨 Просрочена оплата сервера',
                        '',
                        f"🖥️ Сервер: {item['server_name']}",
                        f"💸 Сумма: {item['amount_rub']} ₽ / {item['period_days']} дн.",
                        f"📅 Дата оплаты: {next_due.strftime('%d.%m.%Y')}",
                        f"🔴 Просрочка: {abs(days_left)} дн.",
                        '',
                        'После оплаты откройте карточку сервера и нажмите «✅ Отметить оплату».',
                    ])
                else:
                    text = '\n'.join([
                        '⏰ Скоро нужно оплатить сервер',
                        '',
                        f"🖥️ Сервер: {item['server_name']}",
                        f"💸 Сумма: {item['amount_rub']} ₽ / {item['period_days']} дн.",
                        f"📅 Дата оплаты: {next_due.strftime('%d.%m.%Y')}",
                        f"🕒 Осталось: {days_left} дн.",
                        '',
                        'После оплаты откройте карточку сервера и нажмите «✅ Отметить оплату».',
                    ])
                await notify_admins(bot, store, text)
                await store.set_server_billing_last_notice(int(item['server_id']), token)
        except Exception as exc:
            logger.exception('Server billing reminders loop failed: %s', exc)
        await asyncio.sleep(SERVER_BILLING_REMINDER_INTERVAL_SECONDS)


async def cleanup_loop(store: Store, backups: BackupService) -> None:
    while True:
        try:
            db_stats = await store.cleanup_stale_data()
            file_stats = await backups.cleanup_old_files()
            if any(db_stats.values()) or any(file_stats.values()):
                logger.info('Cleanup finished: db=%s files=%s', db_stats, file_stats)
        except Exception as exc:
            logger.exception('Cleanup loop failed: %s', exc)
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


def render_update_notification(status) -> str:
    short_rev = status.latest_revision[:7] if status.latest_revision else '—'
    commit_line = (status.latest_commit_message or 'Описание коммита не найдено.').splitlines()[0].strip()
    count = 1 if status.update_available else 0
    lines = [
        '📦 Доступно обновление!' if status.update_available else '✅ Новых обновлений нет',
        '',
        f'📦 Доступно обновлений: {count}',
    ]
    if status.update_available:
        lines.extend([
            '',
            'Последние изменения:',
            f'`{short_rev}` Версия {status.latest_version}: {commit_line}',
        ])
    elif status.check_error:
        lines.extend(['', f'⚠️ Не удалось проверить GitHub: {status.check_error}'])
    else:
        lines.extend(['', f'Текущая версия: {status.current_version}'])
    return '\n'.join(lines)


async def update_notifications_loop(bot: Bot, store: Store, updater: UpdateService) -> None:
    last_notified_marker = ''
    while True:
        try:
            status = await updater.get_status()
            marker = f"{status.latest_version}:{status.latest_revision}"
            if status.update_available and marker and marker != last_notified_marker:
                await notify_admins(
                    bot,
                    store,
                    render_update_notification(status),
                    reply_markup=update_notice_keyboard(status.trigger_configured),
                    parse_mode=ParseMode.MARKDOWN,
                )
                last_notified_marker = marker
            elif not status.update_available:
                last_notified_marker = ''
        except Exception as exc:
            logger.warning('Update notifications loop failed: %s', exc)
        await asyncio.sleep(UPDATE_CHECK_INTERVAL_SECONDS)


async def backup_loop(bot: Bot, backups: BackupService) -> None:
    tz = ZoneInfo(settings.backup_timezone)
    while True:
        now = datetime.now(tz)
        target = now.replace(hour=settings.backup_hour, minute=settings.backup_minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            await backups.send_backup_to_admins(bot)
        except Exception as exc:
            logger.exception('Backup loop failed: %s', exc)


async def build_storage():
    if settings.redis_url:
        from aiogram.fsm.storage.redis import RedisStorage

        return RedisStorage.from_url(settings.redis_url)
    return MemoryStorage()


async def start_subscription_server(store: Store, provisioning: ProvisioningService, payments: PaymentService, bot: Bot) -> web.AppRunner:
    app = create_subscription_web_app(store, provisioning)

    async def yookassa_webhook_handler(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({'ok': False, 'error': 'invalid_json'}, status=400)
        try:
            result = await payments.process_yookassa_webhook(payload)
            if result.marked_paid and result.payment_id:
                await activate_paid_payment(bot, store, provisioning, result.payment_id)
            return web.json_response({'ok': True, 'marked_paid': result.marked_paid, 'payment_id': result.payment_id})
        except Exception as exc:
            logger.exception('YooKassa webhook failed: %s', exc)
            return web.json_response({'ok': False, 'error': 'internal_error'}, status=500)

    app.router.add_post('/webhooks/yookassa', yookassa_webhook_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=settings.subscription_host, port=settings.subscription_port)
    await site.start()
    logger.info('Subscription server started on %s:%s', settings.subscription_host, settings.subscription_port)
    if not settings.public_base_url:
        logger.warning('PUBLIC_BASE_URL is empty. Multi-panel subscription links need a public URL of the bot subscription endpoint.')
    return runner


async def main() -> None:
    settings.ensure_directories()
    setup_logging()
    await init_db()

    store = Store()
    await store.seed_defaults()
    payments = PaymentService(store)
    provisioning = ProvisioningService(store)
    backups = BackupService()
    updater = UpdateService()

    session = AiohttpSession(proxy=settings.proxy_url) if settings.proxy_url else None
    bot = Bot(settings.bot_token, session=session)
    storage = await build_storage()
    dp = Dispatcher(storage=storage)
    dp.include_router(BotController(bot=bot, store=store, payments=payments, provisioning=provisioning, backups=backups, updater=updater).router)

    subscription_runner = await start_subscription_server(store, provisioning, payments, bot)
    tasks = [
        asyncio.create_task(payment_polling_loop(bot, store, payments, provisioning)),
        asyncio.create_task(maintenance_loop(provisioning)),
        asyncio.create_task(expiry_notifications_loop(bot, store)),
        asyncio.create_task(server_alerts_loop(bot, store, provisioning)),
        asyncio.create_task(server_billing_reminders_loop(bot, store)),
        asyncio.create_task(cleanup_loop(store, backups)),
        asyncio.create_task(backup_loop(bot, backups)),
        asyncio.create_task(update_notifications_loop(bot, store, updater)),
    ]
    try:
        await dp.start_polling(bot)
    finally:
        for task in tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await subscription_runner.cleanup()
        await bot.session.close()
        if hasattr(storage, 'close'):
            result = storage.close()
            if asyncio.iscoroutine(result):
                await result


def run() -> None:
    asyncio.run(main())


if __name__ == '__main__':
    run()
