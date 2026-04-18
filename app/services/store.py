from __future__ import annotations

import json
import logging
import random
from math import ceil
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from aiogram.types import User as TelegramUser
from sqlalchemy import Select, desc, func, select, update
from sqlalchemy.orm import selectinload

from app.config import settings
from app.utils import is_future_datetime
from app.db.models import (
    AdminActionLog,
    AppSetting,
    BalanceOperation,
    ContentPage,
    FeatureToggle,
    Payment,
    ProvisioningFailureLog,
    Server,
    Subscription,
    Tariff,
    User,
    VpnKey,
)
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

DEFAULT_TOGGLES: dict[str, bool] = {
    "section_referral": True,
    "section_trial": True,
    "section_reserve_access": True,
    "payment_stars": True,
    "payment_yookassa": False,
    "payment_crypto": False,
    "payment_balance": True,
}

DEFAULT_SETTINGS: dict[str, str] = {
    "referral_percent": str(settings.referral_default_percent),
    "trial_days": str(settings.trial_default_days),
    "support_chat_url": settings.support_chat_url,
    "channel_url": settings.channel_url,
    "terms_url": settings.terms_url,
    "yookassa_shop_id": settings.yookassa_shop_id,
    "yookassa_secret_key": settings.yookassa_secret_key,
    "yookassa_return_url": settings.yookassa_return_url,
    "crypto_pay_token": settings.crypto_pay_token,
    "crypto_pay_use_testnet": "true" if settings.crypto_pay_use_testnet else "false",
    "crypto_pay_assets": settings.crypto_pay_assets_raw,
}

DEFAULT_CONTENT: dict[str, tuple[str, str]] = {
    'main': ('Экран «Главное меню»', 'Добро пожаловать в MyAir.\n\nВыберите нужный раздел ниже.'),
    'profile': ('Экран «Мой профиль»', 'Здесь собраны ваши активные подписки, ключи доступа, общая ссылка и баланс.'),
    'buy': ('Экран «Подключить Air»', 'Выберите формат доступа, а затем удобный способ оплаты.'),
    'help': ('Экран «Справка»', 'Здесь можно узнать, как работает бот, и перейти в канал или поддержку.'),
    'referral': ('Экран «Рефералы»', 'Приглашайте друзей и получайте процент с каждой их покупки на внутренний баланс.'),
    'trial': ('Экран «Пробный доступ»', 'Здесь можно активировать тестовый доступ, если он открыт администратором.'),
    'devices_menu': ('Экран «Как подключить»', 'Подключение по устройствам\n\nВыберите своё устройство ниже. Внутри будет короткая пошаговая инструкция, как вставить общую ссылку доступа в приложение.\n\nОбщий принцип везде один:\n• откройте доступ в профиле;\n• скопируйте общую ссылку;\n• в приложении найдите Import / Subscription / URL;\n• вставьте ссылку и обновите конфигурацию.\n\nЕсли приложение не принимает общую ссылку, откройте внутри доступа конкретный серверный ключ и импортируйте его отдельно.'),
    'guide_ios': ('Инструкция: iPhone / iPad', 'iPhone / iPad\n\n1. Скопируйте общую ссылку из подписки в боте.\n2. Откройте приложение, которое умеет импорт по URL.\n3. Найдите пункт вроде Import, Subscription, Add from URL.\n4. Вставьте ссылку и подтвердите импорт.\n5. После добавления обновите подписку и подключайтесь к нужному серверу.\n\nЕсли клиент не принимает общую ссылку, откройте конкретный серверный ключ внутри подписки и импортируйте его отдельно.'),
    'guide_android': ('Инструкция: Android', 'Android\n\n1. Скопируйте общую ссылку из подписки в боте.\n2. Откройте приложение и выберите импорт из буфера, URL или subscription.\n3. Вставьте ссылку и сохраните конфигурацию.\n4. Обновите список серверов внутри приложения.\n5. Выберите нужный сервер и подключайтесь.\n\nЕсли приложение просит формат, обычно нужен URL / Subscription, а не текстовый файл.'),
    'guide_windows': ('Инструкция: Windows', 'Windows\n\n1. Скопируйте общую ссылку в боте.\n2. В приложении найдите Import profile, Add subscription или Import from URL.\n3. Вставьте ссылку и сохраните профиль.\n4. Запустите обновление подписки, если приложение это поддерживает.\n5. После импорта выберите сервер из списка и подключайтесь.\n\nЕсли подписка не импортируется, можно открыть отдельный серверный ключ и добавить его вручную.'),
    'guide_macos': ('Инструкция: macOS', '🍎 macOS\n\n1. Скопируйте общую ссылку из подписки.\n2. Откройте клиент и добавьте подписку через URL.\n3. Вставьте ссылку, сохраните профиль и дождитесь загрузки серверов.\n4. При необходимости обновите подписку вручную внутри клиента.\n5. Выберите удобный сервер и подключайтесь.\n\nЕсли клиент работает только с одиночными конфигами, откройте внутри подписки конкретный серверный ключ.'),
    'subscription_detail': ('Карточка подписки', 'Здесь видны срок действия, общая ссылка подписки, список серверов и все ключи внутри выбранного доступа.'),
    'key_detail': ('Карточка ключа', 'Здесь можно скопировать ключ, показать QR, заменить нерабочий ключ или удалить уже истёкший элемент.'),
    'activation_result': ('Экран после активации / оплаты', 'После оплаты, продления или пробного доступа бот показывает итоговую выдачу и быстрый переход к подписке.'),
    'button_nav_profile': ('Кнопка «Мой профиль» — главный экран', '👤 Мой профиль'),
    'button_nav_buy': ('Кнопка «Подключить Air» — главный экран и быстрые переходы', 'Подключить Air'),
    'button_nav_help': ('Кнопка «Справка» — главный экран', '❓ Справка'),
    'button_nav_referral': ('Кнопка «Рефералы» — главный экран', '🎁 Рефералы'),
    'button_nav_trial': ('Кнопка «Пробный доступ» — главный экран', '🧪 Пробный доступ'),
    'button_nav_home': ('Кнопка «Главное меню» — возврат в витрину', '🏠 Главное меню'),
    'button_nav_back': ('Кнопка «Назад» — возврат на шаг выше', '◀ Назад'),
    'button_help_channel': ('Кнопка «Канал» — раздел «Справка»', '📣 Канал'),
    'button_help_support': ('Кнопка «Поддержка» — раздел «Справка»', '🆘 Поддержка'),
    'button_referral_copy': ('Кнопка «Скопировать ссылку» — раздел «Рефералы»', '📋 Скопировать ссылку'),
    'button_trial_activate': ('Кнопка «Активировать пробный период» — раздел «Пробный доступ»', '🚀 Активировать пробный период'),
    'button_help_devices': ('Кнопка «Как подключить» — карточка подписки и результат выдачи', '📱 Как подключить'),
    'button_guide_ios': ('Кнопка «iPhone / iPad» — выбор устройства', '📱 iPhone / iPad'),
    'button_guide_android': ('Кнопка «Android» — выбор устройства', '🤖 Android'),
    'button_guide_windows': ('Кнопка «Windows» — выбор устройства', '🪟 Windows'),
    'button_guide_macos': ('Кнопка «macOS» — выбор устройства', '🍎 macOS'),
    'button_pay_stars': ('Кнопка «Telegram Stars» — выбор способа оплаты', '⭐ Telegram Stars'),
    'button_pay_yookassa': ('Кнопка «YooKassa» — выбор способа оплаты', '💳 YooKassa'),
    'button_pay_crypto': ('Кнопка «Crypto» — выбор способа оплаты', '🪙 Crypto'),
    'button_pay_balance': ('Кнопка «С баланса» — выбор способа оплаты', '💰 С баланса'),
    'button_pay_open_invoice': ('Кнопка «Перейти к оплате» — экран счёта', '💳 Перейти к оплате'),
    'button_subscription_qr': ('Кнопка «QR подписки» — карточка подписки', '📷 QR подписки'),
    'button_subscription_extend': ('Кнопка «Продлить подписку» — карточка подписки и ключа', '🕒 Продлить подписку'),
    'button_reserve_open': ('Кнопка «Резервный кабинет» — подписка и результат выдачи', '🌍 Резервный кабинет'),
    'button_reserve_qr': ('Кнопка «QR резерва» — карточка подписки', '📷 QR резерва'),
    'button_key_copy': ('Кнопка «Скопировать ключ» — карточка ключа', '📋 Скопировать ключ'),
    'button_key_qr': ('Кнопка «QR ключа» — карточка ключа', '📷 QR ключа'),
    'button_key_replace': ('Кнопка «Заменить ключ» — карточка ключа', '♻️ Заменить ключ'),
    'button_key_delete': ('Кнопка «Удалить ключ» — карточка ключа', '🗑️ Удалить ключ'),
}

LEGACY_BRANDING_REFRESH_KEYS: set[str] = {'main', 'devices_menu', 'guide_ios', 'guide_android', 'guide_windows', 'button_nav_buy'}
LEGACY_BRANDING_EXACT_BODIES: dict[str, str] = {
    'buy': 'Выберите тариф, а затем удобный способ оплаты.',
}

USER_TEXT_CONTENT_KEYS: list[str] = ['main', 'profile', 'buy', 'help', 'referral', 'trial', 'devices_menu', 'guide_ios', 'guide_android', 'guide_windows', 'guide_macos', 'subscription_detail', 'key_detail', 'activation_result']
BUTTON_LABEL_PAGE_KEYS: dict[str, str] = {'nav_profile': 'button_nav_profile', 'nav_buy': 'button_nav_buy', 'nav_help': 'button_nav_help', 'nav_referral': 'button_nav_referral', 'nav_trial': 'button_nav_trial', 'nav_home': 'button_nav_home', 'nav_back': 'button_nav_back', 'help_channel': 'button_help_channel', 'help_support': 'button_help_support', 'referral_copy': 'button_referral_copy', 'trial_activate': 'button_trial_activate', 'help_devices': 'button_help_devices', 'guide_ios': 'button_guide_ios', 'guide_android': 'button_guide_android', 'guide_windows': 'button_guide_windows', 'guide_macos': 'button_guide_macos', 'pay_stars': 'button_pay_stars', 'pay_yookassa': 'button_pay_yookassa', 'pay_crypto': 'button_pay_crypto', 'pay_balance': 'button_pay_balance', 'pay_open_invoice': 'button_pay_open_invoice', 'subscription_qr': 'button_subscription_qr', 'subscription_extend': 'button_subscription_extend', 'reserve_open': 'button_reserve_open', 'reserve_qr': 'button_reserve_qr', 'key_copy': 'button_key_copy', 'key_qr': 'button_key_qr', 'key_replace': 'button_key_replace', 'key_delete': 'button_key_delete'}
USER_BUTTON_CONTENT_KEYS: list[str] = ['button_nav_profile', 'button_nav_buy', 'button_nav_help', 'button_nav_referral', 'button_nav_trial', 'button_nav_home', 'button_nav_back', 'button_help_channel', 'button_help_support', 'button_referral_copy', 'button_trial_activate', 'button_help_devices', 'button_guide_ios', 'button_guide_android', 'button_guide_windows', 'button_guide_macos', 'button_pay_stars', 'button_pay_yookassa', 'button_pay_crypto', 'button_pay_balance', 'button_pay_open_invoice', 'button_subscription_qr', 'button_subscription_extend', 'button_reserve_open', 'button_reserve_qr', 'button_key_copy', 'button_key_qr', 'button_key_replace', 'button_key_delete']
CONTENT_PAGE_GROUPS: dict[str, list[str]] = {"texts": USER_TEXT_CONTENT_KEYS, "buttons": USER_BUTTON_CONTENT_KEYS}
BUTTON_LABEL_DEFAULTS: dict[str, str] = {label_key: DEFAULT_CONTENT[page_key][1] for label_key, page_key in BUTTON_LABEL_PAGE_KEYS.items()}


DEFAULT_TARIFFS: list[dict[str, Any]] = [
    {"name": "Старт", "days": 30, "price_rub": Decimal("299.00"), "price_stars": 450, "description": "1 месяц доступа"},
    {"name": "Оптимум", "days": 90, "price_rub": Decimal("799.00"), "price_stars": 1190, "description": "3 месяца доступа"},
    {"name": "Год", "days": 365, "price_rub": Decimal("2490.00"), "price_stars": 3690, "description": "12 месяцев доступа"},
]


class Store:
    def __init__(self) -> None:
        self.session_factory = SessionLocal

    async def seed_defaults(self) -> None:
        async with self.session_factory() as session:
            for key, enabled in DEFAULT_TOGGLES.items():
                if not await session.get(FeatureToggle, key):
                    session.add(FeatureToggle(key=key, is_enabled=enabled))

            for key, value in DEFAULT_SETTINGS.items():
                if not await session.get(AppSetting, key):
                    session.add(AppSetting(key=key, value=value))

            for key, payload in DEFAULT_CONTENT.items():
                title, body = payload
                page = await session.get(ContentPage, key)
                if not page:
                    session.add(ContentPage(key=key, title=title, body=body))
                else:
                    page.title = title
                    current_body = (page.body or '').strip()
                    if not current_body:
                        page.body = body
                    elif key in LEGACY_BRANDING_REFRESH_KEYS and 'VPN' in current_body:
                        page.body = body
                    elif key in LEGACY_BRANDING_EXACT_BODIES and current_body == LEGACY_BRANDING_EXACT_BODIES[key]:
                        page.body = body
            tariffs_count = await session.scalar(select(func.count(Tariff.id)))
            if not tariffs_count:
                for tariff in DEFAULT_TARIFFS:
                    session.add(Tariff(**tariff))

            await session.commit()

    async def get_or_create_user(self, tg_user: TelegramUser, referral_code: str | None = None) -> User:
        async with self.session_factory() as session:
            result = await session.execute(select(User).where(User.telegram_id == tg_user.id))
            user = result.scalar_one_or_none()
            if user is None:
                user = User(
                    telegram_id=tg_user.id,
                    username=tg_user.username,
                    full_name=' '.join(part for part in [tg_user.first_name, tg_user.last_name] if part).strip() or None,
                    invite_code=f'ref{tg_user.id}',
                    referred_by_code=referral_code,
                    admin_role='owner' if tg_user.id in settings.admin_ids else 'user',
                    is_admin=tg_user.id in settings.admin_ids,
                )
                session.add(user)
                await session.commit()
                await session.refresh(user)
                return user

            updated = False
            full_name = ' '.join(part for part in [tg_user.first_name, tg_user.last_name] if part).strip() or None
            if user.username != tg_user.username:
                user.username = tg_user.username
                updated = True
            if user.full_name != full_name:
                user.full_name = full_name
                updated = True
            expected_role = 'owner' if tg_user.id in settings.admin_ids else (user.admin_role or 'user')
            if user.admin_role != expected_role:
                user.admin_role = expected_role
                updated = True
            expected_is_admin = user.admin_role != 'user' or tg_user.id in settings.admin_ids
            if user.is_admin != expected_is_admin:
                user.is_admin = expected_is_admin
                updated = True
            if updated:
                await session.commit()
                await session.refresh(user)
            return user

    async def get_user_by_telegram_id(self, telegram_id: int) -> User | None:
        async with self.session_factory() as session:
            return await session.scalar(select(User).where(User.telegram_id == telegram_id))

    async def get_user_by_id(self, user_id: int) -> User | None:
        async with self.session_factory() as session:
            return await session.scalar(select(User).where(User.id == user_id))

    async def toggle_user_blocked(self, user_id: int) -> User | None:
        async with self.session_factory() as session:
            user = await session.get(User, user_id)
            if not user:
                return None
            user.is_blocked = not user.is_blocked
            await session.commit()
            await session.refresh(user)
            return user
    async def get_user_summary(self, telegram_id: int) -> User | None:
        async with self.session_factory() as session:
            return await session.scalar(
                select(User)
                .options(
                    selectinload(User.subscriptions).selectinload(Subscription.tariff),
                    selectinload(User.subscriptions).selectinload(Subscription.server),
                    selectinload(User.subscriptions).selectinload(Subscription.user),
                    selectinload(User.subscriptions).selectinload(Subscription.keys).selectinload(VpnKey.server),
                    selectinload(User.subscriptions).selectinload(Subscription.keys).selectinload(VpnKey.subscription),
                    selectinload(User.referrals),
                )
                .where(User.telegram_id == telegram_id)
            )

    async def get_user_admin_summary(self, user_id: int) -> User | None:
        async with self.session_factory() as session:
            return await session.scalar(
                select(User)
                .options(
                    selectinload(User.subscriptions).selectinload(Subscription.tariff),
                    selectinload(User.subscriptions).selectinload(Subscription.server),
                    selectinload(User.subscriptions).selectinload(Subscription.user),
                    selectinload(User.subscriptions).selectinload(Subscription.keys).selectinload(VpnKey.server),
                    selectinload(User.subscriptions).selectinload(Subscription.keys).selectinload(VpnKey.subscription),
                    selectinload(User.payments),
                    selectinload(User.balance_operations),
                    selectinload(User.referrals),
                )
                .where(User.id == user_id)
            )

    async def list_tariffs(self, only_active: bool = True) -> list[Tariff]:
        async with self.session_factory() as session:
            stmt = select(Tariff).order_by(Tariff.days.asc())
            if only_active:
                stmt = stmt.where(Tariff.is_active.is_(True))
            result = await session.scalars(stmt)
            return list(result)

    async def get_tariff(self, tariff_id: int) -> Tariff | None:
        async with self.session_factory() as session:
            return await session.get(Tariff, tariff_id)

    async def create_tariff(self, name: str, days: int, price_rub: Decimal, price_stars: int, description: str) -> Tariff:
        async with self.session_factory() as session:
            tariff = Tariff(
                name=name.strip(),
                days=days,
                price_rub=price_rub,
                price_stars=price_stars,
                description=description.strip(),
            )
            session.add(tariff)
            await session.commit()
            await session.refresh(tariff)
            return tariff

    async def update_tariff(self, tariff_id: int, name: str, days: int, price_rub: Decimal, price_stars: int, description: str) -> Tariff | None:
        async with self.session_factory() as session:
            tariff = await session.get(Tariff, tariff_id)
            if not tariff:
                return None
            tariff.name = name.strip()
            tariff.days = days
            tariff.price_rub = price_rub
            tariff.price_stars = price_stars
            tariff.description = description.strip()
            await session.commit()
            await session.refresh(tariff)
            return tariff

    async def delete_tariff(self, tariff_id: int) -> tuple[bool, str]:
        async with self.session_factory() as session:
            tariff = await session.get(Tariff, tariff_id)
            if not tariff:
                return False, 'Тариф не найден.'

            subscriptions_count = await session.scalar(select(func.count(Subscription.id)).where(Subscription.tariff_id == tariff_id)) or 0
            payments_count = await session.scalar(select(func.count(Payment.id)).where(Payment.tariff_id == tariff_id)) or 0
            if subscriptions_count or payments_count:
                return False, 'Тариф уже использовался в оплатах или подписках. Его можно отредактировать или скрыть, но не удалить.'

            tariff_name = tariff.name
            await session.delete(tariff)
            await session.commit()
            return True, f'Тариф {tariff_name} удалён.'

    async def toggle_tariff(self, tariff_id: int) -> Tariff | None:
        async with self.session_factory() as session:
            tariff = await session.get(Tariff, tariff_id)
            if not tariff:
                return None
            tariff.is_active = not tariff.is_active
            await session.commit()
            await session.refresh(tariff)
            return tariff

    async def list_servers(self) -> list[Server]:
        async with self.session_factory() as session:
            result = await session.scalars(select(Server).order_by(Server.id.asc()))
            return list(result)

    async def get_server(self, server_id: int) -> Server | None:
        async with self.session_factory() as session:
            return await session.get(Server, server_id)

    async def create_server(self, name: str, base_url: str, username: str, password: str, inbound_id: int) -> Server:
        async with self.session_factory() as session:
            server = Server(
                name=name.strip(),
                base_url=base_url.strip().rstrip("/"),
                username=username.strip(),
                password=password.strip(),
                inbound_id=inbound_id,
            )
            session.add(server)
            await session.commit()
            await session.refresh(server)
            return server

    async def toggle_server_enabled(self, server_id: int) -> Server | None:
        async with self.session_factory() as session:
            server = await session.get(Server, server_id)
            if not server:
                return None
            server.is_enabled = not server.is_enabled
            await session.commit()
            await session.refresh(server)
            return server

    async def toggle_server_trial(self, server_id: int) -> Server | None:
        async with self.session_factory() as session:
            server = await session.get(Server, server_id)
            if not server:
                return None
            server.is_trial_available = not server.is_trial_available
            await session.commit()
            await session.refresh(server)
            return server

    async def delete_server(self, server_id: int) -> tuple[bool, str]:
        async with self.session_factory() as session:
            server = await session.get(Server, server_id)
            if not server:
                return False, "Сервер не найден."

            subscriptions_count = await session.scalar(select(func.count(Subscription.id)).where(Subscription.server_id == server_id)) or 0
            keys_count = await session.scalar(select(func.count(VpnKey.id)).where(VpnKey.server_id == server_id)) or 0
            if subscriptions_count or keys_count:
                return False, "Нельзя удалить сервер, к нему уже привязаны подписки или ключи. Сначала скройте его."

            server_name = server.name
            await session.delete(server)
            await session.commit()
            return True, f"Сервер {server_name} удалён."


    async def get_server_agent_config(self, server_id: int) -> dict[str, str]:
        keys = {
            'url': f'server_agent_url_{server_id}',
            'token': f'server_agent_token_{server_id}',
        }
        async with self.session_factory() as session:
            rows = list(await session.scalars(select(AppSetting).where(AppSetting.key.in_(list(keys.values())))))
        values = {name: '' for name in keys}
        reverse = {value: key for key, value in keys.items()}
        for row in rows:
            values[reverse[row.key]] = row.value
        return values

    async def set_server_agent_config(self, server_id: int, url: str, token: str) -> None:
        await self.set_setting(f'server_agent_url_{server_id}', (url or '').strip())
        await self.set_setting(f'server_agent_token_{server_id}', (token or '').strip())

    async def clear_server_agent_config(self, server_id: int) -> None:
        await self.set_server_agent_config(server_id, '', '')
    def _server_billing_keys(self, server_id: int) -> dict[str, str]:
        return {
            'amount_rub': f'server_billing_amount_rub_{server_id}',
            'next_due': f'server_billing_next_due_{server_id}',
            'period_days': f'server_billing_period_days_{server_id}',
            'remind_days': f'server_billing_remind_days_{server_id}',
            'last_notice': f'server_billing_last_notice_{server_id}',
        }

    async def get_server_billing_config(self, server_id: int) -> dict[str, Any]:
        keys = self._server_billing_keys(server_id)
        defaults = {
            'amount_rub': '0',
            'next_due': '',
            'period_days': '30',
            'remind_days': '3',
            'last_notice': '',
        }
        async with self.session_factory() as session:
            rows = list(await session.scalars(select(AppSetting).where(AppSetting.key.in_(list(keys.values())))))
        values = defaults.copy()
        reverse = {value: key for key, value in keys.items()}
        for row in rows:
            values[reverse[row.key]] = row.value
        try:
            amount_rub = Decimal(str(values['amount_rub'] or '0')).quantize(Decimal('0.01'))
        except (InvalidOperation, ValueError):
            amount_rub = Decimal('0.00')
        next_due_raw = (values['next_due'] or '').strip()
        next_due = None
        if next_due_raw:
            for fmt in ('%Y-%m-%d', '%d.%m.%Y'):
                try:
                    next_due = datetime.strptime(next_due_raw, fmt).date()
                    break
                except ValueError:
                    continue
        try:
            period_days = max(int(str(values['period_days'] or '30')), 1)
        except ValueError:
            period_days = 30
        try:
            remind_days = max(int(str(values['remind_days'] or '3')), 0)
        except ValueError:
            remind_days = 3
        return {
            'amount_rub': amount_rub,
            'next_due': next_due,
            'period_days': period_days,
            'remind_days': remind_days,
            'last_notice': (values['last_notice'] or '').strip(),
            'configured': amount_rub > Decimal('0') and next_due is not None,
        }

    async def set_server_billing_config(self, server_id: int, amount_rub: Decimal, next_due, period_days: int, remind_days: int) -> None:
        keys = self._server_billing_keys(server_id)
        await self.set_setting(keys['amount_rub'], str(amount_rub.quantize(Decimal('0.01'))))
        await self.set_setting(keys['next_due'], next_due.strftime('%Y-%m-%d') if next_due else '')
        await self.set_setting(keys['period_days'], str(max(period_days, 1)))
        await self.set_setting(keys['remind_days'], str(max(remind_days, 0)))
        await self.set_setting(keys['last_notice'], '')

    async def clear_server_billing_config(self, server_id: int) -> None:
        await self.set_server_billing_config(server_id, Decimal('0.00'), None, 30, 3)

    async def set_server_billing_last_notice(self, server_id: int, value: str) -> None:
        keys = self._server_billing_keys(server_id)
        await self.set_setting(keys['last_notice'], (value or '').strip())

    async def mark_server_billing_paid(self, server_id: int):
        config = await self.get_server_billing_config(server_id)
        if not config.get('configured'):
            return None
        next_due = config.get('next_due')
        if not next_due:
            return None
        today = datetime.utcnow().date()
        base_date = next_due if next_due >= today else today
        new_due = base_date + timedelta(days=int(config.get('period_days', 30)))
        await self.set_server_billing_config(
            server_id,
            Decimal(str(config.get('amount_rub', Decimal('0.00')))),
            new_due,
            int(config.get('period_days', 30)),
            int(config.get('remind_days', 3)),
        )
        return await self.get_server_billing_config(server_id)

    async def list_server_billing_items(self) -> list[dict[str, Any]]:
        servers = await self.list_servers()
        items: list[dict[str, Any]] = []
        for server in servers:
            config = await self.get_server_billing_config(server.id)
            items.append({
                'server_id': server.id,
                'server_name': server.name,
                'base_url': server.base_url,
                **config,
            })
        return items


    def _server_reissue_key(self, server_id: int) -> str:
        return f'server_reissue_count_{server_id}'

    async def get_server_reissue_count(self, server_id: int) -> int:
        return await self.get_int_setting(self._server_reissue_key(server_id), 0)

    async def get_server_reissue_counts(self, server_ids: list[int]) -> dict[int, int]:
        if not server_ids:
            return {}
        keys = {server_id: self._server_reissue_key(server_id) for server_id in server_ids}
        async with self.session_factory() as session:
            rows = list(await session.scalars(select(AppSetting).where(AppSetting.key.in_(list(keys.values())))))
        raw_map = {row.key: row.value for row in rows}
        result: dict[int, int] = {}
        for server_id, key in keys.items():
            try:
                result[server_id] = max(int(str(raw_map.get(key, '0') or '0')), 0)
            except ValueError:
                result[server_id] = 0
        return result

    async def increment_server_reissue_count(self, server_id: int, delta: int = 1) -> int:
        key = self._server_reissue_key(server_id)
        current = await self.get_int_setting(key, 0)
        new_value = max(current + int(delta), 0)
        await self.set_setting(key, str(new_value))
        return new_value

    async def update_server_health(self, server_id: int, status: str, cpu_percent: int, ram_percent: int, error: str = "") -> None:
        async with self.session_factory() as session:
            server = await session.get(Server, server_id)
            if not server:
                return
            server.health_status = status
            server.cpu_percent = cpu_percent
            server.ram_percent = ram_percent
            server.last_error = error
            server.last_checked_at = datetime.utcnow()
            await session.commit()

    async def list_balanced_servers(self, trial_only: bool = False) -> list[Server]:
        now = datetime.utcnow()
        async with self.session_factory() as session:
            stmt = select(Server).where(Server.is_enabled.is_(True)).order_by(Server.id.asc())
            if trial_only:
                stmt = stmt.where(Server.is_trial_available.is_(True))
            servers = list(await session.scalars(stmt))
            if not servers:
                return []

            preferred = [server for server in servers if server.health_status == "online"] or servers
            server_ids = [server.id for server in preferred]
            counts_result = await session.execute(
                select(Subscription.server_id, func.count(Subscription.id))
                .where(
                    Subscription.server_id.in_(server_ids),
                    Subscription.status == "active",
                    Subscription.ends_at > now,
                )
                .group_by(Subscription.server_id)
            )
            counts = {int(server_id): int(count) for server_id, count in counts_result.all() if server_id is not None}

            buckets: dict[int, list[Server]] = {}
            for server in preferred:
                buckets.setdefault(counts.get(server.id, 0), []).append(server)

            ordered: list[Server] = []
            for load in sorted(buckets):
                group = buckets[load]
                random.shuffle(group)
                ordered.extend(group)
            return ordered

    async def choose_server(self, trial_only: bool = False) -> Server | None:
        servers = await self.list_balanced_servers(trial_only=trial_only)
        return servers[0] if servers else None

    async def list_content_pages(self, group: str | None = None) -> list[ContentPage]:
        async with self.session_factory() as session:
            if group and group in CONTENT_PAGE_GROUPS:
                keys = CONTENT_PAGE_GROUPS[group]
                rows = list(await session.scalars(select(ContentPage).where(ContentPage.key.in_(keys))))
                rows_map = {row.key: row for row in rows}
                return [rows_map[key] for key in keys if key in rows_map]
            result = await session.scalars(select(ContentPage).order_by(ContentPage.key.asc()))
            return list(result)

    async def get_content(self, key: str) -> ContentPage | None:
        async with self.session_factory() as session:
            return await session.get(ContentPage, key)

    async def set_content(self, key: str, body: str) -> None:
        async with self.session_factory() as session:
            page = await session.get(ContentPage, key)
            value = (body or '').strip()
            if page:
                page.body = value
            else:
                title, default_body = DEFAULT_CONTENT.get(key, (key, ''))
                session.add(ContentPage(key=key, title=title, body=value if body is not None else default_body))
            await session.commit()

    async def get_user_button_labels(self) -> dict[str, str]:
        async with self.session_factory() as session:
            rows = list(await session.scalars(select(ContentPage).where(ContentPage.key.in_(USER_BUTTON_CONTENT_KEYS))))
        reverse = {page_key: label_key for label_key, page_key in BUTTON_LABEL_PAGE_KEYS.items()}
        labels = BUTTON_LABEL_DEFAULTS.copy()
        for row in rows:
            label_key = reverse.get(row.key)
            if label_key and (row.body or '').strip():
                labels[label_key] = row.body.strip()
        return labels

    async def get_ui_snapshot(self) -> dict[str, Any]:
        toggle_keys = ["section_referral", "section_trial", "payment_stars", "payment_yookassa", "payment_crypto", "payment_balance"]
        setting_keys = ["support_chat_url", "channel_url", "terms_url", "referral_percent", "trial_days"]
        async with self.session_factory() as session:
            toggle_rows = list(await session.scalars(select(FeatureToggle).where(FeatureToggle.key.in_(toggle_keys))))
            setting_rows = list(await session.scalars(select(AppSetting).where(AppSetting.key.in_(setting_keys))))
            content_rows = list(await session.scalars(select(ContentPage).where(ContentPage.key.in_(USER_BUTTON_CONTENT_KEYS))))

        toggles = {key: DEFAULT_TOGGLES.get(key, True) for key in toggle_keys}
        for row in toggle_rows:
            toggles[row.key] = row.is_enabled

        values = {key: DEFAULT_SETTINGS.get(key, '') for key in setting_keys}
        for row in setting_rows:
            values[row.key] = row.value

        try:
            referral_percent = int(values['referral_percent'] or str(settings.referral_default_percent))
        except (TypeError, ValueError):
            referral_percent = settings.referral_default_percent
        try:
            trial_days = int(values['trial_days'] or str(settings.trial_default_days))
        except (TypeError, ValueError):
            trial_days = settings.trial_default_days

        reverse = {page_key: label_key for label_key, page_key in BUTTON_LABEL_PAGE_KEYS.items()}
        button_labels = BUTTON_LABEL_DEFAULTS.copy()
        for row in content_rows:
            label_key = reverse.get(row.key)
            if label_key and (row.body or '').strip():
                button_labels[label_key] = row.body.strip()

        return {
            'show_referral': toggles['section_referral'],
            'show_trial': toggles['section_trial'],
            'payment_methods': [method for method, key in (('stars', 'payment_stars'), ('yookassa', 'payment_yookassa'), ('crypto', 'payment_crypto'), ('balance', 'payment_balance')) if toggles[key]],
            'channel_url': values['channel_url'] or settings.channel_url,
            'support_chat_url': values['support_chat_url'] or settings.support_chat_url,
            'terms_url': values['terms_url'] or settings.terms_url,
            'referral_percent': referral_percent,
            'trial_days': trial_days,
            'button_labels': button_labels,
        }

    async def get_toggle(self, key: str, default: bool = True) -> bool:
        async with self.session_factory() as session:
            toggle = await session.get(FeatureToggle, key)
            if toggle is None:
                return default
            return toggle.is_enabled

    async def list_toggles(self) -> list[FeatureToggle]:
        async with self.session_factory() as session:
            result = await session.scalars(select(FeatureToggle).order_by(FeatureToggle.key.asc()))
            return list(result)

    async def toggle_feature(self, key: str) -> bool:
        async with self.session_factory() as session:
            toggle = await session.get(FeatureToggle, key)
            if not toggle:
                toggle = FeatureToggle(key=key, is_enabled=True)
                session.add(toggle)
            toggle.is_enabled = not toggle.is_enabled
            await session.commit()
            return toggle.is_enabled

    async def get_setting(self, key: str, default: str = "") -> str:
        async with self.session_factory() as session:
            setting = await session.get(AppSetting, key)
            if setting is None:
                return default
            return setting.value

    async def set_setting(self, key: str, value: str) -> None:
        async with self.session_factory() as session:
            setting = await session.get(AppSetting, key)
            if not setting:
                setting = AppSetting(key=key, value=value)
                session.add(setting)
            else:
                setting.value = value
            await session.commit()

    async def get_int_setting(self, key: str, default: int) -> int:
        try:
            return int(await self.get_setting(key, str(default)))
        except ValueError:
            return default

    async def get_payment_settings_snapshot(self) -> dict[str, Any]:
        keys = [
            'yookassa_shop_id',
            'yookassa_secret_key',
            'yookassa_return_url',
            'crypto_pay_token',
            'crypto_pay_use_testnet',
            'crypto_pay_assets',
        ]
        defaults = {
            'yookassa_shop_id': settings.yookassa_shop_id,
            'yookassa_secret_key': settings.yookassa_secret_key,
            'yookassa_return_url': settings.yookassa_return_url,
            'crypto_pay_token': settings.crypto_pay_token,
            'crypto_pay_use_testnet': 'true' if settings.crypto_pay_use_testnet else 'false',
            'crypto_pay_assets': settings.crypto_pay_assets_raw,
        }
        async with self.session_factory() as session:
            rows = list(await session.scalars(select(AppSetting).where(AppSetting.key.in_(keys))))

        values = defaults.copy()
        for row in rows:
            values[row.key] = row.value

        use_testnet_raw = str(values['crypto_pay_use_testnet']).strip().lower()
        return {
            'yookassa_shop_id': (values['yookassa_shop_id'] or '').strip(),
            'yookassa_secret_key': (values['yookassa_secret_key'] or '').strip(),
            'yookassa_return_url': (values['yookassa_return_url'] or settings.yookassa_return_url).strip(),
            'crypto_pay_token': (values['crypto_pay_token'] or '').strip(),
            'crypto_pay_use_testnet': use_testnet_raw in {'1', 'true', 'yes', 'on'},
            'crypto_pay_assets': [chunk.strip().upper() for chunk in str(values['crypto_pay_assets'] or settings.crypto_pay_assets_raw).split(',') if chunk.strip()],
        }

    async def set_payment_provider_settings(self, provider: str, payload: dict[str, Any]) -> None:
        mapping = {
            'yookassa': ('yookassa_shop_id', 'yookassa_secret_key', 'yookassa_return_url'),
            'crypto': ('crypto_pay_token', 'crypto_pay_use_testnet', 'crypto_pay_assets'),
        }
        keys = mapping.get(provider)
        if not keys:
            return
        for key in keys:
            value = payload.get(key, '')
            if isinstance(value, bool):
                value = 'true' if value else 'false'
            elif isinstance(value, list):
                value = ','.join(str(item).strip().upper() for item in value if str(item).strip())
            await self.set_setting(key, str(value).strip())

    async def create_payment(self, user_id: int, tariff_id: int, method: str, amount: Decimal, currency: str, payload: str) -> Payment:
        async with self.session_factory() as session:
            payment = Payment(
                user_id=user_id,
                tariff_id=tariff_id,
                method=method,
                amount=amount,
                currency=currency,
                payload=payload,
            )
            session.add(payment)
            await session.commit()
            await session.refresh(payment)
            return payment

    async def create_balance_payment(self, user_id: int, tariff_id: int, amount: Decimal, payload: str, description: str) -> tuple[Payment | None, str | None]:
        async with self.session_factory() as session:
            user = await session.get(User, user_id)
            if not user:
                return None, 'Пользователь не найден.'
            if (user.balance or Decimal('0')) < amount:
                return None, 'Недостаточно средств на балансе.'

            payment = Payment(
                user_id=user_id,
                tariff_id=tariff_id,
                method='balance',
                amount=amount,
                currency='RUB',
                payload=payload,
                status='paid',
                paid_at=datetime.utcnow(),
            )
            user.balance = (user.balance or Decimal('0')) - amount
            session.add(payment)
            session.add(
                BalanceOperation(
                    user_id=user.id,
                    payment=payment,
                    kind='balance_payment',
                    amount=-amount,
                    description=description,
                    balance_after=user.balance,
                )
            )
            await session.commit()
            await session.refresh(payment)
            return payment, None

    async def get_payment_bundle(self, payment_id: int) -> tuple[Payment | None, User | None, Tariff | None]:
        async with self.session_factory() as session:
            payment = await session.scalar(select(Payment).where(Payment.id == payment_id))
            if not payment:
                return None, None, None
            user = await session.get(User, payment.user_id)
            tariff = await session.get(Tariff, payment.tariff_id)
            return payment, user, tariff

    async def get_payment_by_payload(self, payload: str) -> Payment | None:
        async with self.session_factory() as session:
            return await session.scalar(select(Payment).where(Payment.payload == payload))

    async def get_payment_by_provider_payment_id(self, provider_payment_id: str) -> Payment | None:
        async with self.session_factory() as session:
            return await session.scalar(select(Payment).where(Payment.provider_payment_id == provider_payment_id))

    async def try_mark_payment_activation_notice_sent(self, payment_id: int) -> bool:
        async with self.session_factory() as session:
            result = await session.execute(
                update(Payment)
                .where(Payment.id == payment_id, Payment.activation_notice_sent_at.is_(None))
                .values(activation_notice_sent_at=datetime.utcnow())
            )
            await session.commit()
            return bool(result.rowcount)

    async def clear_payment_activation_notice_sent(self, payment_id: int) -> None:
        async with self.session_factory() as session:
            await session.execute(
                update(Payment)
                .where(Payment.id == payment_id)
                .values(activation_notice_sent_at=None)
            )
            await session.commit()

    async def update_payment_provider(self, payment_id: int, provider_payment_id: str, provider_url: str) -> None:
        async with self.session_factory() as session:
            payment = await session.get(Payment, payment_id)
            if not payment:
                return
            payment.provider_payment_id = provider_payment_id
            payment.provider_url = provider_url
            await session.commit()

    async def list_pending_external_payments(self) -> list[Payment]:
        async with self.session_factory() as session:
            result = await session.scalars(
                select(Payment).where(
                    Payment.status == "pending",
                    Payment.method.in_(["yookassa", "crypto"]),
                    Payment.provider_payment_id != "",
                )
            )
            return list(result)

    async def mark_payment_paid(self, payment_id: int) -> Payment | None:
        async with self.session_factory() as session:
            payment = await session.get(Payment, payment_id)
            if not payment or payment.status == "paid":
                return payment
            payment.status = "paid"
            payment.paid_at = datetime.utcnow()
            await session.commit()
            await session.refresh(payment)
            return payment

    async def get_subscription_by_payment(self, payment_id: int) -> Subscription | None:
        async with self.session_factory() as session:
            return await session.scalar(select(Subscription).where(Subscription.source_payment_id == payment_id))

    async def get_subscription_details(self, subscription_id: int) -> Subscription | None:
        async with self.session_factory() as session:
            return await session.scalar(
                select(Subscription)
                .options(
                    selectinload(Subscription.tariff),
                    selectinload(Subscription.user),
                    selectinload(Subscription.server),
                    selectinload(Subscription.keys).selectinload(VpnKey.server),
                )
                .where(Subscription.id == subscription_id)
            )


    async def get_key_details(self, key_id: int) -> VpnKey | None:
        async with self.session_factory() as session:
            return await session.scalar(
                select(VpnKey)
                .options(
                    selectinload(VpnKey.server),
                    selectinload(VpnKey.subscription).selectinload(Subscription.tariff),
                    selectinload(VpnKey.subscription).selectinload(Subscription.server),
                    selectinload(VpnKey.subscription).selectinload(Subscription.user),
                    selectinload(VpnKey.subscription).selectinload(Subscription.keys).selectinload(VpnKey.server),
                )
                .where(VpnKey.id == key_id)
            )

    async def list_expiring_subscriptions(self, within_hours: int = 24) -> list[Subscription]:
        now = datetime.utcnow()
        threshold = now + timedelta(hours=within_hours)
        async with self.session_factory() as session:
            result = await session.scalars(
                select(Subscription)
                .options(
                    selectinload(Subscription.user),
                    selectinload(Subscription.server),
                    selectinload(Subscription.tariff),
                    selectinload(Subscription.keys).selectinload(VpnKey.server),
                )
                .where(
                    Subscription.status == "active",
                    Subscription.ends_at > now,
                    Subscription.ends_at <= threshold,
                    Subscription.expiry_notice_sent_at.is_(None),
                )
                .order_by(Subscription.ends_at.asc())
            )
            return list(result)

    async def mark_expiry_notice_sent(self, subscription_id: int) -> None:
        async with self.session_factory() as session:
            subscription = await session.get(Subscription, subscription_id)
            if not subscription:
                return
            subscription.expiry_notice_sent_at = datetime.utcnow()
            await session.commit()

    async def create_subscription(
        self,
        user_id: int,
        tariff_id: int | None,
        server_id: int | None,
        days: int,
        is_trial: bool,
        payment_id: int | None,
    ) -> Subscription:
        async with self.session_factory() as session:
            now = datetime.utcnow()
            subscription = Subscription(
                user_id=user_id,
                tariff_id=tariff_id,
                server_id=server_id,
                starts_at=now,
                ends_at=now + timedelta(days=days),
                is_trial=is_trial,
                source_payment_id=payment_id,
            )
            session.add(subscription)
            await session.commit()
            await session.refresh(subscription)
            return subscription

    async def set_subscription_server(self, subscription_id: int, server_id: int) -> None:
        async with self.session_factory() as session:
            subscription = await session.get(Subscription, subscription_id)
            if not subscription:
                return
            subscription.server_id = server_id
            await session.commit()

    async def create_vpn_key(
        self,
        subscription_id: int,
        server_id: int,
        label: str,
        access_url: str,
        external_id: str = "",
    ) -> VpnKey:
        async with self.session_factory() as session:
            key = VpnKey(
                subscription_id=subscription_id,
                server_id=server_id,
                label=label,
                access_url=access_url,
                external_id=external_id or None,
            )
            session.add(key)
            await session.commit()
            await session.refresh(key)
            return key

    async def update_key_usage(self, key_id: int, used_bytes: int) -> None:
        async with self.session_factory() as session:
            key = await session.get(VpnKey, key_id)
            if not key:
                return
            key.used_bytes = used_bytes
            await session.commit()


    async def extend_subscription(self, subscription_id: int, days: int) -> Subscription | None:
        if days <= 0:
            return None
        async with self.session_factory() as session:
            subscription = await session.get(Subscription, subscription_id)
            if not subscription:
                return None
            now = datetime.utcnow()
            base_time = subscription.ends_at if subscription.ends_at and subscription.ends_at > now else now
            subscription.status = 'active'
            subscription.ends_at = base_time + timedelta(days=days)
            subscription.expiry_notice_sent_at = None
            await session.commit()
        return await self.get_subscription_details(subscription_id)

    async def update_key_credentials(
        self,
        key_id: int,
        access_url: str,
        external_id: str,
        *,
        used_bytes: int | None = None,
        is_active: bool | None = None,
    ) -> VpnKey | None:
        async with self.session_factory() as session:
            key = await session.get(VpnKey, key_id)
            if not key:
                return None
            key.access_url = access_url
            key.external_id = external_id or None
            if used_bytes is not None:
                key.used_bytes = used_bytes
            if is_active is not None:
                key.is_active = is_active
            await session.commit()
            await session.refresh(key)
            return key

    async def deactivate_key(self, key_id: int) -> VpnKey | None:
        async with self.session_factory() as session:
            key = await session.get(VpnKey, key_id)
            if not key:
                return None
            key.is_active = False
            await session.commit()
            await session.refresh(key)
            return key

    async def delete_key_record(self, key_id: int) -> bool:
        async with self.session_factory() as session:
            key = await session.get(VpnKey, key_id)
            if not key:
                return False
            await session.delete(key)
            await session.commit()
            return True

    async def delete_empty_expired_subscription(self, subscription_id: int) -> bool:
        async with self.session_factory() as session:
            subscription = await session.scalar(
                select(Subscription)
                .options(selectinload(Subscription.keys))
                .where(Subscription.id == subscription_id)
            )
            if not subscription:
                return False
            if subscription.status == 'active' and is_future_datetime(getattr(subscription, 'ends_at', None)):
                return False
            if getattr(subscription, 'keys', None):
                return False
            await session.delete(subscription)
            await session.commit()
            return True
    async def list_servers_with_monitoring(self) -> list[Server]:
        async with self.session_factory() as session:
            result = await session.scalars(
                select(Server)
                .options(selectinload(Server.keys).selectinload(VpnKey.subscription))
                .order_by(Server.id.asc())
            )
            return list(result)

    async def get_server_monitoring_details(self, server_id: int) -> Server | None:
        async with self.session_factory() as session:
            return await session.scalar(
                select(Server)
                .options(selectinload(Server.keys).selectinload(VpnKey.subscription))
                .where(Server.id == server_id)
            )

    async def set_trial_claimed(self, user_id: int) -> None:
        async with self.session_factory() as session:
            user = await session.get(User, user_id)
            if not user:
                return
            user.trial_claimed = True
            await session.commit()

    async def add_balance(self, user_id: int, amount: Decimal, kind: str, description: str, payment_id: int | None = None) -> None:
        async with self.session_factory() as session:
            user = await session.get(User, user_id)
            if not user:
                return
            user.balance = (user.balance or Decimal("0")) + amount
            session.add(
                BalanceOperation(
                    user_id=user.id,
                    payment_id=payment_id,
                    kind=kind,
                    amount=amount,
                    description=description,
                    balance_after=user.balance,
                )
            )
            await session.commit()

    async def apply_referral_bonus(self, buyer_user_id: int, payment_amount: Decimal, payment_id: int) -> None:
        percent = Decimal(str(await self.get_int_setting("referral_percent", settings.referral_default_percent)))
        if percent <= 0:
            return

        async with self.session_factory() as session:
            buyer = await session.get(User, buyer_user_id)
            if not buyer or not buyer.referrer_id:
                return

            referrer = await session.get(User, buyer.referrer_id)
            if not referrer:
                return

            bonus = (payment_amount * percent / Decimal("100")).quantize(Decimal("0.01"))
            referrer.balance = (referrer.balance or Decimal("0")) + bonus
            session.add(
                BalanceOperation(
                    user_id=referrer.id,
                    payment_id=payment_id,
                    kind="referral_bonus",
                    amount=bonus,
                    description=f"Бонус за покупку реферала #{buyer.telegram_id}",
                    balance_after=referrer.balance,
                )
            )
            await session.commit()

    async def list_user_operations(self, user_id: int, limit: int = 10) -> list[BalanceOperation]:
        async with self.session_factory() as session:
            result = await session.scalars(
                select(BalanceOperation)
                .where(BalanceOperation.user_id == user_id)
                .order_by(desc(BalanceOperation.created_at))
                .limit(limit)
            )
            return list(result)

    async def list_user_referrals(self, user_id: int) -> list[User]:
        async with self.session_factory() as session:
            result = await session.scalars(select(User).where(User.referrer_id == user_id).order_by(User.created_at.desc()))
            return list(result)

    async def get_analytics_snapshot(self) -> dict[str, Any]:
        now = datetime.utcnow()
        last_day = now - timedelta(days=1)
        last_week = now - timedelta(days=7)
        last_month = now - timedelta(days=30)

        def to_decimal(value: Any) -> Decimal:
            if value is None:
                return Decimal('0.00')
            if isinstance(value, Decimal):
                return value
            return Decimal(str(value))

        async with self.session_factory() as session:
            total_users = await session.scalar(select(func.count(User.id))) or 0
            paying_users = await session.scalar(
                select(func.count(func.distinct(Payment.user_id))).where(Payment.status == 'paid')
            ) or 0
            active_users = await session.scalar(
                select(func.count(func.distinct(Subscription.user_id))).where(
                    Subscription.status == 'active',
                    Subscription.ends_at > now,
                )
            ) or 0
            active_subscriptions = await session.scalar(
                select(func.count(Subscription.id)).where(
                    Subscription.status == 'active',
                    Subscription.ends_at > now,
                )
            ) or 0
            expiring_24h = await session.scalar(
                select(func.count(Subscription.id)).where(
                    Subscription.status == 'active',
                    Subscription.ends_at > now,
                    Subscription.ends_at <= now + timedelta(hours=24),
                )
            ) or 0
            ended_7d = await session.scalar(
                select(func.count(Subscription.id)).where(
                    Subscription.ends_at <= now,
                    Subscription.ends_at >= last_week,
                )
            ) or 0
            new_users_7d = await session.scalar(
                select(func.count(User.id)).where(User.created_at >= last_week)
            ) or 0
            paid_24h_count = await session.scalar(
                select(func.count(Payment.id)).where(
                    Payment.status == 'paid',
                    Payment.paid_at.is_not(None),
                    Payment.paid_at >= last_day,
                )
            ) or 0
            paid_30d_count = await session.scalar(
                select(func.count(Payment.id)).where(
                    Payment.status == 'paid',
                    Payment.paid_at.is_not(None),
                    Payment.paid_at >= last_month,
                )
            ) or 0
            revenue_24h_rub = to_decimal(
                await session.scalar(
                    select(func.coalesce(func.sum(Payment.amount), 0)).where(
                        Payment.status == 'paid',
                        Payment.currency == 'RUB',
                        Payment.paid_at.is_not(None),
                        Payment.paid_at >= last_day,
                    )
                )
            )
            revenue_7d_rub = to_decimal(
                await session.scalar(
                    select(func.coalesce(func.sum(Payment.amount), 0)).where(
                        Payment.status == 'paid',
                        Payment.currency == 'RUB',
                        Payment.paid_at.is_not(None),
                        Payment.paid_at >= last_week,
                    )
                )
            )
            revenue_30d_rub = to_decimal(
                await session.scalar(
                    select(func.coalesce(func.sum(Payment.amount), 0)).where(
                        Payment.status == 'paid',
                        Payment.currency == 'RUB',
                        Payment.paid_at.is_not(None),
                        Payment.paid_at >= last_month,
                    )
                )
            )
            stars_30d = to_decimal(
                await session.scalar(
                    select(func.coalesce(func.sum(Payment.amount), 0)).where(
                        Payment.status == 'paid',
                        Payment.currency == 'XTR',
                        Payment.paid_at.is_not(None),
                        Payment.paid_at >= last_month,
                    )
                )
            )
            rub_payments_30d = await session.scalar(
                select(func.count(Payment.id)).where(
                    Payment.status == 'paid',
                    Payment.currency == 'RUB',
                    Payment.paid_at.is_not(None),
                    Payment.paid_at >= last_month,
                )
            ) or 0
            method_rows = (await session.execute(
                select(
                    Payment.method,
                    Payment.currency,
                    func.count(Payment.id),
                    func.coalesce(func.sum(Payment.amount), 0),
                )
                .where(
                    Payment.status == 'paid',
                    Payment.paid_at.is_not(None),
                    Payment.paid_at >= last_month,
                )
                .group_by(Payment.method, Payment.currency)
                .order_by(desc(func.count(Payment.id)))
            )).all()
            top_tariff_rows = (await session.execute(
                select(Tariff.name, func.count(Payment.id))
                .join(Payment, Payment.tariff_id == Tariff.id)
                .where(
                    Payment.status == 'paid',
                    Payment.paid_at.is_not(None),
                    Payment.paid_at >= last_month,
                )
                .group_by(Tariff.name)
                .order_by(desc(func.count(Payment.id)))
                .limit(5)
            )).all()

        billing_items = await self.list_server_billing_items()
        monitoring_servers = await self.list_servers_with_monitoring()
        today = now.date()
        configured_billing = [item for item in billing_items if item.get('configured')]
        monthly_server_cost_rub = sum((Decimal(str(item.get('amount_rub', Decimal('0.00')))) for item in configured_billing), Decimal('0.00')).quantize(Decimal('0.01')) if configured_billing else Decimal('0.00')

        server_user_counts: dict[int, int] = {}
        for server in monitoring_servers:
            user_ids: set[int] = set()
            for key in getattr(server, 'keys', []) or []:
                subscription = getattr(key, 'subscription', None)
                if not subscription or not getattr(key, 'is_active', False):
                    continue
                if subscription.status != 'active' or subscription.ends_at <= now:
                    continue
                if getattr(subscription, 'user_id', None) is not None:
                    user_ids.add(subscription.user_id)
            server_user_counts[server.id] = len(user_ids)

        server_cost_rows: list[dict[str, Any]] = []
        due_soon_server_payments = 0
        overdue_server_payments = 0
        for item in configured_billing:
            next_due = item.get('next_due')
            remind_days = int(item.get('remind_days', 3))
            days_left = None
            status = 'scheduled'
            if next_due is not None:
                days_left = (next_due - today).days
                if days_left < 0:
                    overdue_server_payments += 1
                    status = 'overdue'
                elif days_left <= remind_days:
                    due_soon_server_payments += 1
                    status = 'due_soon'
            active_users_for_server = int(server_user_counts.get(int(item['server_id']), 0))
            amount_rub = Decimal(str(item.get('amount_rub', Decimal('0.00')))).quantize(Decimal('0.01'))
            if active_users_for_server > 0:
                cost_per_user_rub = (amount_rub / Decimal(active_users_for_server)).quantize(Decimal('0.01'))
            else:
                cost_per_user_rub = amount_rub
            server_cost_rows.append({
                'server_id': item.get('server_id'),
                'server_name': str(item.get('server_name') or 'Сервер'),
                'amount_rub': amount_rub,
                'next_due': next_due,
                'period_days': int(item.get('period_days', 30)),
                'remind_days': remind_days,
                'days_left': days_left,
                'status': status,
                'active_users': active_users_for_server,
                'cost_per_user_rub': cost_per_user_rub,
            })
        server_cost_rows.sort(key=lambda item: (-item['cost_per_user_rub'], item['server_name']))

        conversion_percent = round((int(paying_users) / int(total_users) * 100), 1) if total_users else 0.0
        avg_check_rub_30d = (revenue_30d_rub / Decimal(str(rub_payments_30d))).quantize(Decimal('0.01')) if rub_payments_30d else Decimal('0.00')
        profit_30d_rub = (revenue_30d_rub - monthly_server_cost_rub).quantize(Decimal('0.01'))
        forecast_revenue_30d_rub = ((revenue_7d_rub / Decimal('7')) * Decimal('30')).quantize(Decimal('0.01')) if revenue_7d_rub > Decimal('0.00') else Decimal('0.00')
        forecast_profit_30d_rub = (forecast_revenue_30d_rub - monthly_server_cost_rub).quantize(Decimal('0.01'))
        gap_to_break_even_rub = max((monthly_server_cost_rub - forecast_revenue_30d_rub).quantize(Decimal('0.01')), Decimal('0.00'))
        renewals_to_break_even = None
        if gap_to_break_even_rub > Decimal('0.00'):
            if avg_check_rub_30d > Decimal('0.00'):
                renewals_to_break_even = ceil(gap_to_break_even_rub / avg_check_rub_30d)
        else:
            renewals_to_break_even = 0

        active_cost_rows = [item for item in server_cost_rows if item.get('active_users', 0) > 0]
        most_expensive_server_per_user = None
        if active_cost_rows:
            item = max(active_cost_rows, key=lambda row: (row['cost_per_user_rub'], row['amount_rub']))
            most_expensive_server_per_user = {
                'server_id': item['server_id'],
                'server_name': item['server_name'],
                'cost_per_user_rub': item['cost_per_user_rub'],
                'active_users': item['active_users'],
                'amount_rub': item['amount_rub'],
            }

        return {
            'total_users': int(total_users),
            'paying_users': int(paying_users),
            'conversion_percent': conversion_percent,
            'new_users_7d': int(new_users_7d),
            'active_users': int(active_users),
            'active_subscriptions': int(active_subscriptions),
            'expiring_24h': int(expiring_24h),
            'ended_7d': int(ended_7d),
            'paid_24h_count': int(paid_24h_count),
            'paid_30d_count': int(paid_30d_count),
            'revenue_24h_rub': revenue_24h_rub,
            'revenue_7d_rub': revenue_7d_rub,
            'revenue_30d_rub': revenue_30d_rub,
            'stars_30d': stars_30d,
            'avg_check_rub_30d': avg_check_rub_30d,
            'monthly_server_cost_rub': monthly_server_cost_rub,
            'profit_30d_rub': profit_30d_rub,
            'forecast_revenue_30d_rub': forecast_revenue_30d_rub,
            'forecast_profit_30d_rub': forecast_profit_30d_rub,
            'gap_to_break_even_rub': gap_to_break_even_rub,
            'renewals_to_break_even': renewals_to_break_even,
            'most_expensive_server_per_user': most_expensive_server_per_user,
            'due_soon_server_payments': due_soon_server_payments,
            'overdue_server_payments': overdue_server_payments,
            'configured_server_payments': len(configured_billing),
            'server_costs': server_cost_rows,
            'server_unit_economics': server_cost_rows[:8],
            'method_breakdown': [
                {
                    'method': str(method),
                    'currency': str(currency),
                    'count': int(count),
                    'amount': to_decimal(amount),
                }
                for method, currency, count, amount in method_rows
            ],
            'top_tariffs': [
                {'name': str(name), 'count': int(count)}
                for name, count in top_tariff_rows
            ],
        }

    async def cleanup_stale_data(
        self,
        *,
        pending_payment_days: int = 3,
        archived_key_days: int = 45,
        empty_subscription_days: int = 30,
    ) -> dict[str, int]:
        now = datetime.utcnow()
        pending_before = now - timedelta(days=pending_payment_days)
        archived_before = now - timedelta(days=archived_key_days)
        empty_before = now - timedelta(days=empty_subscription_days)
        stats = {
            'expired_payments': 0,
            'expired_subscriptions': 0,
            'deactivated_keys': 0,
            'deleted_keys': 0,
            'deleted_subscriptions': 0,
        }

        async with self.session_factory() as session:
            pending_payments = list(await session.scalars(
                select(Payment).where(
                    Payment.status == 'pending',
                    Payment.created_at < pending_before,
                )
            ))
            for payment in pending_payments:
                payment.status = 'expired'
                payment.provider_url = ''
                stats['expired_payments'] += 1

            expired_subscriptions = list(await session.scalars(
                select(Subscription)
                .options(selectinload(Subscription.keys))
                .where(
                    Subscription.status == 'active',
                    Subscription.ends_at <= now,
                )
            ))
            for subscription in expired_subscriptions:
                subscription.status = 'expired'
                stats['expired_subscriptions'] += 1
                for key in getattr(subscription, 'keys', []) or []:
                    if key.is_active:
                        key.is_active = False
                        stats['deactivated_keys'] += 1

            old_archived_keys = list(await session.scalars(
                select(VpnKey)
                .join(Subscription, VpnKey.subscription_id == Subscription.id)
                .where(
                    VpnKey.is_active.is_(False),
                    Subscription.ends_at <= archived_before,
                )
            ))
            for key in old_archived_keys:
                await session.delete(key)
                stats['deleted_keys'] += 1

            old_empty_subscriptions = list(await session.scalars(
                select(Subscription)
                .options(selectinload(Subscription.keys))
                .where(
                    Subscription.status != 'active',
                    Subscription.ends_at <= empty_before,
                )
            ))
            for subscription in old_empty_subscriptions:
                if getattr(subscription, 'keys', None):
                    continue
                await session.delete(subscription)
                stats['deleted_subscriptions'] += 1

            await session.commit()

        return stats
    async def list_admin_users(self) -> list[User]:
        async with self.session_factory() as session:
            stmt = select(User).order_by(User.admin_role.asc(), User.created_at.asc())
            if settings.admin_ids:
                stmt = stmt.where((User.admin_role != 'user') | (User.telegram_id.in_(tuple(settings.admin_ids))))
            else:
                stmt = stmt.where(User.admin_role != 'user')
            result = await session.scalars(stmt)
            return list(result)
    async def set_user_admin_role(self, user_id: int, role: str) -> User | None:
        normalized_role = (role or 'user').strip().lower()
        if normalized_role not in {'user', 'support', 'finance', 'ops', 'admin', 'owner'}:
            normalized_role = 'user'
        async with self.session_factory() as session:
            user = await session.get(User, user_id)
            if not user:
                return None
            user.admin_role = normalized_role
            user.is_admin = normalized_role != 'user'
            await session.commit()
            await session.refresh(user)
            return user

    async def log_admin_action(
        self,
        *,
        actor_user_id: int | None,
        action: str,
        description: str,
        target_user_id: int | None = None,
        target_server_id: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        async with self.session_factory() as session:
            session.add(
                AdminActionLog(
                    actor_user_id=actor_user_id,
                    action=(action or '').strip() or 'unknown',
                    description=(description or '').strip(),
                    target_user_id=target_user_id,
                    target_server_id=target_server_id,
                    details_json=json.dumps(details or {}, ensure_ascii=False),
                )
            )
            await session.commit()

    async def list_admin_action_logs(self, limit: int = 30) -> list[AdminActionLog]:
        async with self.session_factory() as session:
            result = await session.scalars(
                select(AdminActionLog)
                .options(
                    selectinload(AdminActionLog.actor),
                    selectinload(AdminActionLog.target_user),
                    selectinload(AdminActionLog.target_server),
                )
                .order_by(desc(AdminActionLog.created_at))
                .limit(limit)
            )
            return list(result)

    async def record_provisioning_failure(
        self,
        *,
        stage: str,
        error: str,
        server_id: int | None = None,
        server_name: str | None = None,
        subscription_id: int | None = None,
        user_telegram_id: int | None = None,
    ) -> None:
        async with self.session_factory() as session:
            session.add(
                ProvisioningFailureLog(
                    stage=(stage or '').strip() or 'unknown',
                    error=(error or '').strip() or 'Неизвестная ошибка',
                    server_id=server_id,
                    server_name=(server_name or '').strip(),
                    subscription_id=subscription_id,
                    user_telegram_id=user_telegram_id,
                )
            )
            await session.commit()

    async def list_provisioning_failures(
        self,
        *,
        limit: int = 30,
        server_id: int | None = None,
        user_telegram_id: int | None = None,
    ) -> list[ProvisioningFailureLog]:
        stmt = (
            select(ProvisioningFailureLog)
            .options(
                selectinload(ProvisioningFailureLog.server),
                selectinload(ProvisioningFailureLog.subscription),
            )
            .order_by(desc(ProvisioningFailureLog.created_at))
            .limit(limit)
        )
        if server_id is not None:
            stmt = stmt.where(ProvisioningFailureLog.server_id == server_id)
        if user_telegram_id is not None:
            stmt = stmt.where(ProvisioningFailureLog.user_telegram_id == user_telegram_id)
        async with self.session_factory() as session:
            result = await session.scalars(stmt)
            return list(result)

    async def get_admin_metrics(self) -> dict[str, int]:
        now = datetime.utcnow()
        async with self.session_factory() as session:
            total_users = await session.scalar(select(func.count(User.id))) or 0
            active_users = await session.scalar(
                select(func.count(func.distinct(Subscription.user_id))).where(
                    Subscription.status == "active",
                    Subscription.ends_at > now,
                )
            ) or 0
            pending_payments = await session.scalar(select(func.count(Payment.id)).where(Payment.status == "pending")) or 0
            servers = await session.scalar(select(func.count(Server.id))) or 0
            admin_users = await session.scalar(select(func.count(User.id)).where(User.admin_role != 'user')) or 0
            provisioning_failures = await session.scalar(
                select(func.count(ProvisioningFailureLog.id)).where(ProvisioningFailureLog.created_at >= now - timedelta(hours=3))
            ) or 0
            return {
                "users": int(total_users),
                "active_users": int(active_users),
                "pending_payments": int(pending_payments),
                "servers": int(servers),
                "admins": int(admin_users),
                "recent_provisioning_failures": int(provisioning_failures),
            }

    async def list_users(self, filter_key: str = "all", page: int = 1, page_size: int = 8) -> tuple[list[User], int]:
        now = datetime.utcnow()
        stmt: Select[tuple[User]] = select(User).order_by(User.created_at.desc())

        active_predicate = (Subscription.status == "active") & (Subscription.ends_at > now)
        if filter_key == "active":
            stmt = stmt.where(User.subscriptions.any(active_predicate))
        elif filter_key == "inactive":
            stmt = stmt.where(~User.subscriptions.any(active_predicate))
        elif filter_key == "never":
            stmt = stmt.where(~User.subscriptions.any())
        elif filter_key == "new":
            stmt = stmt.where(User.created_at >= now - timedelta(days=7))

        count_stmt = select(func.count()).select_from(stmt.subquery())
        offset = max(page - 1, 0) * page_size
        stmt = stmt.offset(offset).limit(page_size)

        async with self.session_factory() as session:
            total = await session.scalar(count_stmt) or 0
            users = list(await session.scalars(stmt))
            return users, int(total)

    async def get_user_filter_counts(self) -> dict[str, int]:
        now = datetime.utcnow()
        active_predicate = (Subscription.status == "active") & (Subscription.ends_at > now)
        new_predicate = User.created_at >= now - timedelta(days=7)
        async with self.session_factory() as session:
            all_count = await session.scalar(select(func.count(User.id))) or 0
            active_count = await session.scalar(select(func.count(User.id)).where(User.subscriptions.any(active_predicate))) or 0
            inactive_count = await session.scalar(select(func.count(User.id)).where(~User.subscriptions.any(active_predicate))) or 0
            never_count = await session.scalar(select(func.count(User.id)).where(~User.subscriptions.any())) or 0
            new_count = await session.scalar(select(func.count(User.id)).where(new_predicate)) or 0
        return {
            'all': int(all_count),
            'active': int(active_count),
            'inactive': int(inactive_count),
            'never': int(never_count),
            'new': int(new_count),
        }

    async def get_broadcast_targets(self, filter_key: str) -> list[int]:
        users, _ = await self.list_users(filter_key=filter_key, page=1, page_size=10000)
        return [user.telegram_id for user in users]

    async def record_failed_server_check(self, server_id: int, error: str) -> None:
        error_text = (error or "Неизвестная ошибка").strip() or "Неизвестная ошибка"
        logger.warning("Server check failed for %s: %s", server_id, error_text)
        server = await self.get_server(server_id)
        await self.update_server_health(server_id, "offline", 0, 0, error_text)
        await self.record_provisioning_failure(
            stage='server_check',
            error=error_text,
            server_id=server_id,
            server_name=getattr(server, 'name', ''),
        )
