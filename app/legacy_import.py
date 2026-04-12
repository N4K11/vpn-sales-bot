from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import (
    AppSetting,
    BalanceOperation,
    ContentPage,
    FeatureToggle,
    Payment,
    Server,
    Subscription,
    Tariff,
    User,
    VpnKey,
)
from app.db.session import SessionLocal, init_db
from app.services.store import DEFAULT_CONTENT, DEFAULT_SETTINGS, DEFAULT_TOGGLES
from app.services.xui import XUIClient

logger = logging.getLogger(__name__)

def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(slots=True)
class ImportSummary:
    users: int = 0
    tariffs: int = 0
    servers: int = 0
    subscriptions: int = 0
    keys: int = 0
    payments: int = 0
    balance_operations: int = 0
    skipped_keys: int = 0
    skipped_payments: int = 0
    warnings: list[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        logger.warning(message)

    def render(self) -> str:
        lines = [
            'Импорт завершён.',
            '',
            f'Пользователи: {self.users}',
            f'Тарифы: {self.tariffs}',
            f'Серверы: {self.servers}',
            f'Подписки: {self.subscriptions}',
            f'Ключи: {self.keys}',
            f'Платежи: {self.payments}',
            f'Операции баланса: {self.balance_operations}',
            f'Пропущенные ключи: {self.skipped_keys}',
            f'Пропущенные платежи: {self.skipped_payments}',
        ]
        if self.warnings:
            lines.extend(['', 'Предупреждения:'])
            for item in self.warnings:
                lines.append(f'- {item}')
        return '\n'.join(lines)


@dataclass(slots=True)
class LegacyXUISnapshot:
    file_name: str
    web_port: int
    web_base_path: str
    inbounds: list[dict[str, Any]]


class LegacyImporter:
    def __init__(self, legacy_bot_db: Path, xui_dir: Path | None, *, wipe_current: bool, skip_payments: bool) -> None:
        self.legacy_bot_db = legacy_bot_db
        self.xui_dir = xui_dir or legacy_bot_db.parent
        self.wipe_current = wipe_current
        self.skip_payments = skip_payments
        self.summary = ImportSummary()

        self.legacy_users: list[sqlite3.Row] = []
        self.legacy_tariffs: list[sqlite3.Row] = []
        self.legacy_servers: list[sqlite3.Row] = []
        self.legacy_keys: list[sqlite3.Row] = []
        self.legacy_payments: list[sqlite3.Row] = []
        self.legacy_settings: dict[str, str] = {}
        self.legacy_referral_levels: list[sqlite3.Row] = []
        self.notification_dates: dict[int, datetime] = {}
        self.xui_snapshots: dict[tuple[int, str], LegacyXUISnapshot] = {}

        self.users_by_old_id: dict[int, User] = {}
        self.tariffs_by_old_id: dict[int, Tariff] = {}
        self.servers_by_old_id: dict[int, Server] = {}
        self.payments_by_old_id: dict[int, Payment] = {}
        self.payment_by_legacy_key_id: dict[int, Payment] = {}
        self.trial_tariff_ids: set[int] = set()
        self.used_invite_codes: set[str] = set()
        self.legacy_key_counts_by_server: dict[int, Counter[int]] = defaultdict(Counter)

    async def run(self) -> ImportSummary:
        self._load_legacy_bot_db()
        self._load_xui_snapshots()

        await init_db()
        async with SessionLocal() as session:
            await self._prepare_target(session)
            await self._seed_support_tables(session)
            await self._import_users(session)
            await self._import_referrals(session)
            await self._import_tariffs(session)
            await self._import_servers(session)
            if not self.skip_payments:
                await self._import_payments(session)
            await self._import_subscriptions_and_keys(session)
            await self._apply_legacy_settings(session)
            await session.commit()

        return self.summary

    def _load_legacy_bot_db(self) -> None:
        if not self.legacy_bot_db.exists():
            raise FileNotFoundError(f'Не найден legacy bot DB: {self.legacy_bot_db}')

        conn = sqlite3.connect(str(self.legacy_bot_db))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        self.legacy_users = cur.execute('SELECT * FROM users ORDER BY id').fetchall()
        self.legacy_tariffs = cur.execute('SELECT * FROM tariffs ORDER BY id').fetchall()
        self.legacy_servers = cur.execute('SELECT * FROM servers ORDER BY id').fetchall()
        self.legacy_keys = cur.execute('SELECT * FROM vpn_keys ORDER BY id').fetchall()
        self.legacy_payments = cur.execute('SELECT * FROM payments ORDER BY id').fetchall()
        self.legacy_referral_levels = cur.execute('SELECT * FROM referral_levels ORDER BY level_number').fetchall()
        self.legacy_settings = {row['key']: row['value'] for row in cur.execute('SELECT key, value FROM settings').fetchall()}

        for row in self.legacy_keys:
            server_id = row['server_id']
            inbound_id = row['panel_inbound_id']
            if server_id is not None and inbound_id is not None:
                self.legacy_key_counts_by_server[int(server_id)][int(inbound_id)] += 1

        for row in cur.execute('SELECT vpn_key_id, MAX(sent_at) AS sent_at FROM notification_log GROUP BY vpn_key_id').fetchall():
            sent_at = self._parse_datetime(row['sent_at'])
            if sent_at:
                self.notification_dates[int(row['vpn_key_id'])] = sent_at

        self.trial_tariff_ids = {int(row['id']) for row in self.legacy_tariffs if self._is_trial_tariff_name(str(row['name'] or ''))}
        conn.close()

    def _load_xui_snapshots(self) -> None:
        if not self.xui_dir.exists():
            self.summary.warn(f'Папка со snapshot x-ui не найдена: {self.xui_dir}')
            return

        for db_path in sorted(self.xui_dir.glob('server_*_x-ui.db')):
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            settings_map = {row['key']: row['value'] for row in cur.execute('SELECT key, value FROM settings').fetchall()}
            try:
                web_port = int(settings_map.get('webPort') or 0)
            except ValueError:
                web_port = 0
            web_base_path = self._normalize_web_base_path(settings_map.get('webBasePath'))
            inbounds: list[dict[str, Any]] = []
            for row in cur.execute('SELECT id, port, protocol, remark, enable, settings, stream_settings FROM inbounds ORDER BY id').fetchall():
                inbounds.append(
                    {
                        'id': row['id'],
                        'port': row['port'],
                        'protocol': row['protocol'],
                        'remark': row['remark'],
                        'enable': row['enable'],
                        'settings': row['settings'],
                        'streamSettings': row['stream_settings'],
                    }
                )
            conn.close()

            key = (web_port, web_base_path)
            self.xui_snapshots[key] = LegacyXUISnapshot(
                file_name=db_path.name,
                web_port=web_port,
                web_base_path=web_base_path,
                inbounds=inbounds,
            )

    async def _prepare_target(self, session: AsyncSession) -> None:
        if self.wipe_current:
            for model in (BalanceOperation, Payment, VpnKey, Subscription, Server, Tariff, User, FeatureToggle, AppSetting, ContentPage):
                await session.execute(delete(model))
            await session.commit()
            return

        for model in (User, Tariff, Server, Subscription, VpnKey, Payment):
            count = await session.scalar(select(func.count()).select_from(model)) or 0
            if count:
                raise RuntimeError('Текущая база уже содержит данные. Запустите импорт с флагом --wipe-current.')

    async def _seed_support_tables(self, session: AsyncSession) -> None:
        for key, enabled in DEFAULT_TOGGLES.items():
            if not await session.get(FeatureToggle, key):
                session.add(FeatureToggle(key=key, is_enabled=enabled))

        for key, value in DEFAULT_SETTINGS.items():
            if not await session.get(AppSetting, key):
                session.add(AppSetting(key=key, value=value))

        for key, payload in DEFAULT_CONTENT.items():
            if not await session.get(ContentPage, key):
                title, body = payload
                session.add(ContentPage(key=key, title=title, body=body))

        await session.flush()

    async def _import_users(self, session: AsyncSession) -> None:
        for row in self.legacy_users:
            telegram_id = int(row['telegram_id'])
            user = User(
                telegram_id=telegram_id,
                username=row['username'],
                full_name=row['username'] or '',
                is_admin=telegram_id in settings.admin_ids,
                is_blocked=self._as_bool(row['is_banned']),
                invite_code=self._make_invite_code(str(row['referral_code'] or ''), telegram_id),
                balance=self._legacy_balance_to_decimal(row['personal_balance']),
                trial_claimed=self._as_bool(row['used_trial']),
            )
            created_at = self._parse_datetime(row['created_at']) or _utc_now_naive()
            user.created_at = created_at
            user.updated_at = created_at
            session.add(user)
            await session.flush()
            self.users_by_old_id[int(row['id'])] = user
            self.summary.users += 1

            if user.balance > Decimal('0'):
                session.add(
                    BalanceOperation(
                        user_id=user.id,
                        kind='legacy_import_balance',
                        amount=user.balance,
                        description='Импортирован баланс из legacy-базы',
                        balance_after=user.balance,
                    )
                )
                self.summary.balance_operations += 1

        await session.flush()

    async def _import_referrals(self, session: AsyncSession) -> None:
        for row in self.legacy_users:
            referred_by = row['referred_by']
            if referred_by is None:
                continue
            user = self.users_by_old_id.get(int(row['id']))
            referrer = self.users_by_old_id.get(int(referred_by))
            if user and referrer and user.id != referrer.id:
                user.referrer_id = referrer.id
        await session.flush()

    async def _import_tariffs(self, session: AsyncSession) -> None:
        for row in self.legacy_tariffs:
            tariff = Tariff(
                name=str(row['name'] or f'Legacy tariff #{row["id"]}').strip(),
                days=int(row['duration_days'] or 0),
                price_rub=self._legacy_tariff_rub_price(row),
                price_stars=int(row['price_stars'] or 0),
                description=self._legacy_tariff_description(row),
                is_active=self._as_bool(row['is_active']),
            )
            session.add(tariff)
            await session.flush()
            self.tariffs_by_old_id[int(row['id'])] = tariff
            self.summary.tariffs += 1
        await session.flush()

    async def _import_servers(self, session: AsyncSession) -> None:
        trial_server_ids = {
            int(row['server_id'])
            for row in self.legacy_keys
            if row['server_id'] is not None and int(row['tariff_id'] or 0) in self.trial_tariff_ids
        }

        for row in self.legacy_servers:
            old_server_id = int(row['id'])
            snapshot = self._match_xui_snapshot(row)
            inbound_id, inbound = self._resolve_legacy_inbound(row, snapshot)
            if inbound_id is None:
                self.summary.warn(f'Для сервера {row["name"]} не удалось определить inbound_id. Будет использован 0.')
                inbound_id = 0

            server = Server(
                name=str(row['name'] or f'Legacy server #{old_server_id}').strip(),
                base_url=self._build_panel_base_url(row),
                username=str(row['login'] or '').strip(),
                password=str(row['password'] or '').strip(),
                inbound_id=inbound_id,
                is_enabled=self._as_bool(row['is_active']),
                is_trial_available=old_server_id in trial_server_ids,
                health_status='unknown',
                cpu_percent=0,
                ram_percent=0,
                last_error='',
                last_checked_at=None,
            )
            session.add(server)
            await session.flush()
            self.servers_by_old_id[old_server_id] = server
            setattr(server, '_legacy_inbound', inbound)
            setattr(server, '_legacy_snapshot_name', snapshot.file_name if snapshot else '')
            self.summary.servers += 1
        await session.flush()

    async def _import_payments(self, session: AsyncSession) -> None:
        for row in self.legacy_payments:
            user = self.users_by_old_id.get(int(row['user_id'])) if row['user_id'] is not None else None
            tariff = self.tariffs_by_old_id.get(int(row['tariff_id'])) if row['tariff_id'] is not None else None
            if not user or not tariff:
                self.summary.skipped_payments += 1
                continue

            method = self._map_payment_method(str(row['payment_type'] or 'legacy'))
            if method == 'stars':
                amount = Decimal(int(row['amount_stars'] or 0))
                currency = 'XTR'
            else:
                amount = self._cents_to_decimal(row['amount_cents'])
                currency = 'RUB'

            payment = Payment(
                user_id=user.id,
                tariff_id=tariff.id,
                method=method,
                amount=amount,
                currency=currency,
                status=str(row['status'] or 'paid'),
                provider_payment_id=str(row['yookassa_payment_id'] or row['order_id'] or ''),
                provider_url='',
                payload=f'legacy-{row["id"]}-{row["order_id"]}',
                paid_at=self._parse_datetime(row['paid_at']),
            )
            created_at = payment.paid_at or _utc_now_naive()
            payment.created_at = created_at
            payment.updated_at = created_at
            session.add(payment)
            await session.flush()
            self.payments_by_old_id[int(row['id'])] = payment
            if row['vpn_key_id'] is not None and int(row['vpn_key_id']) not in self.payment_by_legacy_key_id:
                self.payment_by_legacy_key_id[int(row['vpn_key_id'])] = payment
            self.summary.payments += 1
        await session.flush()

    async def _import_subscriptions_and_keys(self, session: AsyncSession) -> None:
        now = _utc_now_naive()
        for row in self.legacy_keys:
            legacy_key_id = int(row['id'])
            old_user_id = row['user_id']
            old_server_id = row['server_id']
            old_tariff_id = row['tariff_id']

            user = self.users_by_old_id.get(int(old_user_id)) if old_user_id is not None else None
            server = self.servers_by_old_id.get(int(old_server_id)) if old_server_id is not None else None
            tariff = self.tariffs_by_old_id.get(int(old_tariff_id)) if old_tariff_id is not None else None
            if not user or not server or not tariff:
                self.summary.skipped_keys += 1
                self.summary.warn(f'Ключ #{legacy_key_id} пропущен: не удалось сопоставить пользователя, сервер или тариф.')
                continue

            starts_at = self._parse_datetime(row['created_at']) or now
            ends_at = self._parse_datetime(row['expires_at']) or starts_at
            is_trial = int(old_tariff_id or 0) in self.trial_tariff_ids
            status = 'active' if ends_at > now else 'expired'

            subscription = Subscription(
                user_id=user.id,
                tariff_id=tariff.id,
                server_id=server.id,
                status=status,
                starts_at=starts_at,
                ends_at=ends_at,
                is_trial=is_trial,
                source_payment_id=(self.payment_by_legacy_key_id.get(legacy_key_id).id if legacy_key_id in self.payment_by_legacy_key_id else None),
                expiry_notice_sent_at=self.notification_dates.get(legacy_key_id),
            )
            subscription.created_at = starts_at
            subscription.updated_at = max(starts_at, self.notification_dates.get(legacy_key_id) or starts_at)
            session.add(subscription)
            await session.flush()
            self.summary.subscriptions += 1

            access_url, external_id, requires_reissue = self._build_legacy_key_material(server, row)
            key_is_active = bool(ends_at > now and not requires_reissue)
            label = str(row['custom_name'] or '').strip() or f'{tariff.name} / {server.name}'
            if requires_reissue:
                label = f'{label} / нужен перевыпуск'
                if ends_at > now:
                    self.summary.warn(
                        f'Для ключа #{legacy_key_id} не удалось восстановить рабочую ссылку автоматически. '
                        f'Ключ импортирован как требующий перевыпуска.'
                    )

            vpn_key = VpnKey(
                subscription_id=subscription.id,
                server_id=server.id,
                label=label,
                access_url=access_url,
                external_id=external_id,
                used_bytes=int(row['traffic_used'] or 0),
                is_active=key_is_active,
            )
            vpn_key.created_at = starts_at
            vpn_key.updated_at = self._parse_datetime(row['traffic_updated_at']) or starts_at
            session.add(vpn_key)
            self.summary.keys += 1

        await session.flush()

    async def _apply_legacy_settings(self, session: AsyncSession) -> None:
        referral_level_1 = next((row for row in self.legacy_referral_levels if int(row['level_number'] or 0) == 1), None)
        referral_visible = bool(referral_level_1 and self._as_bool(referral_level_1['enabled']))
        referral_percent = int(referral_level_1['percent']) if referral_level_1 else settings.referral_default_percent
        trial_tariff = next((row for row in self.legacy_tariffs if int(row['id']) in self.trial_tariff_ids), None)
        trial_visible = bool(trial_tariff and self._as_bool(trial_tariff['is_active']))
        trial_days = int(trial_tariff['duration_days']) if trial_tariff else settings.trial_default_days

        payment_types = {self._map_payment_method(str(row['payment_type'] or '')) for row in self.legacy_payments}
        await self._set_toggle(session, 'section_referral', referral_visible)
        await self._set_toggle(session, 'section_trial', trial_visible)
        await self._set_toggle(session, 'payment_stars', 'stars' in payment_types)
        await self._set_toggle(session, 'payment_yookassa', 'yookassa' in payment_types)
        await self._set_toggle(session, 'payment_crypto', 'crypto' in payment_types)
        await self._set_toggle(session, 'payment_balance', True)

        await self._set_setting(session, 'referral_percent', str(referral_percent))
        await self._set_setting(session, 'trial_days', str(trial_days))
        await session.flush()

    async def _set_toggle(self, session: AsyncSession, key: str, value: bool) -> None:
        toggle = await session.get(FeatureToggle, key)
        if not toggle:
            toggle = FeatureToggle(key=key, is_enabled=value)
            session.add(toggle)
        else:
            toggle.is_enabled = value

    async def _set_setting(self, session: AsyncSession, key: str, value: str) -> None:
        setting = await session.get(AppSetting, key)
        if not setting:
            setting = AppSetting(key=key, value=value)
            session.add(setting)
        else:
            setting.value = value

    def _match_xui_snapshot(self, server_row: sqlite3.Row) -> LegacyXUISnapshot | None:
        key = (int(server_row['port'] or 0), self._normalize_web_base_path(server_row['web_base_path']))
        return self.xui_snapshots.get(key)

    def _resolve_legacy_inbound(self, server_row: sqlite3.Row, snapshot: LegacyXUISnapshot | None) -> tuple[int | None, dict[str, Any] | None]:
        inbound_counter = self.legacy_key_counts_by_server.get(int(server_row['id']), Counter())
        preferred_inbound_id = inbound_counter.most_common(1)[0][0] if inbound_counter else None

        if snapshot and snapshot.inbounds:
            if preferred_inbound_id is not None:
                for inbound in snapshot.inbounds:
                    if int(inbound.get('id') or 0) == int(preferred_inbound_id):
                        return int(preferred_inbound_id), inbound
            enabled = [inbound for inbound in snapshot.inbounds if self._as_bool(inbound.get('enable', 1))]
            pool = enabled or snapshot.inbounds
            if len(pool) == 1:
                inbound = pool[0]
                return int(inbound.get('id') or 0), inbound
            if pool:
                inbound = pool[0]
                self.summary.warn(
                    f'Для сервера {server_row["name"]} найдено несколько inbound в snapshot {snapshot.file_name}. '
                    f'Будет использован первый inbound #{inbound.get("id")}.'
                )
                return int(inbound.get('id') or 0), inbound

        if preferred_inbound_id is not None:
            return int(preferred_inbound_id), None
        return None, None

    def _build_legacy_key_material(self, server: Server, legacy_key: sqlite3.Row) -> tuple[str, str, bool]:
        inbound = getattr(server, '_legacy_inbound', None)
        if not inbound:
            fallback_external_id = str(legacy_key['client_uuid'] or legacy_key['panel_email'] or f'legacy-key-{legacy_key["id"]}')
            return f'legacy-import://missing-snapshot/{legacy_key["id"]}', fallback_external_id, True

        protocol = str(inbound.get('protocol') or 'vless').lower()
        inbound_settings = self._json_dict(inbound.get('settings'))
        template_client = ((inbound_settings.get('clients') or [])[:1] or [{}])[0]
        client = self._find_legacy_client(inbound_settings, legacy_key)
        email = str(legacy_key['panel_email'] or '').strip() or str((client or {}).get('email') or '') or f'legacy-key-{legacy_key["id"]}'

        resolved_client = dict(template_client)
        if client:
            resolved_client.update(client)
        resolved_client['email'] = email
        secret = str((client or {}).get('id') or (client or {}).get('password') or legacy_key['client_uuid'] or legacy_key['panel_email'] or f'legacy-{legacy_key["id"]}').strip()
        if protocol in {'trojan', 'shadowsocks', 'http', 'socks'}:
            resolved_client['password'] = secret
        else:
            resolved_client['id'] = secret

        proxy_server = SimpleNamespace(
            base_url=server.base_url,
            name=server.name,
            inbound_id=server.inbound_id,
            username=server.username,
            password=server.password,
        )
        client_builder = XUIClient(proxy_server)
        try:
            access_url = client_builder._build_connection_string(inbound, resolved_client, email)
            external_id = client_builder._client_identifier(protocol, resolved_client, email, fallback=secret)
            return access_url, external_id, client is None
        except Exception:
            fallback_external_id = secret
            return f'legacy-import://reissue-required/{legacy_key["id"]}', fallback_external_id, True

    def _find_legacy_client(self, inbound_settings: dict[str, Any], legacy_key: sqlite3.Row) -> dict[str, Any] | None:
        clients = inbound_settings.get('clients') or []
        client_uuid = str(legacy_key['client_uuid'] or '').strip()
        panel_email = str(legacy_key['panel_email'] or '').strip()
        for client in clients:
            if client_uuid and str(client.get('id') or client.get('password') or '').strip() == client_uuid:
                return dict(client)
            if panel_email and str(client.get('email') or '').strip() == panel_email:
                return dict(client)
        return None

    def _build_panel_base_url(self, server_row: sqlite3.Row) -> str:
        scheme = str(server_row['protocol'] or 'https').strip() or 'https'
        host = str(server_row['host'] or '').strip()
        port = int(server_row['port'] or 0)
        path = self._normalize_web_base_path(server_row['web_base_path']).rstrip('/')
        return f'{scheme}://{host}:{port}{path}'

    def _legacy_tariff_rub_price(self, row: sqlite3.Row) -> Decimal:
        price_rub = row['price_rub']
        if price_rub not in (None, ''):
            return Decimal(str(int(price_rub))).quantize(Decimal('0.01'))
        return self._cents_to_decimal(row['price_cents'])

    def _legacy_tariff_description(self, row: sqlite3.Row) -> str:
        traffic_limit = int(row['traffic_limit_gb'] or 0)
        parts = ['Импортировано из legacy-базы.']
        if traffic_limit > 0:
            parts.append(f'Лимит трафика: {traffic_limit} ГБ.')
        if row['external_id'] not in (None, ''):
            parts.append(f'Legacy external_id: {row["external_id"]}.')
        return ' '.join(parts)

    def _legacy_balance_to_decimal(self, value: Any) -> Decimal:
        if value in (None, ''):
            return Decimal('0.00')
        return (Decimal(int(value)) / Decimal('100')).quantize(Decimal('0.01'))

    def _cents_to_decimal(self, value: Any) -> Decimal:
        if value in (None, ''):
            return Decimal('0.00')
        return (Decimal(int(value)) / Decimal('100')).quantize(Decimal('0.01'))

    def _map_payment_method(self, raw: str) -> str:
        value = raw.strip().lower()
        if value in {'stars', 'telegram_stars', 'xtr'}:
            return 'stars'
        if value in {'yookassa', 'yoo', 'bank', 'card'}:
            return 'yookassa'
        if value in {'crypto', 'cryptobot', 'usdt', 'ton'}:
            return 'crypto'
        if value in {'balance', 'wallet'}:
            return 'balance'
        return value or 'legacy'

    def _make_invite_code(self, raw_code: str, telegram_id: int) -> str:
        base = ''.join(ch for ch in (raw_code or '').strip() if ch.isalnum())[:32]
        if not base:
            base = f'ref{telegram_id}'
        candidate = base
        suffix = 1
        while candidate in self.used_invite_codes:
            suffix += 1
            candidate = f'{base[:28]}{suffix}'
        self.used_invite_codes.add(candidate)
        return candidate

    def _parse_datetime(self, value: Any) -> datetime | None:
        if value in (None, ''):
            return None
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, (int, float)):
            # legacy x-ui timestamps are usually in milliseconds
            if value > 10_000_000_000:
                dt = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
            else:
                dt = datetime.fromtimestamp(value, tz=timezone.utc)
        else:
            text = str(value).strip()
            try:
                dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
            except ValueError:
                for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
                    try:
                        dt = datetime.strptime(text, fmt)
                        break
                    except ValueError:
                        continue
                else:
                    return None
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    def _normalize_web_base_path(self, value: Any) -> str:
        text = str(value or '/').strip() or '/'
        if not text.startswith('/'):
            text = '/' + text
        if text != '/' and not text.endswith('/'):
            text += '/'
        return text

    def _json_dict(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                return {}
        return {}

    def _as_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value in (None, '', 0, '0', 'false', 'False', 'FALSE'):
            return False
        return True

    def _is_trial_tariff_name(self, name: str) -> bool:
        lowered = name.lower()
        return 'проб' in lowered or 'trial' in lowered


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Импорт legacy SQLite-бэкапа в текущую схему VPN-бота.')
    parser.add_argument('--legacy-bot-db', required=True, help='Путь к старой vpn_bot.db')
    parser.add_argument('--xui-dir', help='Папка с backup .db от x-ui. По умолчанию берётся папка legacy-bot-db.')
    parser.add_argument('--wipe-current', action='store_true', help='Очистить текущую БД перед импортом.')
    parser.add_argument('--skip-payments', action='store_true', help='Не импортировать legacy-платежи.')
    return parser


async def async_main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
    importer = LegacyImporter(
        legacy_bot_db=Path(args.legacy_bot_db),
        xui_dir=Path(args.xui_dir) if args.xui_dir else None,
        wipe_current=args.wipe_current,
        skip_payments=args.skip_payments,
    )
    summary = await importer.run()
    print(summary.render())
    return 0


def main() -> None:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(asyncio.run(async_main()))


if __name__ == '__main__':
    main()

