from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter, deque
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.models import Subscription, User, VpnKey
from app.db.session import SessionLocal
from app.services.store import Store
from app.services.xui import XUIClient
from app.utils import is_future_datetime

logger = logging.getLogger(__name__)
_EXTENSION_RE = re.compile(r"-ext(?P<subscription_id>\d+)-")


def _describe_exception(exc: Exception) -> str:
    message = str(exc).strip()
    return message or repr(exc)


class ProvisioningService:
    def __init__(self, store: Store) -> None:
        self.store = store
        self._failure_events: deque[dict[str, object]] = deque(maxlen=600)

    async def activate_payment(self, payment_id: int) -> tuple[Subscription | None, list[VpnKey], bool]:
        existing = await self.store.get_subscription_by_payment(payment_id)
        if existing:
            subscription = await self._load_subscription(existing.id)
            return subscription, self._active_keys(subscription), False

        payment, user, tariff = await self.store.get_payment_bundle(payment_id)
        if not payment or not user or not tariff:
            return None, [], False

        await self.store.mark_payment_paid(payment_id)
        extend_subscription_id = self._extract_extension_target(payment.payload)
        if extend_subscription_id:
            target = await self.store.get_subscription_details(extend_subscription_id)
            if target and target.user_id == user.id and not target.is_trial:
                subscription = await self.store.extend_subscription(target.id, tariff.days)
                if not subscription:
                    return None, [], False
                keys = await self._sync_subscription_keys(user, subscription, 'Продлённый доступ', trial_only=False)
                await self.store.apply_referral_bonus(user.id, Decimal(str(payment.amount)), payment.id)
                subscription = await self._load_subscription(subscription.id)
                return subscription, self._active_keys(subscription), True

        subscription = await self.store.create_subscription(
            user_id=user.id,
            tariff_id=tariff.id,
            server_id=None,
            days=tariff.days,
            is_trial=False,
            payment_id=payment.id,
        )
        keys = await self._sync_subscription_keys(user, subscription, 'Платный доступ', trial_only=False)
        await self._sync_primary_server(subscription.id, keys)
        await self.store.apply_referral_bonus(user.id, Decimal(str(payment.amount)), payment.id)
        subscription = await self._load_subscription(subscription.id)
        return subscription, self._active_keys(subscription), False

    async def grant_trial(self, user_telegram_id: int) -> tuple[Subscription | None, list[VpnKey], str | None]:
        user = await self.store.get_user_by_telegram_id(user_telegram_id)
        if not user:
            return None, [], 'Пользователь не найден.'
        if user.trial_claimed:
            return None, [], 'Пробный доступ уже был активирован раньше.'
        if not await self.store.get_toggle('section_trial', default=True):
            return None, [], 'Пробный доступ сейчас скрыт администратором.'

        trial_days = await self.store.get_int_setting('trial_days', 3)
        subscription = await self.store.create_subscription(
            user_id=user.id,
            tariff_id=None,
            server_id=None,
            days=trial_days,
            is_trial=True,
            payment_id=None,
        )
        await self.store.set_trial_claimed(user.id)
        keys = await self._sync_subscription_keys(user, subscription, 'Пробный доступ', trial_only=True)
        if not keys:
            return None, [], 'Не удалось выдать пробный доступ. Проверьте панели 3x-ui.'
        await self._sync_primary_server(subscription.id, keys)
        subscription = await self._load_subscription(subscription.id)
        return subscription, self._active_keys(subscription), None

    async def issue_manual_subscription(self, user_id: int, days: int) -> tuple[Subscription | None, list[VpnKey], str | None]:
        if days <= 0:
            return None, [], 'Срок доступа должен быть больше 0 дней.'
        user = await self.store.get_user_by_id(user_id)
        if not user:
            return None, [], 'Пользователь не найден.'

        subscription = await self.store.create_subscription(
            user_id=user.id,
            tariff_id=None,
            server_id=None,
            days=days,
            is_trial=False,
            payment_id=None,
        )
        keys = await self._sync_subscription_keys(user, subscription, 'Ручной доступ', trial_only=False)
        if not keys:
            return None, [], 'Не удалось выдать доступ. Проверьте доступность серверов.'
        await self._sync_primary_server(subscription.id, keys)
        subscription = await self._load_subscription(subscription.id)
        return subscription, self._active_keys(subscription), None

    async def replace_key(self, key_id: int) -> tuple[VpnKey | None, Subscription | None, str | None]:
        key = await self.store.get_key_details(key_id)
        if not key or not key.subscription or not key.subscription.user or not key.server:
            return None, None, 'Ключ не найден.'
        if not self._is_subscription_active(key.subscription):
            return None, key.subscription, 'Срок действия доступа уже закончился. Сначала продлите его.'
        if not key.is_active:
            return None, key.subscription, 'Этот ключ уже неактивен. Откройте актуальный ключ в подписке.'

        try:
            result = await XUIClient(key.server).replace_key(key, key.subscription, key.subscription.user)
        except Exception as exc:
            error_text = _describe_exception(exc)
            logger.warning('Unable to replace key %s: %s', key.id, error_text)
            self._record_failure_event(
                stage='replace',
                error=error_text,
                server_id=getattr(key.server, 'id', None),
                server_name=getattr(key.server, 'name', None),
                subscription_id=getattr(key.subscription, 'id', None),
                user_id=getattr(key.subscription.user, 'telegram_id', None),
            )
            return None, key.subscription, 'Не удалось переиздать ключ на сервере. Попробуйте позже.'

        await self.store.update_key_credentials(key.id, result.access_url, result.external_id, used_bytes=0, is_active=True)
        await self.store.increment_server_reissue_count(key.server.id)
        updated_key = await self.store.get_key_details(key.id)
        subscription = await self._load_subscription(key.subscription_id)
        return updated_key, subscription, None

    async def delete_expired_key(self, key_id: int) -> tuple[Subscription | None, str | None]:
        key = await self.store.get_key_details(key_id)
        if not key or not key.subscription:
            return None, 'Ключ не найден.'
        if self._is_subscription_active(key.subscription):
            return None, 'Удаление доступно только после окончания срока действия.'
        if key.server and key.subscription.user:
            try:
                await XUIClient(key.server).delete_key(key, key.subscription, key.subscription.user)
            except Exception as exc:
                logger.warning('Unable to delete expired key %s from panel: %s', key.id, _describe_exception(exc))
        await self.store.delete_key_record(key.id)
        if await self.store.delete_empty_expired_subscription(key.subscription_id):
            return None, None
        subscription = await self._load_subscription(key.subscription_id)
        return subscription, None

    async def refresh_servers(self) -> None:
        for server in [item for item in await self.store.list_servers() if item.is_enabled]:
            await self.refresh_server(server.id)

    async def refresh_server(self, server_id: int) -> None:
        server = await self.store.get_server(server_id)
        if not server:
            return
        try:
            status, cpu_percent, ram_percent, error = await XUIClient(server).fetch_server_status()
            await self.store.update_server_health(server.id, status, cpu_percent, ram_percent, error)
        except Exception as exc:
            await self.store.record_failed_server_check(server.id, _describe_exception(exc))

    async def refresh_key_usage(self) -> None:
        async with SessionLocal() as session:
            keys = list(
                await session.scalars(
                    select(VpnKey)
                    .options(
                        selectinload(VpnKey.server),
                        selectinload(VpnKey.subscription).selectinload(Subscription.user),
                    )
                    .where(VpnKey.is_active.is_(True))
                )
            )
        for key in keys:
            if not key.server or not key.subscription or not key.subscription.user:
                continue
            email = self._subscription_email(key.subscription.user, key.subscription)
            try:
                used = await XUIClient(key.server).get_client_traffic(email)
                await self.store.update_key_usage(key.id, used)
            except Exception as exc:
                logger.warning('Unable to refresh key %s usage: %s', key.id, _describe_exception(exc))

    def get_provisioning_alert_snapshot(self, *, window_minutes: int = 10, total_threshold: int = 5, per_server_threshold: int = 3) -> dict[str, object]:
        self._trim_failure_events(keep_minutes=max(window_minutes * 6, 60))
        cutoff = datetime.utcnow() - timedelta(minutes=window_minutes)
        recent_events = [event for event in self._failure_events if isinstance(event.get('ts'), datetime) and event['ts'] >= cutoff]
        if not recent_events:
            return {
                'has_issue': False,
                'state_key': 'ok',
                'window_minutes': window_minutes,
                'total_failures': 0,
                'server_breakdown': [],
                'triggered_servers': [],
                'stage_breakdown': [],
                'top_errors': [],
                'recent_events': [],
            }

        stage_counter = Counter(str(event.get('stage') or 'unknown') for event in recent_events)
        error_counter = Counter(str(event.get('error') or 'Неизвестная ошибка') for event in recent_events)
        server_stats: dict[tuple[int | None, str], int] = {}
        for event in recent_events:
            server_id = event.get('server_id') if isinstance(event.get('server_id'), int) else None
            server_name = str(event.get('server_name') or f'Сервер #{server_id}' if server_id else 'Неизвестный сервер')
            key = (server_id, server_name)
            server_stats[key] = server_stats.get(key, 0) + 1

        server_breakdown = [
            {
                'server_id': server_id,
                'server_name': server_name,
                'count': count,
            }
            for (server_id, server_name), count in sorted(server_stats.items(), key=lambda item: (-item[1], item[0][1]))
        ]
        triggered_servers = [item for item in server_breakdown if item['count'] >= per_server_threshold]
        total_failures = len(recent_events)
        has_issue = total_failures >= total_threshold or bool(triggered_servers)
        if triggered_servers:
            state_key = 'servers:' + ','.join(str(item['server_id'] or item['server_name']) for item in triggered_servers)
        elif has_issue:
            state_key = 'global'
        else:
            state_key = 'ok'

        return {
            'has_issue': has_issue,
            'state_key': state_key,
            'window_minutes': window_minutes,
            'total_failures': total_failures,
            'server_breakdown': server_breakdown,
            'triggered_servers': triggered_servers,
            'stage_breakdown': [
                {'stage': stage, 'count': count}
                for stage, count in stage_counter.most_common()
            ],
            'top_errors': [
                {'error': error, 'count': count}
                for error, count in error_counter.most_common(3)
            ],
            'recent_events': sorted(recent_events, key=lambda item: item['ts'], reverse=True)[:5],
        }

    def get_server_failure_stats(self, *, window_minutes: int = 180) -> dict[int, dict[str, object]]:
        self._trim_failure_events(keep_minutes=max(window_minutes * 2, 180))
        cutoff = datetime.utcnow() - timedelta(minutes=window_minutes)
        stats: dict[int, dict[str, object]] = {}
        for event in self._failure_events:
            event_ts = event.get('ts')
            server_id = event.get('server_id')
            if not isinstance(event_ts, datetime) or event_ts < cutoff or not isinstance(server_id, int):
                continue
            bucket = stats.setdefault(server_id, {
                'count': 0,
                'last_ts': None,
                'last_error': '',
                'last_stage': '',
            })
            bucket['count'] = int(bucket['count']) + 1
            if bucket['last_ts'] is None or event_ts > bucket['last_ts']:
                bucket['last_ts'] = event_ts
                bucket['last_error'] = str(event.get('error') or 'Неизвестная ошибка')
                bucket['last_stage'] = str(event.get('stage') or 'unknown')
        return stats

    async def _load_subscription(self, subscription_id: int) -> Subscription | None:
        async with SessionLocal() as session:
            return await session.get(
                Subscription,
                subscription_id,
                options=[
                    selectinload(Subscription.keys).selectinload(VpnKey.server),
                    selectinload(Subscription.tariff),
                    selectinload(Subscription.server),
                    selectinload(Subscription.user),
                ],
            )

    async def _sync_primary_server(self, subscription_id: int, keys: list[VpnKey]) -> None:
        if len(keys) == 1:
            await self.store.set_subscription_server(subscription_id, keys[0].server_id)

    async def _sync_subscription_keys(self, user: User, subscription: Subscription, title: str, trial_only: bool) -> list[VpnKey]:
        current_subscription = await self._load_subscription(subscription.id) or subscription
        active_keys = {key.server_id: key for key in getattr(current_subscription, 'keys', []) or [] if key.is_active and key.server_id}

        for key in list(active_keys.values()):
            if not key.server:
                continue
            try:
                result = await XUIClient(key.server).update_key(key, current_subscription, user)
                await self.store.update_key_credentials(key.id, result.access_url, result.external_id, is_active=True)
            except Exception as exc:
                logger.warning(
                    'Unable to update existing key %s for subscription %s: %s',
                    key.id,
                    current_subscription.id,
                    _describe_exception(exc),
                )
                try:
                    replacement = await XUIClient(key.server).replace_key(key, current_subscription, user)
                    await self.store.update_key_credentials(key.id, replacement.access_url, replacement.external_id, used_bytes=0, is_active=True)
                    await self.store.increment_server_reissue_count(key.server.id)
                except Exception as replacement_exc:
                    replacement_error = _describe_exception(replacement_exc)
                    logger.warning(
                        'Unable to rotate existing key %s for subscription %s: %s',
                        key.id,
                        current_subscription.id,
                        replacement_error,
                    )
                    self._record_failure_event(
                        stage='rotate',
                        error=replacement_error,
                        server_id=getattr(key.server, 'id', None),
                        server_name=getattr(key.server, 'name', None),
                        subscription_id=getattr(current_subscription, 'id', None),
                        user_id=getattr(user, 'telegram_id', None),
                    )
                    await self.store.record_failed_server_check(key.server.id, replacement_error)

        provision_servers = await self.store.list_balanced_servers(trial_only=trial_only)
        for server in provision_servers:
            if server.id in active_keys:
                continue
            try:
                result = await XUIClient(server).provision_key(user, current_subscription)
                await self.store.create_vpn_key(
                    subscription_id=current_subscription.id,
                    server_id=server.id,
                    label=f'{title} / {server.name}',
                    access_url=result.access_url,
                    external_id=result.external_id,
                )
            except Exception as exc:
                error_text = _describe_exception(exc)
                logger.warning(
                    'Unable to provision key for subscription %s on server %s: %s',
                    current_subscription.id,
                    server.id,
                    error_text,
                )
                self._record_failure_event(
                    stage='provision',
                    error=error_text,
                    server_id=getattr(server, 'id', None),
                    server_name=getattr(server, 'name', None),
                    subscription_id=getattr(current_subscription, 'id', None),
                    user_id=getattr(user, 'telegram_id', None),
                )
                await self.store.record_failed_server_check(server.id, error_text)

        refreshed = await self._load_subscription(current_subscription.id)
        return self._active_keys(refreshed)

    def _active_keys(self, subscription: Subscription | None) -> list[VpnKey]:
        if not subscription:
            return []
        return [key for key in getattr(subscription, 'keys', []) or [] if key.is_active]

    def _is_subscription_active(self, subscription: Subscription) -> bool:
        return subscription.status == 'active' and is_future_datetime(getattr(subscription, 'ends_at', None))

    def _subscription_email(self, user: User, subscription: Subscription) -> str:
        return f'tg{user.telegram_id}-sub{subscription.id}'

    def _extract_extension_target(self, payload: str) -> int | None:
        if not payload:
            return None
        match = _EXTENSION_RE.search(payload)
        if not match:
            return None
        try:
            return int(match.group('subscription_id'))
        except (TypeError, ValueError):
            return None

    def _record_failure_event(
        self,
        *,
        stage: str,
        error: str,
        server_id: int | None,
        server_name: str | None,
        subscription_id: int | None,
        user_id: int | None,
    ) -> None:
        self._trim_failure_events()
        normalized_error = (error or 'Неизвестная ошибка').strip() or 'Неизвестная ошибка'
        self._failure_events.append(
            {
                'ts': datetime.utcnow(),
                'stage': stage,
                'error': normalized_error,
                'server_id': server_id,
                'server_name': server_name or (f'Сервер #{server_id}' if server_id else 'Неизвестный сервер'),
                'subscription_id': subscription_id,
                'user_id': user_id,
            }
        )

    def _trim_failure_events(self, keep_minutes: int = 120) -> None:
        cutoff = datetime.utcnow() - timedelta(minutes=keep_minutes)
        while self._failure_events and isinstance(self._failure_events[0].get('ts'), datetime) and self._failure_events[0]['ts'] < cutoff:
            self._failure_events.popleft()

