from __future__ import annotations

import asyncio
import csv
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Iterable
from uuid import uuid4

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, LabeledPrice, LinkPreviewOptions, Message, PreCheckoutQuery, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.keyboards import (
    ADMIN_LABEL,
    BUY_LABEL,
    HELP_LABEL,
    PROFILE_LABEL,
    REFERRAL_LABEL,
    TRIAL_LABEL,
    access_result_keyboard,
    admin_guide_keyboard,
    admin_panel_keyboard,
    analytics_keyboard,
    finance_keyboard,
    admin_result_keyboard,
    backup_keyboard,
    back_keyboard,
    broadcast_filters_keyboard,
    build_main_menu,
    contents_keyboard,
    device_guide_keyboard,
    device_guides_menu_keyboard,
    help_inline_keyboard,
    home_inline_keyboard,
    key_detail_keyboard,
    payment_methods_keyboard,
    profile_inline_keyboard,
    referral_admin_keyboard,
    reserve_admin_keyboard,
    referral_inline_keyboard,
    server_actions_keyboard,
    servers_keyboard,
    subscription_detail_keyboard,
    tariff_detail_keyboard,
    tariffs_admin_keyboard,
    tariffs_keyboard,
    toggles_keyboard,
    trial_admin_keyboard,
    trial_inline_keyboard,
    updates_admin_keyboard,
    user_actions_keyboard,
    user_operations_keyboard,
    user_referrals_keyboard,
    users_filters_keyboard,
    users_list_keyboard,
)
from app.bot.states import (
    BalanceGrantState,
    BroadcastState,
    ManualSubscriptionState,
    PaymentConfigState,
    ReferralEditState,
    ServerAgentState,
    ServerBillingState,
    ServerCommandState,
    ServerCreateState,
    TariffCreateState,
    TariffEditState,
    TextEditState,
    TrialEditState,
)
from app.config import settings
from app.services.backup import BackupService
from app.services.payments import PaymentGatewayError, PaymentService
from app.services.provisioning import ProvisioningService
from app.services.qr_codes import QRCodeUnavailableError, build_qr_png_bytes
from app.services.server_agents import ServerAgentClient, ServerAgentError
from app.services.store import Store
from app.services.subscription_links import build_reserve_access_url, build_subscription_url, subscription_server_names
from app.services.updater import UpdateService
from app.utils import format_gb, format_money, is_future_datetime


class BotController:
    def __init__(self, bot: Bot, store: Store, payments: PaymentService, provisioning: ProvisioningService, backups: BackupService, updater: UpdateService | None = None) -> None:
        self.bot = bot
        self.store = store
        self.payments = payments
        self.provisioning = provisioning
        self.backups = backups
        self.updater = updater or UpdateService()
        self.router = Router()
        self._register_handlers()

    def _register_handlers(self) -> None:
        self.router.message.register(self.start, CommandStart())
        self.router.message.register(self.show_menu, Command("menu"))
        self.router.message.register(self.show_admin_panel_message, Command("admin"))
        self.router.message.register(self.receive_tariff_payload, TariffCreateState.waiting_payload)
        self.router.message.register(self.receive_tariff_edit_payload, TariffEditState.waiting_payload)
        self.router.message.register(self.receive_server_payload, ServerCreateState.waiting_payload)
        self.router.message.register(self.receive_server_agent_payload, ServerAgentState.waiting_payload)
        self.router.message.register(self.receive_server_billing_payload, ServerBillingState.waiting_payload)
        self.router.message.register(self.receive_server_agent_command, ServerCommandState.waiting_command)
        self.router.message.register(self.receive_text_body, TextEditState.waiting_body)
        self.router.message.register(self.receive_payment_config_payload, PaymentConfigState.waiting_payload)
        self.router.message.register(self.receive_referral_percent, ReferralEditState.waiting_percent)
        self.router.message.register(self.receive_trial_days, TrialEditState.waiting_days)
        self.router.message.register(self.receive_balance_amount, BalanceGrantState.waiting_amount)
        self.router.message.register(self.receive_manual_subscription_days, ManualSubscriptionState.waiting_days)
        self.router.message.register(self.receive_broadcast_text, BroadcastState.waiting_text)
        self.router.message.register(self.show_profile, F.text == PROFILE_LABEL)
        self.router.message.register(self.show_buy, F.text == BUY_LABEL)
        self.router.message.register(self.show_help, F.text == HELP_LABEL)
        self.router.message.register(self.show_referrals, F.text == REFERRAL_LABEL)
        self.router.message.register(self.show_trial, F.text == TRIAL_LABEL)
        self.router.message.register(self.show_admin_panel_message, F.text == ADMIN_LABEL)
        self.router.message.register(self.handle_successful_payment, F.successful_payment)
        self.router.pre_checkout_query.register(self.pre_checkout)
        self.router.callback_query.register(self.noop, F.data == "noop")
        self.router.callback_query.register(self.handle_nav_callbacks, F.data.startswith("nav:"))
        self.router.callback_query.register(self.handle_help_callbacks, F.data.startswith("help:"))
        self.router.callback_query.register(self.handle_qr_callbacks, F.data.startswith("qr:"))
        self.router.callback_query.register(self.handle_subscription_callbacks, F.data.startswith("sub:"))
        self.router.callback_query.register(self.handle_key_callbacks, F.data.startswith("key:"))
        self.router.callback_query.register(self.handle_trial_callbacks, F.data.startswith("trial:"))
        self.router.callback_query.register(self.handle_buy_callbacks, F.data.startswith("buy:"))
        self.router.callback_query.register(self.handle_admin_callbacks, F.data.startswith("adm:"))
        self.router.message.register(self.fallback_message)

    async def start(self, message: Message, command: CommandObject | None = None) -> None:
        referral_code = command.args.strip() if command and command.args else None
        user = await self.store.get_or_create_user(message.from_user, referral_code=referral_code)
        if await self._deny_blocked_message(message, user):
            return
        await self._send_inline_screen(message, await self._render_home_text(), await self._home_inline_markup(user.is_admin))

    async def show_menu(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        user = await self.store.get_or_create_user(message.from_user)
        if await self._deny_blocked_message(message, user):
            return
        await self._send_inline_screen(message, await self._render_home_text(), await self._home_inline_markup(user.is_admin))

    async def show_profile(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        user = await self.store.get_or_create_user(message.from_user)
        if await self._deny_blocked_message(message, user):
            return
        summary = await self.store.get_user_summary(message.from_user.id)
        if not summary:
            await message.answer("Не удалось загрузить профиль. Попробуйте ещё раз.")
            return
        reserve_url = await self._reserve_access_url(summary)
        await self._send_inline_screen(message, await self._render_profile(summary, reserve_url=reserve_url), await self._profile_markup(summary, page=1))

    async def show_buy(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        user = await self.store.get_or_create_user(message.from_user)
        if await self._deny_blocked_message(message, user):
            return
        tariffs = await self.store.list_tariffs(only_active=True)
        if not tariffs:
            await message.answer("Сейчас нет доступных тарифов. Загляните чуть позже.")
            return
        ui = await self._ui_snapshot()
        await self._send_inline_screen(message, await self._render_buy_text(tariffs), tariffs_keyboard(tariffs, labels=await self._user_button_labels(ui)))

    async def show_help(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        user = await self.store.get_or_create_user(message.from_user)
        if await self._deny_blocked_message(message, user):
            return
        await self._send_inline_screen(message, await self._render_help_text(), await self._help_markup(user.is_admin))

    async def show_referrals(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        user = await self.store.get_or_create_user(message.from_user)
        if await self._deny_blocked_message(message, user):
            return
        if not await self.store.get_toggle("section_referral", default=True):
            await message.answer("Реферальный раздел временно скрыт администратором.")
            return
        summary = await self.store.get_user_summary(message.from_user.id)
        if not summary:
            await message.answer("Не удалось загрузить реферальную статистику.")
            return
        await self._send_inline_screen(message, await self._render_referral_text(summary), await self._referral_markup(summary))

    async def show_trial(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        user = await self.store.get_or_create_user(message.from_user)
        if await self._deny_blocked_message(message, user):
            return
        if not await self.store.get_toggle("section_trial", default=True):
            await message.answer("Пробный доступ сейчас скрыт администратором.")
            return
        summary = await self.store.get_user_summary(message.from_user.id)
        if not summary:
            await message.answer("Не удалось загрузить пробный доступ.")
            return
        await self._send_inline_screen(message, await self._render_trial_text(summary), await self._trial_markup(summary))

    async def show_admin_panel_message(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        actor = await self._admin_actor(message.from_user)
        if not await self._assert_admin_message(message):
            return
        actor_role = self._admin_role_value(actor)
        await self._send_inline_screen(message, await self._admin_panel_text(actor_role), admin_panel_keyboard(actor_role))

    async def pre_checkout(self, query: PreCheckoutQuery) -> None:
        await query.answer(ok=True)

    async def handle_successful_payment(self, message: Message) -> None:
        payload = message.successful_payment.invoice_payload
        payment = await self.store.get_payment_by_payload(payload)
        if not payment:
            await message.answer("Платёж получен, но не найден во внутренней базе. Напишите в поддержку.")
            return
        subscription, vpn_keys, extended = await self.provisioning.activate_payment(payment.id)
        if not subscription or not vpn_keys:
            await message.answer("Платёж прошёл, но доступ пока не создался. Проверьте серверы 3x-ui и попробуйте позже.")
            return
        reserve_url = await self._reserve_access_url(getattr(subscription, 'user', None))
        await message.answer(
            await self._render_activation_result(subscription, vpn_keys, extended=extended, reserve_url=reserve_url),
            reply_markup=access_result_keyboard(self._subscription_action_rows([subscription], "profile"), subscription_url=build_subscription_url(subscription), reserve_url=reserve_url, reserve_qr_callback=(f"qr:reserve:{subscription.user_id}" if reserve_url and getattr(subscription, 'user_id', None) else None), labels=await self._user_button_labels()),
        )

    async def handle_nav_callbacks(self, callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        user = await self.store.get_or_create_user(callback.from_user)
        if await self._deny_blocked_callback(callback, user):
            return
        target = callback.data.split(":", maxsplit=1)[1]
        if target == "home":
            await self._safe_edit_message_text(callback.message, await self._render_home_text(), reply_markup=await self._home_inline_markup(user.is_admin))
            await self._safe_answer_callback(callback, )
            return
        if target == "profile" or target.startswith("profile:"):
            page = 1
            if ":" in target:
                try:
                    page = max(int(target.split(":", maxsplit=1)[1]), 1)
                except ValueError:
                    page = 1
            summary = await self.store.get_user_summary(callback.from_user.id)
            if not summary:
                await self._safe_answer_callback(callback, "Не удалось открыть профиль.", show_alert=True)
                return
            reserve_url = await self._reserve_access_url(summary)
            await self._safe_edit_message_text(callback.message, await self._render_profile(summary, reserve_url=reserve_url), reply_markup=await self._profile_markup(summary, page=page))
            await self._safe_answer_callback(callback, )
            return
        if target == "buy":
            tariffs = await self.store.list_tariffs(only_active=True)
            if not tariffs:
                await self._safe_answer_callback(callback, "Сейчас нет доступных тарифов.", show_alert=True)
                return
            ui = await self._ui_snapshot()
            await self._safe_edit_message_text(callback.message, await self._render_buy_text(tariffs), reply_markup=tariffs_keyboard(tariffs, labels=await self._user_button_labels(ui)))
            await self._safe_answer_callback(callback, )
            return
        if target == "help":
            await self._safe_edit_message_text(callback.message, await self._render_help_text(), reply_markup=await self._help_markup(user.is_admin))
            await self._safe_answer_callback(callback, )
            return
        if target == "referral":
            if not await self.store.get_toggle("section_referral", default=True):
                await self._safe_answer_callback(callback, "Реферальный раздел скрыт.", show_alert=True)
                return
            summary = await self.store.get_user_summary(callback.from_user.id)
            if not summary:
                await self._safe_answer_callback(callback, "Не удалось загрузить раздел.", show_alert=True)
                return
            await self._safe_edit_message_text(callback.message, await self._render_referral_text(summary), reply_markup=await self._referral_markup(summary))
            await self._safe_answer_callback(callback, )
            return
        if target == "trial":
            if not await self.store.get_toggle("section_trial", default=True):
                await self._safe_answer_callback(callback, "Пробный доступ скрыт.", show_alert=True)
                return
            summary = await self.store.get_user_summary(callback.from_user.id)
            if not summary:
                await self._safe_answer_callback(callback, "Не удалось загрузить раздел.", show_alert=True)
                return
            await self._safe_edit_message_text(callback.message, await self._render_trial_text(summary), reply_markup=await self._trial_markup(summary))
            await self._safe_answer_callback(callback, )
            return
        if target == "admin":
            actor = await self._admin_actor(callback.from_user)
            actor_role = self._admin_role_value(actor)
            if actor_role == 'user':
                await self._safe_answer_callback(callback, "??? ???????.", show_alert=True)
                return
            await self._safe_edit_message_text(callback.message, await self._admin_panel_text(actor_role), reply_markup=admin_panel_keyboard(actor_role))
            await self._safe_answer_callback(callback)
            return
        await self._safe_answer_callback(callback, )

    async def handle_help_callbacks(self, callback: CallbackQuery) -> None:
        user = await self.store.get_or_create_user(callback.from_user)
        if await self._deny_blocked_callback(callback, user):
            return
        parts = callback.data.split(':')
        target = parts[1] if len(parts) > 1 else 'devices'
        if target == 'devices':
            await self._safe_edit_message_text(
                callback.message,
                await self._render_device_guides_menu(),
                reply_markup=device_guides_menu_keyboard(labels=await self._user_button_labels()),
            )
            await self._safe_answer_callback(callback)
            return
        if target in {'ios', 'android', 'windows', 'macos'}:
            await self._safe_edit_message_text(
                callback.message,
                await self._render_device_guide(target),
                reply_markup=device_guide_keyboard(labels=await self._user_button_labels()),
            )
            await self._safe_answer_callback(callback)
            return
        await self._safe_answer_callback(callback)

    async def handle_qr_callbacks(self, callback: CallbackQuery) -> None:
        user = await self.store.get_or_create_user(callback.from_user)
        if await self._deny_blocked_callback(callback, user):
            return
        parts = callback.data.split(':')
        if len(parts) < 3:
            await self._safe_answer_callback(callback)
            return
        kind = parts[1]
        chat_id = callback.message.chat.id if callback.message else callback.from_user.id
        await self._safe_answer_callback(callback, 'Готовлю QR...')
        try:
            if kind == 'sub':
                subscription_id = int(parts[2])
                subscription = await self.store.get_subscription_details(subscription_id)
                if not subscription or not subscription.user:
                    raise ValueError('Подписка не найдена.')
                if callback.from_user.id not in settings.admin_ids and subscription.user.telegram_id != callback.from_user.id:
                    raise ValueError('Нет доступа к этой подписке.')
                payload = build_subscription_url(subscription)
                if not payload:
                    raise ValueError('Общая ссылка подписки пока не сформировалась.')
                await self._send_qr_image(chat_id, payload, f'subscription_{subscription.id}.png', f'?? QR ????? ??????\n{self._subscription_title(subscription)}')
                return
            if kind == 'key':
                key_id = int(parts[2])
                key = await self.store.get_key_details(key_id)
                if not key or not key.subscription or not key.subscription.user:
                    raise ValueError('Ключ не найден.')
                if callback.from_user.id not in settings.admin_ids and key.subscription.user.telegram_id != callback.from_user.id:
                    raise ValueError('Нет доступа к этому ключу.')
                payload = (key.access_url or '').strip()
                if not payload or payload.startswith('legacy-import://'):
                    raise ValueError('Для этого ключа QR пока недоступен.')
                await self._send_qr_image(chat_id, payload, f'key_{key.id}.png', f'?? QR ?????????? ?????\n{self._key_server_name(key)}')
                return
            if kind == 'reserve':
                target_user_id = int(parts[2])
                target_user = await self.store.get_user_admin_summary(target_user_id)
                if not target_user:
                    raise ValueError('Пользователь не найден.')
                if callback.from_user.id not in settings.admin_ids and target_user.telegram_id != callback.from_user.id:
                    raise ValueError('Нет доступа к резервной ссылке.')
                payload = await self._reserve_access_url(target_user)
                if not payload:
                    raise ValueError('Резервный доступ сейчас скрыт или не настроен.')
                await self._send_qr_image(chat_id, payload, f'reserve_{target_user.id}.png', '?? QR ?????????? ????????\n????????? ??? ???????, ????? ?? ???????? ?????? ??? Telegram.')
                return
            await self.bot.send_message(chat_id, '⚠️ Неизвестный тип QR-запроса.')
        except ValueError as exc:
            await self.bot.send_message(chat_id, f'⚠️ {exc}')
        except QRCodeUnavailableError as exc:
            await self.bot.send_message(chat_id, f'?? {exc}\n\n????? ???????? QR-????, ???????? ???????????: python -m pip install .')

    async def handle_subscription_callbacks(self, callback: CallbackQuery) -> None:
        user = await self.store.get_or_create_user(callback.from_user)
        if await self._deny_blocked_callback(callback, user):
            return
        parts = callback.data.split(':')
        if len(parts) < 3 or parts[1] != 'show':
            await self._safe_answer_callback(callback)
            return
        try:
            subscription_id = int(parts[2])
        except ValueError:
            await self._safe_answer_callback(callback)
            return
        context = ':'.join(parts[3:]) or 'profile'
        subscription = await self.store.get_subscription_details(subscription_id)
        if not subscription or not subscription.user:
            await self._safe_answer_callback(callback, 'Подписка не найдена.', show_alert=True)
            return
        if callback.from_user.id not in settings.admin_ids and subscription.user.telegram_id != callback.from_user.id:
            await self._safe_answer_callback(callback, 'Нет доступа к этой подписке.', show_alert=True)
            return
        reserve_url = await self._reserve_access_url(getattr(subscription, 'user', None))
        await self._safe_edit_message_text(
            callback.message,
            await self._render_subscription_view(subscription, reserve_url=reserve_url),
            reply_markup=await self._subscription_detail_markup(subscription, context, callback.from_user.id, reserve_url=reserve_url),
        )
        await self._safe_answer_callback(callback)

    async def handle_key_callbacks(self, callback: CallbackQuery) -> None:
        user = await self.store.get_or_create_user(callback.from_user)
        if await self._deny_blocked_callback(callback, user):
            return
        parts = callback.data.split(':')
        if len(parts) < 4:
            await self._safe_answer_callback(callback)
            return
        action = parts[1]
        try:
            key_id = int(parts[2])
            subscription_id = int(parts[3])
        except ValueError:
            await self._safe_answer_callback(callback)
            return
        context = ':'.join(parts[4:]) or 'profile'
        key = await self.store.get_key_details(key_id)
        if not key or not key.subscription or not key.subscription.user:
            await self._safe_answer_callback(callback, 'Ключ не найден.', show_alert=True)
            return
        owner_id = key.subscription.user.telegram_id
        if callback.from_user.id not in settings.admin_ids and owner_id != callback.from_user.id:
            await self._safe_answer_callback(callback, 'Нет доступа к этому ключу.', show_alert=True)
            return
        if action == 'show':
            await self._safe_edit_message_text(
                callback.message,
                await self._render_key_view(key),
                reply_markup=await self._key_detail_markup(key, subscription_id, context, callback.from_user.id),
            )
            await self._safe_answer_callback(callback)
            return
        if action == 'replace':
            await self._safe_answer_callback(callback, 'Переиздаю ключ...')
            updated_key, subscription, error = await self.provisioning.replace_key(key_id)
            if error or not updated_key or not subscription:
                await self._safe_edit_message_text(
                    callback.message,
                    await self._render_key_view(key, notice=f'⚠️ {error or "Не удалось заменить ключ."}'),
                    reply_markup=await self._key_detail_markup(key, subscription_id, context, callback.from_user.id),
                )
                return
            await self._safe_edit_message_text(
                callback.message,
                await self._render_key_view(updated_key, notice='♻️ Ключ перевыпущен. Старую ссылку больше не используйте.'),
                reply_markup=await self._key_detail_markup(updated_key, subscription.id, context, callback.from_user.id),
            )
            return
        if action == 'delete':
            await self._safe_answer_callback(callback, 'Удаляю ключ...')
            subscription, error = await self.provisioning.delete_expired_key(key_id)
            if error:
                await self._safe_edit_message_text(
                    callback.message,
                    await self._render_key_view(key, notice=f'⚠️ {error}'),
                    reply_markup=await self._key_detail_markup(key, subscription_id, context, callback.from_user.id),
                )
                return
            if not subscription:
                await self._safe_edit_message_text(
                    callback.message,
                    '🗑️ Ключ удалён. Подписка больше недоступна.',
                    reply_markup=back_keyboard(self._subscription_back_callback(context), labels=await self._user_button_labels()),
                )
                return
            reserve_url = await self._reserve_access_url(getattr(subscription, 'user', None))
            await self._safe_edit_message_text(
                callback.message,
                await self._render_subscription_view(subscription, notice='🗑️ Истёкший ключ удалён из подписки.', reserve_url=reserve_url),
                reply_markup=await self._subscription_detail_markup(subscription, context, callback.from_user.id, reserve_url=reserve_url),
            )
            return
        await self._safe_answer_callback(callback)

    async def handle_trial_callbacks(self, callback: CallbackQuery) -> None:
        user = await self.store.get_or_create_user(callback.from_user)
        if await self._deny_blocked_callback(callback, user):
            return
        if callback.data != 'trial:activate':
            await self._safe_answer_callback(callback)
            return
        await self._safe_answer_callback(callback, 'Готовлю пробный доступ...')
        subscription, vpn_keys, error = await self.provisioning.grant_trial(callback.from_user.id)
        if error:
            await self._safe_edit_message_text(callback.message, f'?? {error}\n\n?????????? ????? ??? ????????? ?????.', reply_markup=back_keyboard('nav:trial', labels=await self._user_button_labels()))
            return
        reserve_url = await self._reserve_access_url(getattr(subscription, 'user', None))
        await self._safe_edit_message_text(callback.message, await self._render_activation_result(subscription, vpn_keys, is_trial=True, reserve_url=reserve_url), reply_markup=access_result_keyboard(self._subscription_action_rows([subscription], 'profile'), subscription_url=build_subscription_url(subscription), reserve_url=reserve_url, reserve_qr_callback=(f"qr:reserve:{subscription.user_id}" if reserve_url and getattr(subscription, 'user_id', None) else None), labels=await self._user_button_labels()))

    async def handle_buy_callbacks(self, callback: CallbackQuery) -> None:
        user = await self.store.get_or_create_user(callback.from_user)
        if await self._deny_blocked_callback(callback, user):
            return
        if callback.data == 'buy:back':
            tariffs = await self.store.list_tariffs(only_active=True)
            if tariffs:
                ui = await self._ui_snapshot()
                await self._safe_edit_message_text(callback.message, await self._render_buy_text(tariffs), reply_markup=tariffs_keyboard(tariffs, labels=await self._user_button_labels(ui)))
            await self._safe_answer_callback(callback)
            return
        parts = callback.data.split(':')
        if len(parts) < 3:
            await self._safe_answer_callback(callback)
            return
        if parts[1] == 'extend':
            subscription = await self.store.get_subscription_details(int(parts[2]))
            if not subscription or not subscription.user or subscription.user.telegram_id != callback.from_user.id:
                await self._safe_answer_callback(callback, 'Подписка для продления не найдена.', show_alert=True)
                return
            if not self._can_extend_subscription(subscription):
                await self._safe_answer_callback(callback, 'Продлить можно только активную платную подписку.', show_alert=True)
                return
            tariffs = await self.store.list_tariffs(only_active=True)
            text = '\n'.join([
                '🕒 Продление подписки',
                '',
                f'Текущий тариф: {self._subscription_title(subscription)}',
                f'Действует до: {subscription.ends_at:%d.%m.%Y %H:%M}',
                '',
                'Выберите тариф ниже. После оплаты бот продлит срок и обновит все рабочие ключи внутри этой подписки.',
            ])
            await self._safe_edit_message_text(
                callback.message,
                text,
                reply_markup=tariffs_keyboard(
                    tariffs,
                    extend_subscription_id=subscription.id,
                    back_callback=f'sub:show:{subscription.id}:profile',
                    labels=await self._user_button_labels(),
                ),
            )
            await self._safe_answer_callback(callback)
            return
        if parts[1] == 'tariff':
            tariff = await self.store.get_tariff(int(parts[2]))
            all_tariffs = await self.store.list_tariffs(only_active=True)
            extend_subscription_id = int(parts[4]) if len(parts) >= 5 and parts[3] == 'extend' else None
            target_subscription = None
            if extend_subscription_id:
                target_subscription = await self.store.get_subscription_details(extend_subscription_id)
                if not target_subscription or not target_subscription.user or target_subscription.user.telegram_id != callback.from_user.id:
                    await self._safe_answer_callback(callback, 'Подписка для продления не найдена.', show_alert=True)
                    return
            current_user = await self.store.get_user_by_telegram_id(callback.from_user.id)
            methods = await self._payment_methods_for_user(current_user, tariff)
            if not tariff or not methods:
                await self._safe_answer_callback(callback, 'Для этого тарифа сейчас нет доступных способов оплаты.', show_alert=True)
                return
            balance_line = [f'💰 Баланс аккаунта: {format_money(current_user.balance)}'] if current_user else []
            upsell_lines = self._tariff_upsell_lines(tariff, all_tariffs)
            if target_subscription:
                body = [
                    '🕒 Продление подписки',
                    '',
                    f'Тариф: {tariff.name}',
                    f'Срок продления: {tariff.days} дн.',
                    f'Цена в рублях: {format_money(tariff.price_rub)}',
                    f'Цена в Stars: {format_money(tariff.price_stars, "XTR")}',
                    *balance_line,
                    'После оплаты срок текущей подписки увеличится, а активные ключи будут синхронизированы автоматически.',
                ]
            else:
                body = [
                    f'🛒 {tariff.name}',
                    '',
                    f'Срок доступа: {tariff.days} дн.',
                    f'Цена в рублях: {format_money(tariff.price_rub)}',
                    f'Цена в Stars: {format_money(tariff.price_stars, "XTR")}',
                    *balance_line,
                    'После оплаты бот создаст или продлит доступ и обновит общую ссылку на все включённые серверы.',
                ]
                if tariff.description:
                    body.extend(['', tariff.description])
            if upsell_lines:
                body.extend(['', *upsell_lines])
            body.extend(['', 'Выберите способ оплаты ниже.'])
            back_callback = f'buy:extend:{extend_subscription_id}' if extend_subscription_id else 'buy:back'
            await self._safe_edit_message_text(callback.message, '\n'.join(body), reply_markup=payment_methods_keyboard(tariff.id, methods, extend_subscription_id=extend_subscription_id, back_callback=back_callback, labels=await self._user_button_labels()))
            await self._safe_answer_callback(callback)
            return
        if parts[1] == 'method' and len(parts) >= 4:
            method = parts[2]
            tariff_id = int(parts[3])
            extend_subscription_id = int(parts[5]) if len(parts) >= 6 and parts[4] == 'extend' else None
            target_subscription = None
            if extend_subscription_id:
                target_subscription = await self.store.get_subscription_details(extend_subscription_id)
                if not target_subscription or not target_subscription.user or target_subscription.user.telegram_id != callback.from_user.id:
                    await self._safe_answer_callback(callback, 'Подписка для продления не найдена.', show_alert=True)
                    return
            current_user = await self.store.get_user_by_telegram_id(callback.from_user.id)
            tariff = await self.store.get_tariff(tariff_id)
            if not current_user or not tariff:
                await self._safe_answer_callback(callback, 'Пользователь или тариф не найдены.', show_alert=True)
                return
            extend_tag = f'-ext{extend_subscription_id}' if extend_subscription_id else ''
            payload = f'pay-{method}-{current_user.id}-{tariff.id}{extend_tag}-{uuid4().hex}'
            is_extension = target_subscription is not None
            if method == 'stars':
                if tariff.price_stars <= 0:
                    await self._safe_answer_callback(callback, 'Для этого тарифа оплата Stars недоступна.', show_alert=True)
                    return
                payment = await self.store.create_payment(current_user.id, tariff.id, method, Decimal(tariff.price_stars), 'XTR', payload)
                title = f'Продление {tariff.name}' if is_extension else f'MyAir {tariff.name}'
                description = f'Продление подписки на {tariff.days} дн.' if is_extension else f'Доступ на {tariff.days} дн.'
                await self._safe_answer_callback(callback, 'Открываю счёт Telegram Stars...')
                await self.bot.send_invoice(chat_id=callback.from_user.id, title=title, description=description, payload=payment.payload, currency='XTR', provider_token='', prices=[LabeledPrice(label=tariff.name, amount=tariff.price_stars)], start_parameter=f'tariff-{tariff.id}')
                builder = InlineKeyboardBuilder()
                labels = await self._user_button_labels()
                builder.row(InlineKeyboardButton(text=labels['nav_back'], callback_data=(f'buy:extend:{extend_subscription_id}' if is_extension else 'buy:back')))
                builder.row(InlineKeyboardButton(text=labels['nav_home'], callback_data='nav:home'))
                info_text = '⭐ Счёт на продление отправлен отдельным сообщением от Telegram.' if is_extension else '⭐ Счёт на оплату отправлен отдельным сообщением от Telegram.'
                await self._safe_edit_message_text(callback.message, info_text, reply_markup=builder.as_markup())
                return
            amount = Decimal(str(tariff.price_rub))
            if method == 'balance':
                description = f'Продление тарифа {tariff.name} с баланса аккаунта' if is_extension else f'Покупка тарифа {tariff.name} с баланса аккаунта'
                payment, error = await self.store.create_balance_payment(current_user.id, tariff.id, amount, payload, description)
                if error or not payment:
                    await self._safe_answer_callback(callback, error or 'Недостаточно средств на балансе.', show_alert=True)
                    return
                await self._safe_answer_callback(callback, 'Активирую доступ...')
                subscription, vpn_keys, extended = await self.provisioning.activate_payment(payment.id)
                if not subscription:
                    await self._safe_edit_message_text(callback.message, '⚠️ Оплата прошла, но доступ пока не создался. Проверьте доступность серверов 3x-ui и попробуйте чуть позже.', reply_markup=back_keyboard('nav:profile', labels=await self._user_button_labels()))
                    return
                reserve_url = await self._reserve_access_url(getattr(subscription, 'user', None))
                await self._safe_edit_message_text(callback.message, await self._render_activation_result(subscription, vpn_keys, extended=extended, reserve_url=reserve_url), reply_markup=access_result_keyboard(self._subscription_action_rows([subscription], 'profile'), subscription_url=build_subscription_url(subscription), reserve_url=reserve_url, reserve_qr_callback=(f"qr:reserve:{subscription.user_id}" if reserve_url and getattr(subscription, 'user_id', None) else None), labels=await self._user_button_labels()))
                return
            payment = await self.store.create_payment(current_user.id, tariff.id, method, amount, 'RUB', payload)
            await self._safe_answer_callback(callback, 'Готовлю платёжную ссылку...')
            try:
                invoice = await self.payments.create_invoice(payment.id)
            except PaymentGatewayError as exc:
                back = f'buy:tariff:{tariff.id}:extend:{extend_subscription_id}' if is_extension else f'buy:tariff:{tariff.id}'
                await self._safe_edit_message_text(callback.message, f'⚠️ {exc}', reply_markup=back_keyboard(back, labels=await self._user_button_labels()))
                return
            builder = InlineKeyboardBuilder()
            labels = await self._user_button_labels()
            builder.row(InlineKeyboardButton(text=labels['pay_open_invoice'], url=invoice.payment_url))
            builder.row(InlineKeyboardButton(text=labels['nav_back'], callback_data=(f'buy:extend:{extend_subscription_id}' if is_extension else 'buy:back')))
            builder.row(InlineKeyboardButton(text=labels['nav_home'], callback_data='nav:home'))
            text = '\n'.join([
                '💳 Счёт готов',
                '',
                f'Тариф: {tariff.name}',
                f'Способ оплаты: {self._payment_method_title(method)}',
                f'Сумма: {format_money(amount)}',
                '',
                'После подтверждения оплаты бот автоматически активирует доступ и пришлёт обновлённую ссылку.',
            ])
            await self._safe_edit_message_text(callback.message, text, reply_markup=builder.as_markup())
            return
        await self._safe_answer_callback(callback)
    async def handle_admin_callbacks(self, callback: CallbackQuery, state: FSMContext) -> None:
        actor = await self._admin_actor(callback.from_user)
        actor_role = self._admin_role_value(actor)
        if not await self._assert_admin_callback(callback):
            return
        parts = callback.data.split(":")
        section = parts[1]
        if not self._can_access_admin_section(actor_role, section):
            await self._safe_answer_callback(callback, '??? ??????? ? ????? ???????.', show_alert=True)
            return
        if section == "panel":
            await state.clear()
            await self._safe_edit_message_text(callback.message, await self._admin_panel_text(actor_role), reply_markup=admin_panel_keyboard(actor_role))
            await self._safe_answer_callback(callback)
            return
        if section == 'roles':
            admins = await self.store.list_admin_users()
            await self._safe_edit_message_text(callback.message, self._render_admin_roles(admins), reply_markup=self._roles_markup(admins))
            await self._safe_answer_callback(callback)
            return
        if section == 'role' and len(parts) > 3 and parts[2] == 'view':
            target_user = await self.store.get_user_admin_summary(int(parts[3]))
            if not target_user:
                await self._safe_answer_callback(callback, '???????????? ?? ??????.', show_alert=True)
                return
            await self._safe_edit_message_text(callback.message, self._render_role_card(target_user), reply_markup=self._role_card_markup(target_user, actor_role))
            await self._safe_answer_callback(callback)
            return
        if section == 'role' and len(parts) > 4 and parts[2] == 'set':
            target_user = await self.store.get_user_admin_summary(int(parts[3]))
            role = parts[4]
            if not target_user:
                await self._safe_answer_callback(callback, '???????????? ?? ??????.', show_alert=True)
                return
            if not self._can_edit_role(actor_role, target_user):
                await self._safe_answer_callback(callback, '??? ???? ?????? ????.', show_alert=True)
                return
            updated = await self.store.set_user_admin_role(target_user.id, role)
            if not updated:
                await self._safe_answer_callback(callback, '?? ??????? ???????? ????.', show_alert=True)
                return
            await self._log_admin_action(actor, action='role_set', description=f'???????? ???? ?? {self._admin_role_title(role)}', target_user_id=updated.id, details={'role': role})
            await self._safe_edit_message_text(callback.message, self._render_role_card(updated), reply_markup=self._role_card_markup(updated, actor_role))
            await self._safe_answer_callback(callback, '???? ?????????')
            return
        if section == 'audit':
            logs = await self.store.list_admin_action_logs(limit=30)
            await self._safe_edit_message_text(callback.message, self._render_admin_audit(logs), reply_markup=self._audit_markup())
            await self._safe_answer_callback(callback)
            return
        if section == 'user' and len(parts) > 5 and parts[2] == 'diag':
            user_id = int(parts[3])
            filter_key = parts[4]
            page = int(parts[5])
            admin_user = await self.store.get_user_admin_summary(user_id)
            if not admin_user:
                await self._safe_answer_callback(callback, '???????????? ?? ??????.', show_alert=True)
                return
            failures = await self.store.list_provisioning_failures(limit=20, user_telegram_id=admin_user.telegram_id)
            await self._safe_edit_message_text(callback.message, self._render_user_diagnostics(admin_user, failures), reply_markup=self._user_diagnostics_markup(user_id, filter_key, page))
            await self._safe_answer_callback(callback)
            return
        if section == 'user' and len(parts) > 5 and parts[2] == 'role':
            user_id = int(parts[3])
            filter_key = parts[4]
            page = int(parts[5])
            target_user = await self.store.get_user_admin_summary(user_id)
            if not target_user:
                await self._safe_answer_callback(callback, '???????????? ?? ??????.', show_alert=True)
                return
            await self._safe_edit_message_text(callback.message, self._render_role_card(target_user), reply_markup=self._role_card_markup(target_user, actor_role, back_callback=f'adm:user:{user_id}:{filter_key}:{page}'))
            await self._safe_answer_callback(callback)
            return
        if section == 'server' and len(parts) > 3 and parts[2] == 'failures':
            server_id = int(parts[3])
            server = await self._load_server_for_admin(server_id)
            if not server:
                await self._safe_answer_callback(callback, '?????? ?? ??????.', show_alert=True)
                return
            failures = await self.store.list_provisioning_failures(limit=25, server_id=server_id)
            await self._safe_edit_message_text(callback.message, self._render_server_failures(server, failures), reply_markup=self._server_failures_markup(server_id))
            await self._safe_answer_callback(callback)
            return
        if section == "finance":
            if len(parts) > 2 and parts[2] in {"csv", "xls"}:
                export_kind = parts[2]
                await self._safe_answer_callback(callback, f"Готовлю {'CSV' if export_kind == 'csv' else 'Excel'}...")
                analytics = await self.store.get_analytics_snapshot()
                await self._send_analytics_export(callback, analytics, export_kind)
                return
            analytics = await self.store.get_analytics_snapshot()
            await self._safe_edit_message_text(callback.message, self._render_finance_admin(analytics), reply_markup=finance_keyboard())
            await self._safe_answer_callback(callback)
            return
        if section == "analytics":
            if len(parts) > 2 and parts[2] in {"csv", "xls"}:
                export_kind = parts[2]
                await self._safe_answer_callback(callback, f"Готовлю {'CSV' if export_kind == 'csv' else 'Excel'}...")
                analytics = await self.store.get_analytics_snapshot()
                await self._send_analytics_export(callback, analytics, export_kind)
                return
            analytics = await self.store.get_analytics_snapshot()
            await self._safe_edit_message_text(callback.message, self._render_analytics_admin(analytics), reply_markup=analytics_keyboard())
            await self._safe_answer_callback(callback)
            return
        if section == "guide":
            section_key = parts[2] if len(parts) > 2 else "start"
            await self._safe_edit_message_text(callback.message, self._render_admin_guide(section_key), reply_markup=admin_guide_keyboard(section_key))
            await self._safe_answer_callback(callback)
            return
        if section == "reserve":
            visible = await self.store.get_toggle("section_reserve_access", default=True)
            await self._safe_edit_message_text(callback.message, self._render_reserve_admin(visible), reply_markup=reserve_admin_keyboard(visible))
            await self._safe_answer_callback(callback)
            return
        if section == "tariffs":
            tariffs = await self.store.list_tariffs(only_active=False)
            await self._safe_edit_message_text(callback.message, self._render_tariffs_admin(tariffs), reply_markup=tariffs_admin_keyboard(tariffs))
            await self._safe_answer_callback(callback)
            return
        if section == "tariff" and len(parts) > 2 and parts[2] == "add":
            await state.set_state(TariffCreateState.waiting_payload)
            await state.update_data(source_chat_id=callback.message.chat.id, source_message_id=callback.message.message_id)
            await self._safe_edit_message_text(callback.message, "📦 Новый тариф\n\nОтправьте данные в формате:\nНазвание|дни|цена RUB|цена Stars|описание", reply_markup=back_keyboard("adm:tariffs"))
            await self._safe_answer_callback(callback)
            return
        if section == "tariff" and len(parts) > 3 and parts[2] == "view":
            tariff = await self.store.get_tariff(int(parts[3]))
            if not tariff:
                await self._safe_answer_callback(callback, "Тариф не найден.", show_alert=True)
                return
            await self._safe_edit_message_text(callback.message, self._render_tariff_card(tariff), reply_markup=tariff_detail_keyboard(tariff.id, tariff.is_active))
            await self._safe_answer_callback(callback)
            return
        if section == "tariff" and len(parts) > 3 and parts[2] == "edit":
            tariff = await self.store.get_tariff(int(parts[3]))
            if not tariff:
                await self._safe_answer_callback(callback, "Тариф не найден.", show_alert=True)
                return
            await state.set_state(TariffEditState.waiting_payload)
            await state.update_data(tariff_id=tariff.id, source_chat_id=callback.message.chat.id, source_message_id=callback.message.message_id)
            await self._safe_edit_message_text(callback.message, f"📦 Редактирование тарифа\n\nТекущие данные:\n{tariff.name}|{tariff.days}|{tariff.price_rub}|{tariff.price_stars}|{tariff.description}\n\nОтправьте новые данные в этом же формате.", reply_markup=back_keyboard(f"adm:tariff:view:{tariff.id}"))
            await self._safe_answer_callback(callback)
            return
        if section == "tariff" and len(parts) > 3 and parts[2] == "toggle":
            tariff = await self.store.toggle_tariff(int(parts[3]))
            if not tariff:
                await self._safe_answer_callback(callback, "Тариф не найден.", show_alert=True)
                return
            await self._safe_edit_message_text(callback.message, self._render_tariff_card(tariff), reply_markup=tariff_detail_keyboard(tariff.id, tariff.is_active))
            await self._safe_answer_callback(callback, "Статус тарифа обновлён")
            return
        if section == "tariff" and len(parts) > 3 and parts[2] == "delete":
            deleted, result_text = await self.store.delete_tariff(int(parts[3]))
            if not deleted:
                await self._safe_answer_callback(callback, result_text, show_alert=True)
                return
            tariffs = await self.store.list_tariffs(only_active=False)
            await self._safe_edit_message_text(callback.message, self._render_tariffs_admin(tariffs), reply_markup=tariffs_admin_keyboard(tariffs))
            await self._safe_answer_callback(callback, result_text)
            return
        if section == "payments":
            toggles = [toggle for toggle in await self.store.list_toggles() if toggle.key.startswith("payment_")]
            payment_config = await self.store.get_payment_settings_snapshot()
            await self._safe_edit_message_text(callback.message, self._render_payments_admin(toggles, payment_config), reply_markup=toggles_keyboard(toggles, payment_config))
            await self._safe_answer_callback(callback)
            return
        if section == "paymentcfg" and len(parts) > 2:
            provider = parts[2]
            payment_config = await self.store.get_payment_settings_snapshot()
            if provider == "yookassa":
                await state.set_state(PaymentConfigState.waiting_payload)
                await state.update_data(provider=provider, source_chat_id=callback.message.chat.id, source_message_id=callback.message.message_id)
                prompt = "💳 Настройка YooKassa\n\nОтправьте данные в формате:\nshop_id|secret_key|return_url\n\nЧтобы отключить интеграцию, отправьте: off\n\nТекущий return_url: {0}".format(payment_config.get("yookassa_return_url") or settings.yookassa_return_url)
                await self._safe_edit_message_text(callback.message, prompt, reply_markup=back_keyboard("adm:payments"))
                await self._safe_answer_callback(callback)
                return
            if provider == "crypto":
                await state.set_state(PaymentConfigState.waiting_payload)
                await state.update_data(provider=provider, source_chat_id=callback.message.chat.id, source_message_id=callback.message.message_id)
                assets = ",".join(payment_config.get("crypto_pay_assets") or settings.crypto_assets)
                prompt = f"🪙 Настройка Crypto Pay\n\nОтправьте данные в формате:\ntoken|testnet(true/false)|USDT,TON,BTC\n\nЧтобы отключить интеграцию, отправьте: off\n\nТекущие assets: {assets}"
                await self._safe_edit_message_text(callback.message, prompt, reply_markup=back_keyboard("adm:payments"))
                await self._safe_answer_callback(callback)
                return
        if section == "toggle" and len(parts) > 2:
            key = parts[2]
            await self.store.toggle_feature(key)
            await self._safe_answer_callback(callback, "Настройка обновлена")
            if key.startswith("payment_"):
                toggles = [toggle for toggle in await self.store.list_toggles() if toggle.key.startswith("payment_")]
                payment_config = await self.store.get_payment_settings_snapshot()
                await self._safe_edit_message_text(callback.message, self._render_payments_admin(toggles, payment_config), reply_markup=toggles_keyboard(toggles, payment_config))
                return
            if key == "section_referral":
                percent = await self.store.get_int_setting("referral_percent", settings.referral_default_percent)
                visible = await self.store.get_toggle("section_referral", default=True)
                await self._safe_edit_message_text(callback.message, self._render_referral_admin(percent, visible), reply_markup=referral_admin_keyboard(visible))
                return
            if key == "section_trial":
                await self._safe_edit_message_text(callback.message, await self._render_trial_admin(), reply_markup=trial_admin_keyboard())
                return
            if key == "section_reserve_access":
                visible = await self.store.get_toggle("section_reserve_access", default=True)
                await self._safe_edit_message_text(callback.message, self._render_reserve_admin(visible), reply_markup=reserve_admin_keyboard(visible))
                return
            await self._safe_edit_message_text(callback.message, await self._admin_panel_text(actor_role), reply_markup=admin_panel_keyboard(actor_role))
            return
        if section == "users" and len(parts) > 2 and parts[2] == "filters":
            filter_counts = await self.store.get_user_filter_counts()
            users, total = await self.store.list_users(filter_key="all", page=1, page_size=8)
            await self._safe_edit_message_text(callback.message, self._render_users_list_text("all", total, 1, filter_counts), reply_markup=users_list_keyboard(users, "all", 1, total, 8, filter_counts))
            await self._safe_answer_callback(callback)
            return
        if section == "users" and len(parts) > 3:
            filter_key = parts[2]
            page = int(parts[3])
            filter_counts = await self.store.get_user_filter_counts()
            users, total = await self.store.list_users(filter_key=filter_key, page=page, page_size=8)
            await self._safe_edit_message_text(callback.message, self._render_users_list_text(filter_key, total, page, filter_counts), reply_markup=users_list_keyboard(users, filter_key, page, total, 8, filter_counts))
            await self._safe_answer_callback(callback)
            return
        if section == "user" and len(parts) == 5 and parts[2].isdigit():
            user_id = int(parts[2])
            filter_key = parts[3]
            page = int(parts[4])
            admin_user = await self.store.get_user_admin_summary(user_id)
            if not admin_user:
                await self._safe_answer_callback(callback, "???????????? ?? ??????.", show_alert=True)
                return
            await self._safe_edit_message_text(callback.message, self._render_admin_user(admin_user), reply_markup=self._admin_user_markup(admin_user, actor_role, filter_key, page))
            await self._safe_answer_callback(callback)
            return
        if section == "user" and len(parts) > 5:
            permissions = self._admin_user_action_permissions(actor_role)
            action = parts[2]
            user_id = int(parts[3])
            filter_key = parts[4]
            page = int(parts[5])
            if action == "balance":
                if not permissions['can_grant_balance']:
                    await self._safe_answer_callback(callback, "??? ??????? ? ?????????? ???????.", show_alert=True)
                    return
                await state.set_state(BalanceGrantState.waiting_amount)
                await state.update_data(user_id=user_id, filter_key=filter_key, page=page, source_chat_id=callback.message.chat.id, source_message_id=callback.message.message_id)
                await self._safe_edit_message_text(callback.message, "?? ?????? ???????\n\n????????? ?????, ??????? ????? ????????? ????????????.\n??????: 150 ??? 150.50", reply_markup=back_keyboard(f"adm:user:{user_id}:{filter_key}:{page}"))
                await self._safe_answer_callback(callback)
                return
            if action == "key":
                if not permissions['can_grant_access']:
                    await self._safe_answer_callback(callback, "??? ??????? ? ?????? ??????.", show_alert=True)
                    return
                if not await self.store.list_balanced_servers(trial_only=False):
                    await self._safe_answer_callback(callback, "??? ?????????? ???????? ??? ?????? ???????.", show_alert=True)
                    return
                await state.set_state(ManualSubscriptionState.waiting_days)
                await state.update_data(grant_user_id=user_id, filter_key=filter_key, page=page, source_chat_id=callback.message.chat.id, source_message_id=callback.message.message_id)
                await self._safe_edit_message_text(callback.message, "?? ?????? ???????\n\n????????? ???? ??????? ? ????.\n????? ????????????? ??????? ???????? ?? ????????? ???? ? ????????? ????????? ???????.", reply_markup=back_keyboard(f"adm:user:{user_id}:{filter_key}:{page}"))
                await self._safe_answer_callback(callback)
                return
            if action == "ops":
                operations = await self.store.list_user_operations(user_id, limit=20)
                await self._safe_edit_message_text(callback.message, self._render_operations_text(operations), reply_markup=user_operations_keyboard(user_id, filter_key, page))
                await self._safe_answer_callback(callback)
                return
            if action == "refs":
                referrals = await self.store.list_user_referrals(user_id)
                await self._safe_edit_message_text(callback.message, self._render_user_referrals_text(referrals), reply_markup=user_referrals_keyboard(user_id, filter_key, page))
                await self._safe_answer_callback(callback)
                return
            if action == "block":
                if not permissions['can_manage_block']:
                    await self._safe_answer_callback(callback, "??? ???? ?? ?????????? ????????????.", show_alert=True)
                    return
                admin_user = await self.store.get_user_admin_summary(user_id)
                if not admin_user:
                    await self._safe_answer_callback(callback, "???????????? ?? ??????.", show_alert=True)
                    return
                if self._admin_role_value(admin_user) != 'user':
                    await self._safe_answer_callback(callback, "????????????????? ???? ??????????? ??????.", show_alert=True)
                    return
                await self.store.toggle_user_blocked(user_id)
                admin_user = await self.store.get_user_admin_summary(user_id)
                await self._log_admin_action(actor, action='user_block_toggle', description='??????? ?????? ?????????? ????????????', target_user_id=user_id, details={'is_blocked': admin_user.is_blocked})
                await self._safe_edit_message_text(callback.message, self._render_admin_user(admin_user), reply_markup=self._admin_user_markup(admin_user, actor_role, filter_key, page))
                await self._safe_answer_callback(callback, "?????? ???????????? ????????")
                return

        if section == "texts":
            group = parts[2] if len(parts) > 2 and parts[2] in {"texts", "buttons"} else "texts"
            pages = await self.store.list_content_pages(group=group)
            await self._safe_edit_message_text(callback.message, self._render_texts_admin(group, pages), reply_markup=contents_keyboard(pages, group=group))
            await self._safe_answer_callback(callback, )
            return
        if section == "text" and len(parts) > 2:
            if len(parts) > 3:
                group = parts[2]
                key = parts[3]
            else:
                key = parts[2]
                group = "buttons" if key.startswith("button_") else "texts"
            page = await self.store.get_content(key)
            if not page:
                await self._safe_answer_callback(callback, "Раздел не найден.", show_alert=True)
                return
            await state.set_state(TextEditState.waiting_body)
            await state.update_data(content_key=key, content_group=group, source_chat_id=callback.message.chat.id, source_message_id=callback.message.message_id)
            await self._safe_edit_message_text(callback.message, self._render_content_edit_prompt(page, group), reply_markup=back_keyboard(f"adm:texts:{group}"))
            await self._safe_answer_callback(callback, )
            return
        if section == "servers":
            servers = await self._load_servers_for_admin()
            await self._safe_edit_message_text(callback.message, self._render_servers_overview(servers), reply_markup=servers_keyboard(servers))
            await self._safe_answer_callback(callback)
            return
        if section == "server" and len(parts) > 2:
            action = parts[2]
            if action == "add":
                await state.set_state(ServerCreateState.waiting_payload)
                await state.update_data(source_chat_id=callback.message.chat.id, source_message_id=callback.message.message_id)
                await self._safe_edit_message_text(callback.message, self._server_payload_hint(), reply_markup=back_keyboard("adm:servers"))
                await self._safe_answer_callback(callback)
                return
            if action == "refresh":
                await self._safe_answer_callback(callback, "Проверяю серверы...")
                await self.provisioning.refresh_servers()
                servers = await self._load_servers_for_admin()
                await self._safe_edit_message_text(callback.message, self._render_servers_overview(servers), reply_markup=servers_keyboard(servers))
                return
            if action == "usage":
                await self._safe_answer_callback(callback, "Обновляю трафик ключей...")
                await self.provisioning.refresh_key_usage()
                servers = await self._load_servers_for_admin()
                await self._safe_edit_message_text(callback.message, self._render_servers_overview(servers), reply_markup=servers_keyboard(servers))
                return
            if action == "refreshone" and len(parts) > 3:
                server_id = int(parts[3])
                await self._safe_answer_callback(callback, "Проверяю сервер...")
                await self.provisioning.refresh_server(server_id)
                server = await self._load_server_for_admin(server_id)
                if not server:
                    await self._safe_edit_message_text(callback.message, "Сервер не найден.", reply_markup=back_keyboard("adm:servers"))
                    return
                await self._safe_edit_message_text(callback.message, self._render_server(server), reply_markup=self._server_actions_markup(server))
                return
            if action == "view" and len(parts) > 3:
                server = await self._load_server_for_admin(int(parts[3]))
                if not server:
                    await self._safe_answer_callback(callback, "Сервер не найден.", show_alert=True)
                    return
                await self._safe_edit_message_text(callback.message, self._render_server(server), reply_markup=self._server_actions_markup(server))
                await self._safe_answer_callback(callback)
                return
            if action == "toggle" and len(parts) > 3:
                toggled = await self.store.toggle_server_enabled(int(parts[3]))
                if not toggled:
                    await self._safe_answer_callback(callback, "Сервер не найден.", show_alert=True)
                    return
                server = await self._load_server_for_admin(int(parts[3]))
                await self._safe_edit_message_text(callback.message, self._render_server(server), reply_markup=self._server_actions_markup(server))
                await self._safe_answer_callback(callback, "Статус сервера обновлён")
                return
            if action == "trial" and len(parts) > 3:
                toggled = await self.store.toggle_server_trial(int(parts[3]))
                if not toggled:
                    await self._safe_answer_callback(callback, "Сервер не найден.", show_alert=True)
                    return
                server = await self._load_server_for_admin(int(parts[3]))
                await self._safe_edit_message_text(callback.message, self._render_server(server), reply_markup=self._server_actions_markup(server))
                await self._safe_answer_callback(callback, "Настройки trial обновлены")
                return
            if action == "billingcfg" and len(parts) > 3:
                server_id = int(parts[3])
                server = await self._load_server_for_admin(server_id)
                if not server:
                    await self._safe_answer_callback(callback, "Сервер не найден.", show_alert=True)
                    return
                billing_cfg = await self.store.get_server_billing_config(server_id)
                await state.set_state(ServerBillingState.waiting_payload)
                await state.update_data(server_id=server_id, source_chat_id=callback.message.chat.id, source_message_id=callback.message.message_id)
                await self._safe_edit_message_text(callback.message, self._render_server_billing_prompt(server, billing_cfg), reply_markup=back_keyboard(f"adm:server:view:{server_id}"))
                await self._safe_answer_callback(callback)
                return
            if action == "billingpaid" and len(parts) > 3:
                server_id = int(parts[3])
                await self._safe_answer_callback(callback, "Сдвигаю дату следующей оплаты...")
                billing_cfg = await self.store.mark_server_billing_paid(server_id)
                server = await self._load_server_for_admin(server_id)
                if not server:
                    await self._safe_edit_message_text(callback.message, "Сервер не найден.", reply_markup=back_keyboard("adm:servers"))
                    return
                if not billing_cfg:
                    await self._safe_edit_message_text(callback.message, self._render_server(server, notice="⚠️ Сначала настройте сумму и дату оплаты для этого сервера."), reply_markup=self._server_actions_markup(server))
                    return
                next_due = billing_cfg.get("next_due")
                notice = f"✅ Оплата отмечена. Следующая дата: {next_due.strftime('%d.%m.%Y') if next_due else 'не задана'}."
                await self._safe_edit_message_text(callback.message, self._render_server(server, notice=notice), reply_markup=self._server_actions_markup(server))
                return

            if action == "agentcfg" and len(parts) > 3:
                server_id = int(parts[3])
                server = await self.store.get_server(server_id)
                if not server:
                    await self._safe_answer_callback(callback, "Сервер не найден.", show_alert=True)
                    return
                agent_cfg = await self.store.get_server_agent_config(server_id)
                await state.set_state(ServerAgentState.waiting_payload)
                await state.update_data(server_id=server_id, source_chat_id=callback.message.chat.id, source_message_id=callback.message.message_id)
                hint = self._render_server_agent_prompt(server, agent_cfg)
                await self._safe_edit_message_text(callback.message, hint, reply_markup=back_keyboard(f"adm:server:view:{server_id}"))
                await self._safe_answer_callback(callback)
                return
            if action == "agentclear" and len(parts) > 3:
                server_id = int(parts[3])
                await self.store.clear_server_agent_config(server_id)
                server = await self._load_server_for_admin(server_id)
                if not server:
                    await self._safe_edit_message_text(callback.message, "Сервер не найден.", reply_markup=back_keyboard("adm:servers"))
                    return
                await self._safe_edit_message_text(callback.message, self._render_server(server), reply_markup=self._server_actions_markup(server))
                await self._safe_answer_callback(callback, "Агент отключён")
                return
            if action == "agentstatus" and len(parts) > 3:
                server = await self._load_server_for_admin(int(parts[3]))
                if not server:
                    await self._safe_answer_callback(callback, "Сервер не найден.", show_alert=True)
                    return
                await self._safe_edit_message_text(callback.message, self._render_server(server), reply_markup=self._server_actions_markup(server))
                await self._safe_answer_callback(callback, "Статус агента обновлён")
                return
            if action == "agentcustom" and len(parts) > 3:
                server_id = int(parts[3])
                server = await self._load_server_for_admin(server_id)
                if not server:
                    await self._safe_answer_callback(callback, "Сервер не найден.", show_alert=True)
                    return
                await state.set_state(ServerCommandState.waiting_command)
                await state.update_data(server_id=server_id, source_chat_id=callback.message.chat.id, source_message_id=callback.message.message_id)
                await self._safe_edit_message_text(callback.message, self._render_server_command_prompt(server), reply_markup=back_keyboard(f"adm:server:view:{server_id}"))
                await self._safe_answer_callback(callback)
                return
            if action == "agentcmd" and len(parts) > 4:
                server_id = int(parts[3])
                command = parts[4]
                server = await self._load_server_for_admin(server_id)
                if not server:
                    await self._safe_answer_callback(callback, "Сервер не найден.", show_alert=True)
                    return
                await self._safe_answer_callback(callback, "Отправляю команду на сервер...")
                result_text = await self._run_server_agent_command(server, command)
                server = await self._load_server_for_admin(server_id)
                await self._safe_edit_message_text(callback.message, self._render_server(server, notice=result_text), reply_markup=self._server_actions_markup(server))
                return
            if action == "delete" and len(parts) > 3:
                deleted, result_text = await self.store.delete_server(int(parts[3]))
                if not deleted:
                    await self._safe_answer_callback(callback, result_text, show_alert=True)
                    return
                servers = await self._load_servers_for_admin()
                await self._safe_edit_message_text(callback.message, self._render_servers_overview(servers), reply_markup=servers_keyboard(servers))
                await self._safe_answer_callback(callback, result_text)
                return
        if section == "referral" and len(parts) > 2 and parts[2] == "edit":
            await state.set_state(ReferralEditState.waiting_percent)
            await state.update_data(source_chat_id=callback.message.chat.id, source_message_id=callback.message.message_id)
            await self._safe_edit_message_text(callback.message, "🎁 Реферальная программа\n\nОтправьте новый процент вознаграждения.\nПример: 10", reply_markup=back_keyboard("adm:referral"))
            await self._safe_answer_callback(callback, )
            return
        if section == "referral":
            percent = await self.store.get_int_setting("referral_percent", settings.referral_default_percent)
            visible = await self.store.get_toggle("section_referral", default=True)
            await self._safe_edit_message_text(callback.message, self._render_referral_admin(percent, visible), reply_markup=referral_admin_keyboard(visible))
            await self._safe_answer_callback(callback, )
            return
        if section == "broadcast":
            if len(parts) == 2:
                await self._safe_edit_message_text(callback.message, self._render_broadcast_intro(), reply_markup=broadcast_filters_keyboard())
                await self._safe_answer_callback(callback, )
                return
            filter_key = parts[2]
            await state.set_state(BroadcastState.waiting_text)
            await state.update_data(filter_key=filter_key, source_chat_id=callback.message.chat.id, source_message_id=callback.message.message_id)
            await self._safe_edit_message_text(callback.message, f"📣 Рассылка\n\nОтправьте текст рассылки для аудитории: {self._filter_title(filter_key)}.", reply_markup=back_keyboard("adm:broadcast"))
            await self._safe_answer_callback(callback, )
            return
        if section == "trial" and len(parts) > 2 and parts[2] == "edit":
            await state.set_state(TrialEditState.waiting_days)
            await state.update_data(source_chat_id=callback.message.chat.id, source_message_id=callback.message.message_id)
            await self._safe_edit_message_text(callback.message, "🧪 Настройки trial\n\nОтправьте новый срок пробного доступа в днях.", reply_markup=back_keyboard("adm:trial"))
            await self._safe_answer_callback(callback, )
            return
        if section == "trial":
            await self._safe_edit_message_text(callback.message, await self._render_trial_admin(), reply_markup=trial_admin_keyboard())
            await self._safe_answer_callback(callback, )
            return
        if section == "backup" and len(parts) > 2 and parts[2] == "run":
            archive = await self.backups.send_backup_to_admins(self.bot)
            await self._safe_edit_message_text(callback.message, f"🗂️ Backup\n\nАрхив создан: {archive.name}\nФайл уже отправлен всем администраторам.", reply_markup=backup_keyboard())
            await self._safe_answer_callback(callback, "Backup отправлен")
            return
        if section == "backup":
            await self._safe_edit_message_text(callback.message, "🗂️ Backup\n\nЕжедневный архив отправляется администраторам автоматически. Здесь можно запустить его вручную.", reply_markup=backup_keyboard())
            await self._safe_answer_callback(callback, )
            return
        if section == "updates" and len(parts) > 2 and parts[2] == "run":
            await self._safe_answer_callback(callback, "Запрос на обновление отправлен...")
            status = await self.updater.get_status()
            try:
                result_text = await self.updater.trigger_update()
            except Exception as exc:
                await self._safe_edit_message_text(callback.message, self._render_updates_admin(status, error_text=str(exc)), reply_markup=updates_admin_keyboard(status.trigger_configured, status.update_available))
                return
            await self._safe_edit_message_text(callback.message, self._render_updates_admin(status, result_text=result_text), reply_markup=updates_admin_keyboard(status.trigger_configured, status.update_available))
            return
        if section == "updates":
            status = await self.updater.get_status()
            await self._safe_edit_message_text(callback.message, self._render_updates_admin(status), reply_markup=updates_admin_keyboard(status.trigger_configured, status.update_available))
            await self._safe_answer_callback(callback, )
            return
        await self._safe_answer_callback(callback, )

    async def receive_tariff_payload(self, message: Message, state: FSMContext) -> None:
        if not await self._assert_admin_message(message):
            return
        parts = [chunk.strip() for chunk in (message.text or "").split("|", 4)]
        data = await state.get_data()
        chat_id = data.get("source_chat_id")
        message_id = data.get("source_message_id")
        if len(parts) < 5:
            await self._safe_edit_message_by_id(chat_id, message_id, "📦 Новый тариф\n\nНеверный формат.\nИспользуйте:\nНазвание|дни|цена RUB|цена Stars|описание", reply_markup=back_keyboard("adm:tariffs"))
            return
        try:
            days = int(parts[1])
            price_rub = Decimal(parts[2])
            price_stars = int(parts[3])
        except (ValueError, InvalidOperation):
            await self._safe_edit_message_by_id(chat_id, message_id, "📦 Новый тариф\n\nНе удалось разобрать числа. Проверьте срок и цены.", reply_markup=back_keyboard("adm:tariffs"))
            return
        await self.store.create_tariff(parts[0], days, price_rub, price_stars, parts[4])
        tariffs = await self.store.list_tariffs(only_active=False)
        await self._safe_edit_message_by_id(chat_id, message_id, self._render_tariffs_admin(tariffs), reply_markup=tariffs_admin_keyboard(tariffs))
        await state.clear()

    async def receive_tariff_edit_payload(self, message: Message, state: FSMContext) -> None:
        if not await self._assert_admin_message(message):
            return
        parts = [chunk.strip() for chunk in (message.text or "").split("|", 4)]
        data = await state.get_data()
        chat_id = data.get("source_chat_id")
        message_id = data.get("source_message_id")
        tariff_id = data.get("tariff_id")
        if not tariff_id:
            await self._safe_edit_message_by_id(chat_id, message_id, "Тариф для редактирования не найден.", reply_markup=back_keyboard("adm:tariffs"))
            return
        if len(parts) < 5:
            await self._safe_edit_message_by_id(chat_id, message_id, "📦 Редактирование тарифа\n\nНеверный формат.\nИспользуйте:\nНазвание|дни|цена RUB|цена Stars|описание", reply_markup=back_keyboard(f"adm:tariff:view:{tariff_id}"))
            return
        try:
            days = int(parts[1])
            price_rub = Decimal(parts[2])
            price_stars = int(parts[3])
        except (ValueError, InvalidOperation):
            await self._safe_edit_message_by_id(chat_id, message_id, "📦 Редактирование тарифа\n\nНе удалось разобрать числа. Проверьте срок и цены.", reply_markup=back_keyboard(f"adm:tariff:view:{tariff_id}"))
            return
        tariff = await self.store.update_tariff(int(tariff_id), parts[0], days, price_rub, price_stars, parts[4])
        if not tariff:
            await self._safe_edit_message_by_id(chat_id, message_id, "Тариф не найден.", reply_markup=back_keyboard("adm:tariffs"))
            return
        await self._safe_edit_message_by_id(chat_id, message_id, self._render_tariff_card(tariff), reply_markup=tariff_detail_keyboard(tariff.id, tariff.is_active))
        await state.clear()

    async def receive_server_payload(self, message: Message, state: FSMContext) -> None:
        if not await self._assert_admin_message(message):
            return
        parts = [chunk.strip() for chunk in (message.text or "").split("|", 4)]
        data = await state.get_data()
        chat_id = data.get("source_chat_id")
        message_id = data.get("source_message_id")
        if len(parts) < 5:
            await self._safe_edit_message_by_id(chat_id, message_id, self._server_payload_hint(), reply_markup=back_keyboard("adm:servers"))
            return
        try:
            inbound_id = int(parts[4])
        except ValueError:
            await self._safe_edit_message_by_id(chat_id, message_id, "inbound_id должен быть числом. Возьмите его из раздела Inbounds в 3x-ui.", reply_markup=back_keyboard("adm:servers"))
            return
        await self.store.create_server(parts[0], parts[1], parts[2], parts[3], inbound_id)
        servers = await self._load_servers_for_admin()
        await self._safe_edit_message_by_id(chat_id, message_id, self._render_servers_overview(servers), reply_markup=servers_keyboard(servers))
        await state.clear()

    async def receive_server_agent_payload(self, message: Message, state: FSMContext) -> None:
        if not await self._assert_admin_message(message):
            return
        data = await state.get_data()
        chat_id = data.get("source_chat_id")
        message_id = data.get("source_message_id")
        server_id = data.get("server_id")
        if not server_id:
            await self._safe_edit_message_by_id(chat_id, message_id, "Сервер для agent-настройки не найден.", reply_markup=back_keyboard("adm:servers"))
            return
        payload = (message.text or "").strip()
        if payload.lower() == "off":
            await self.store.clear_server_agent_config(int(server_id))
        else:
            parts = [chunk.strip() for chunk in payload.split("|", 1)]
            if len(parts) < 2 or not parts[0] or not parts[1]:
                await self._safe_edit_message_by_id(chat_id, message_id, "🤖 Настройка Ubuntu-agent\n\nФормат: https://host:8799|TOKEN\nИли отправьте off, чтобы отключить агент.", reply_markup=back_keyboard(f"adm:server:view:{server_id}"))
                return
            await self.store.set_server_agent_config(int(server_id), parts[0], parts[1])
        server = await self._load_server_for_admin(int(server_id))
        if not server:
            await self._safe_edit_message_by_id(chat_id, message_id, "Сервер не найден.", reply_markup=back_keyboard("adm:servers"))
            return
        await self._safe_edit_message_by_id(chat_id, message_id, self._render_server(server), reply_markup=self._server_actions_markup(server))
        await state.clear()

    async def receive_server_billing_payload(self, message: Message, state: FSMContext) -> None:
        if not await self._assert_admin_message(message):
            return
        data = await state.get_data()
        chat_id = data.get("source_chat_id")
        message_id = data.get("source_message_id")
        server_id = data.get("server_id")
        if not server_id:
            await self._safe_edit_message_by_id(chat_id, message_id, "Сервер для настройки оплаты не найден.", reply_markup=back_keyboard("adm:servers"))
            return
        payload = (message.text or "").strip()
        if payload.lower() == "off":
            await self.store.clear_server_billing_config(int(server_id))
        else:
            parts = [chunk.strip() for chunk in payload.split("|", 3)]
            if len(parts) < 4:
                server = await self._load_server_for_admin(int(server_id))
                billing_cfg = await self.store.get_server_billing_config(int(server_id))
                await self._safe_edit_message_by_id(chat_id, message_id, self._render_server_billing_prompt(server, billing_cfg), reply_markup=back_keyboard(f"adm:server:view:{server_id}"))
                return
            amount_raw = parts[0].replace(",", ".")
            try:
                amount_rub = Decimal(amount_raw)
            except InvalidOperation:
                await self._safe_edit_message_by_id(chat_id, message_id, "💳 Оплата сервера\n\nСумма должна быть числом. Пример: 1490|25.04.2026|30|3", reply_markup=back_keyboard(f"adm:server:view:{server_id}"))
                return
            if amount_rub <= 0:
                await self._safe_edit_message_by_id(chat_id, message_id, "💳 Оплата сервера\n\nСумма должна быть больше 0.", reply_markup=back_keyboard(f"adm:server:view:{server_id}"))
                return
            try:
                next_due = datetime.strptime(parts[1], "%d.%m.%Y").date()
            except ValueError:
                await self._safe_edit_message_by_id(chat_id, message_id, "💳 Оплата сервера\n\nДата должна быть в формате ДД.ММ.ГГГГ. Пример: 25.04.2026", reply_markup=back_keyboard(f"adm:server:view:{server_id}"))
                return
            try:
                period_days = max(int(parts[2]), 1)
                remind_days = max(int(parts[3]), 0)
            except ValueError:
                await self._safe_edit_message_by_id(chat_id, message_id, "💳 Оплата сервера\n\nПериод и напоминание должны быть целыми числами. Пример: 1490|25.04.2026|30|3", reply_markup=back_keyboard(f"adm:server:view:{server_id}"))
                return
            await self.store.set_server_billing_config(int(server_id), amount_rub, next_due, period_days, remind_days)
        server = await self._load_server_for_admin(int(server_id))
        if not server:
            await self._safe_edit_message_by_id(chat_id, message_id, "Сервер не найден.", reply_markup=back_keyboard("adm:servers"))
            return
        await self._safe_edit_message_by_id(chat_id, message_id, self._render_server(server), reply_markup=self._server_actions_markup(server))
        await state.clear()

    async def receive_server_agent_command(self, message: Message, state: FSMContext) -> None:
        if not await self._assert_admin_message(message):
            return
        data = await state.get_data()
        chat_id = data.get("source_chat_id")
        message_id = data.get("source_message_id")
        server_id = data.get("server_id")
        if not server_id:
            await self._safe_edit_message_by_id(chat_id, message_id, "Сервер для команды не найден.", reply_markup=back_keyboard("adm:servers"))
            return
        command = (message.text or "").strip()
        if not command:
            await self._safe_edit_message_by_id(chat_id, message_id, "⌨️ Команда для Ubuntu-agent\n\nВведите команду, например:\nsystemctl restart x-ui\nsystemctl status xray\njournalctl -u x-ui -n 50 --no-pager", reply_markup=back_keyboard(f"adm:server:view:{server_id}"))
            return
        server = await self._load_server_for_admin(int(server_id))
        if not server:
            await self._safe_edit_message_by_id(chat_id, message_id, "Сервер не найден.", reply_markup=back_keyboard("adm:servers"))
            return
        result_text = await self._run_server_agent_command(server, command)
        server = await self._load_server_for_admin(int(server_id))
        if not server:
            await self._safe_edit_message_by_id(chat_id, message_id, "Сервер не найден.", reply_markup=back_keyboard("adm:servers"))
            return
        await self._safe_edit_message_by_id(chat_id, message_id, self._render_server(server, notice=result_text), reply_markup=self._server_actions_markup(server))
        await state.clear()

    async def receive_payment_config_payload(self, message: Message, state: FSMContext) -> None:
        if not await self._assert_admin_message(message):
            return
        data = await state.get_data()
        chat_id = data.get("source_chat_id")
        message_id = data.get("source_message_id")
        provider = data.get("provider")
        if not provider:
            await self._safe_edit_message_by_id(chat_id, message_id, "Платёжный провайдер не найден.", reply_markup=back_keyboard("adm:payments"))
            return
        payload = (message.text or "").strip()
        if payload.lower() == "off":
            if provider == "yookassa":
                await self.store.set_payment_provider_settings(provider, {"yookassa_shop_id": "", "yookassa_secret_key": "", "yookassa_return_url": settings.yookassa_return_url})
            elif provider == "crypto":
                await self.store.set_payment_provider_settings(provider, {"crypto_pay_token": "", "crypto_pay_use_testnet": False, "crypto_pay_assets": settings.crypto_assets})
        elif provider == "yookassa":
            parts = [chunk.strip() for chunk in payload.split("|", 2)]
            if len(parts) < 3:
                await self._safe_edit_message_by_id(chat_id, message_id, "💳 Настройка YooKassa\n\nФормат: shop_id|secret_key|return_url", reply_markup=back_keyboard("adm:payments"))
                return
            await self.store.set_payment_provider_settings(provider, {"yookassa_shop_id": parts[0], "yookassa_secret_key": parts[1], "yookassa_return_url": parts[2]})
        elif provider == "crypto":
            parts = [chunk.strip() for chunk in payload.split("|", 2)]
            if len(parts) < 3:
                await self._safe_edit_message_by_id(chat_id, message_id, "🪙 Настройка Crypto Pay\n\nФормат: token|testnet(true/false)|USDT,TON,BTC", reply_markup=back_keyboard("adm:payments"))
                return
            use_testnet = parts[1].lower() in {"1", "true", "yes", "on"}
            assets = [item.strip().upper() for item in parts[2].split(",") if item.strip()]
            await self.store.set_payment_provider_settings(provider, {"crypto_pay_token": parts[0], "crypto_pay_use_testnet": use_testnet, "crypto_pay_assets": assets})
        toggles = [toggle for toggle in await self.store.list_toggles() if toggle.key.startswith("payment_")]
        payment_config = await self.store.get_payment_settings_snapshot()
        await self._safe_edit_message_by_id(chat_id, message_id, self._render_payments_admin(toggles, payment_config), reply_markup=toggles_keyboard(toggles, payment_config))
        await state.clear()

    async def receive_text_body(self, message: Message, state: FSMContext) -> None:
        if not await self._assert_admin_message(message):
            return
        data = await state.get_data()
        chat_id = data.get("source_chat_id")
        message_id = data.get("source_message_id")
        key = data.get("content_key")
        group = data.get("content_group", "texts")
        if not key:
            await self._safe_edit_message_by_id(chat_id, message_id, "Раздел для редактирования не найден.", reply_markup=back_keyboard(f"adm:texts:{group}"))
            return
        await self.store.set_content(key, message.text or "")
        pages = await self.store.list_content_pages(group=group)
        await self._safe_edit_message_by_id(chat_id, message_id, self._render_texts_admin(group, pages), reply_markup=contents_keyboard(pages, group=group))
        await state.clear()

    async def receive_referral_percent(self, message: Message, state: FSMContext) -> None:
        if not await self._assert_admin_message(message):
            return
        data = await state.get_data()
        chat_id = data.get("source_chat_id")
        message_id = data.get("source_message_id")
        try:
            percent = int((message.text or "").strip())
        except ValueError:
            await self._safe_edit_message_by_id(chat_id, message_id, "Введите целое число, например 10.", reply_markup=back_keyboard("adm:referral"))
            return
        await self.store.set_setting("referral_percent", str(percent))
        visible = await self.store.get_toggle("section_referral", default=True)
        await self._safe_edit_message_by_id(chat_id, message_id, self._render_referral_admin(percent, visible), reply_markup=referral_admin_keyboard(visible))
        await state.clear()

    async def receive_trial_days(self, message: Message, state: FSMContext) -> None:
        if not await self._assert_admin_message(message):
            return
        data = await state.get_data()
        chat_id = data.get("source_chat_id")
        message_id = data.get("source_message_id")
        try:
            days = int((message.text or "").strip())
        except ValueError:
            await self._safe_edit_message_by_id(chat_id, message_id, "Введите количество дней числом.", reply_markup=back_keyboard("adm:trial"))
            return
        await self.store.set_setting("trial_days", str(days))
        await self._safe_edit_message_by_id(chat_id, message_id, await self._render_trial_admin(), reply_markup=trial_admin_keyboard())
        await state.clear()

    async def receive_balance_amount(self, message: Message, state: FSMContext) -> None:
        if not await self._assert_admin_message(message):
            return
        data = await state.get_data()
        chat_id = data.get("source_chat_id")
        message_id = data.get("source_message_id")
        user_id = data.get("user_id")
        filter_key = data.get("filter_key", "all")
        page = int(data.get("page", 1))
        if not user_id:
            await self._safe_edit_message_by_id(chat_id, message_id, "Пользователь для пополнения не найден.", reply_markup=back_keyboard("adm:users:all:1"))
            return
        try:
            amount = Decimal((message.text or "").strip())
        except InvalidOperation:
            await self._safe_edit_message_by_id(chat_id, message_id, "💰 Выдача баланса\n\nВведите сумму числом, например 100 или 100.50.", reply_markup=back_keyboard(f"adm:user:{user_id}:{filter_key}:{page}"))
            return
        await self.store.add_balance(user_id, amount, "admin_adjustment", "Начисление администратором")
        admin_user = await self.store.get_user_admin_summary(user_id)
        await self._safe_edit_message_by_id(chat_id, message_id, self._render_admin_user(admin_user), reply_markup=user_actions_keyboard(admin_user.id, admin_user.is_blocked, filter_key, page, can_manage_block=not admin_user.is_admin))
        await state.clear()

    async def receive_manual_subscription_days(self, message: Message, state: FSMContext) -> None:
        if not await self._assert_admin_message(message):
            return
        data = await state.get_data()
        chat_id = data.get("source_chat_id")
        message_id = data.get("source_message_id")
        user_id = data.get("grant_user_id")
        filter_key = data.get("filter_key", "all")
        page = int(data.get("page", 1))
        if not user_id:
            await self._safe_edit_message_by_id(chat_id, message_id, "Пользователь для выдачи не найден.", reply_markup=back_keyboard("adm:users:all:1"))
            return
        try:
            days = int((message.text or "").strip())
        except ValueError:
            await self._safe_edit_message_by_id(chat_id, message_id, "🌐 Ручная выдача доступа\n\nВведите срок в днях числом, например 30.", reply_markup=back_keyboard(f"adm:user:{user_id}:{filter_key}:{page}"))
            return
        if days <= 0:
            await self._safe_edit_message_by_id(chat_id, message_id, "🌐 Ручная выдача доступа\n\nСрок должен быть больше 0 дней.", reply_markup=back_keyboard(f"adm:user:{user_id}:{filter_key}:{page}"))
            return
        subscription, vpn_keys, error = await self.provisioning.issue_manual_subscription(user_id, days)
        if error or not subscription or not vpn_keys:
            await self._safe_edit_message_by_id(chat_id, message_id, f"🌐 Ручная выдача доступа\n\n{error or 'Не удалось выдать доступ.'}", reply_markup=back_keyboard(f"adm:user:{user_id}:{filter_key}:{page}"))
            return
        reserve_url = await self._reserve_access_url(getattr(subscription, 'user', None))
        actor = await self._admin_actor(message.from_user)
        await self._log_admin_action(actor, action='manual_access_issue', description='????? ?????? ?????? ????????????', target_user_id=user_id, details={'days': days, 'subscription_id': getattr(subscription, 'id', None)})
        await self._safe_edit_message_by_id(chat_id, message_id, await self._render_activation_result(subscription, vpn_keys, manual=True, reserve_url=reserve_url), reply_markup=admin_result_keyboard(self._subscription_action_rows([subscription], f"adminuser:{user_id}:{filter_key}:{page}"), f"adm:user:{user_id}:{filter_key}:{page}"))
        await state.clear()

    async def receive_broadcast_text(self, message: Message, state: FSMContext) -> None:
        if not await self._assert_admin_message(message):
            return
        data = await state.get_data()
        chat_id = data.get("source_chat_id")
        message_id = data.get("source_message_id")
        filter_key = data.get("filter_key", "all")
        targets = await self.store.get_broadcast_targets(filter_key)
        success = 0
        for telegram_id in targets:
            try:
                await self.bot.send_message(telegram_id, message.text or "")
                success += 1
                await asyncio.sleep(0.03)
            except Exception:
                continue
        await self._safe_edit_message_by_id(chat_id, message_id, f"📣 Рассылка завершена\n\nАудитория: {self._filter_title(filter_key)}\nДоставлено: {success}/{len(targets)}", reply_markup=broadcast_filters_keyboard())
        await state.clear()

    async def fallback_message(self, message: Message, state: FSMContext) -> None:
        user = await self.store.get_or_create_user(message.from_user)
        if await self._deny_blocked_message(message, user):
            return
        text_value = (message.text or '').strip()
        ui = await self._ui_snapshot()
        labels = await self._user_button_labels(ui)
        if text_value:
            if text_value == labels.get('nav_profile'):
                await self.show_profile(message, state)
                return
            if text_value == labels.get('nav_buy'):
                await self.show_buy(message, state)
                return
            if text_value == labels.get('nav_help'):
                await self.show_help(message, state)
                return
            if text_value == labels.get('nav_referral') and ui.get('show_referral'):
                await self.show_referrals(message, state)
                return
            if text_value == labels.get('nav_trial') and ui.get('show_trial'):
                await self.show_trial(message, state)
                return
            if text_value == ADMIN_LABEL:
                await self.show_admin_panel_message(message, state)
                return
        await self._send_inline_screen(message, await self._render_home_text(), await self._home_inline_markup(user.is_admin, ui=ui))

    async def noop(self, callback: CallbackQuery) -> None:
        await self._safe_answer_callback(callback, )

    async def _send_inline_screen(self, message: Message, text: str, reply_markup) -> None:
        cleanup = await message.answer("·", reply_markup=ReplyKeyboardRemove())
        try:
            await cleanup.delete()
        except Exception:
            pass
        await message.answer(text, reply_markup=reply_markup, link_preview_options=LinkPreviewOptions(is_disabled=True))

    async def _send_qr_image(self, chat_id: int, payload: str, filename: str, caption: str) -> None:
        png = build_qr_png_bytes(payload)
        await self.bot.send_photo(chat_id=chat_id, photo=BufferedInputFile(png, filename=filename), caption=caption)

    async def _safe_answer_callback(self, callback: CallbackQuery, text: str | None = None, show_alert: bool = False) -> None:
        try:
            await callback.answer(text, show_alert=show_alert)
        except TelegramBadRequest as exc:
            message = str(exc)
            if "query is too old" in message or "query ID is invalid" in message:
                return
            raise

    def _should_resend_screen(self, error_text: str) -> bool:
        lowered = error_text.lower()
        return any(
            marker in lowered
            for marker in (
                "message can't be edited",
                "message to edit not found",
                "there is no text in the message to edit",
                "message identifier is not specified",
                "chat not found",
            )
        )

    async def _safe_edit_message_text(self, message: Message | None, text: str, reply_markup=None) -> None:
        if message is None:
            return
        try:
            await message.edit_text(text, reply_markup=reply_markup, link_preview_options=LinkPreviewOptions(is_disabled=True))
        except TelegramBadRequest as exc:
            error_text = str(exc)
            if "message is not modified" in error_text:
                return
            if self._should_resend_screen(error_text):
                await message.answer(text, reply_markup=reply_markup, link_preview_options=LinkPreviewOptions(is_disabled=True))
                return
            raise

    async def _safe_edit_message_by_id(self, chat_id: int | None, message_id: int | None, text: str, reply_markup=None) -> None:
        if not chat_id or not message_id:
            return
        try:
            await self.bot.edit_message_text(text=text, chat_id=chat_id, message_id=message_id, reply_markup=reply_markup, link_preview_options=LinkPreviewOptions(is_disabled=True))
        except TelegramBadRequest as exc:
            error_text = str(exc)
            if "message is not modified" in error_text:
                return
            if self._should_resend_screen(error_text):
                await self.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, link_preview_options=LinkPreviewOptions(is_disabled=True))
                return
            raise

    async def _admin_actor(self, tg_user) -> object:
        return await self.store.get_or_create_user(tg_user)

    def _admin_role_value(self, user) -> str:
        if not user:
            return 'user'
        if getattr(user, 'telegram_id', None) in settings.admin_ids:
            return 'owner'
        role = (getattr(user, 'admin_role', None) or '').strip().lower()
        if role in {'owner', 'admin', 'support', 'finance', 'ops'}:
            return role
        return 'admin' if getattr(user, 'is_admin', False) else 'user'

    def _admin_role_title(self, role: str) -> str:
        return {
            'owner': '????????',
            'admin': '?????????????',
            'support': '?????????',
            'finance': '???????',
            'ops': '????????',
            'user': '????????????',
        }.get(role, role)
    def _admin_role_badge(self, role: str) -> str:
        return {
            'owner': '??',
            'admin': '???',
            'support': '??',
            'finance': '??',
            'ops': '???',
            'user': '??',
        }.get(role, '??')
    def _admin_sections(self, role: str) -> set[str]:
        mapping = {
            'owner': {'panel', 'finance', 'analytics', 'guide', 'reserve', 'tariffs', 'tariff', 'payments', 'paymentcfg', 'toggle', 'users', 'user', 'texts', 'text', 'servers', 'server', 'referral', 'broadcast', 'trial', 'backup', 'updates', 'roles', 'role', 'audit'},
            'admin': {'panel', 'finance', 'analytics', 'guide', 'reserve', 'tariffs', 'tariff', 'payments', 'paymentcfg', 'toggle', 'users', 'user', 'texts', 'text', 'servers', 'server', 'referral', 'broadcast', 'trial', 'backup', 'updates'},
            'support': {'panel', 'guide', 'reserve', 'users', 'user', 'texts', 'text', 'updates'},
            'finance': {'panel', 'finance', 'analytics', 'payments', 'paymentcfg', 'users', 'user', 'updates'},
            'ops': {'panel', 'servers', 'server', 'guide', 'updates'},
        }
        return mapping.get(role, set())

    def _can_access_admin_section(self, role: str, section: str) -> bool:
        return section in self._admin_sections(role)

    def _admin_user_action_permissions(self, role: str) -> dict[str, bool]:
        return {
            'can_grant_balance': role in {'owner', 'admin', 'finance'},
            'can_grant_access': role in {'owner', 'admin'},
            'can_view_diagnostics': role in {'owner', 'admin', 'support', 'finance', 'ops'},
            'can_manage_block': role in {'owner', 'admin', 'support'},
            'can_manage_role': role == 'owner',
        }

    def _can_edit_role(self, actor_role: str, target_user) -> bool:
        if actor_role != 'owner' or not target_user:
            return False
        return getattr(target_user, 'telegram_id', None) not in settings.admin_ids

    def _admin_display_name(self, user) -> str:
        return getattr(user, 'full_name', None) or getattr(user, 'username', None) or str(getattr(user, 'telegram_id', '-'))

    def _role_scope_lines(self, role: str) -> list[str]:
        scopes = {
            'owner': ['?????? ?????? ?? ???? ???????? ? ??????????.', '????? ?????? ????, ???????? ?????? ???????? ? ????????? ???? ????????.'],
            'admin': ['????????? ??????????????, ????????, ????????, ????????? ? ??????????.', '?? ?????? ???? ? ?? ????? ?????? ????????.'],
            'support': ['???????? ? ??????????????, ????????, ???????????? ? ????????????.', '?? ????????? ????????, ?????? ? ?????????.'],
            'finance': ['????? ???????, ?????????, ?????? ? ???????????????? ????????.', '?? ?????? ????, ??????? ? ??????.'],
            'ops': ['???????? ? ?????????, ???????????? ? ??????????? ????????????.', '?? ????????? ???????? ? ??????.'],
        }
        return scopes.get(role, ['???? ?? ???? ??????? ? ????????? ????????.'])
    async def _log_admin_action(self, actor, action: str, description: str, **kwargs) -> None:
        await self.store.log_admin_action(
            actor_user_id=getattr(actor, 'id', None),
            action=action,
            description=description,
            target_user_id=kwargs.get('target_user_id'),
            target_server_id=kwargs.get('target_server_id'),
            details=kwargs.get('details'),
        )

    def _admin_user_markup(self, admin_user, actor_role: str, filter_key: str, page: int):
        permissions = self._admin_user_action_permissions(actor_role)
        can_manage_block = permissions['can_manage_block'] and self._admin_role_value(admin_user) == 'user'
        return user_actions_keyboard(
            admin_user.id,
            admin_user.is_blocked,
            filter_key,
            page,
            can_manage_block=can_manage_block,
            can_grant_balance=permissions['can_grant_balance'],
            can_grant_access=permissions['can_grant_access'],
            can_view_diagnostics=permissions['can_view_diagnostics'],
            can_manage_role=permissions['can_manage_role'],
        )

    async def _assert_admin_message(self, message: Message) -> bool:
        actor = await self._admin_actor(message.from_user)
        if self._admin_role_value(actor) == 'user':
            await message.answer('? ??? ??? ??????? ? ?????-??????.')
            return False
        return True
    async def _assert_admin_callback(self, callback: CallbackQuery) -> bool:
        actor = await self._admin_actor(callback.from_user)
        if self._admin_role_value(actor) == 'user':
            await self._safe_answer_callback(callback, '??? ???????.', show_alert=True)
            return False
        return True
    async def _deny_blocked_message(self, message: Message, user) -> bool:
        if user.is_admin or message.from_user.id in settings.admin_ids:
            return False
        if user.is_blocked:
            await message.answer(self._blocked_access_text())
            return True
        return False

    async def _deny_blocked_callback(self, callback: CallbackQuery, user) -> bool:
        if user.is_admin or callback.from_user.id in settings.admin_ids:
            return False
        if user.is_blocked:
            await self._safe_answer_callback(callback, self._blocked_access_text(), show_alert=True)
            return True
        return False

    def _blocked_access_text(self) -> str:
        return "Ваш доступ к боту ограничен администратором. Если это ошибка, напишите в поддержку."

    def _server_payload_hint(self) -> str:
        return "🖥️ Добавление сервера\n\nОтправьте данные в формате:\nНазвание|https://panel.example.com[:port][/path]|username|password|inbound_id\n\nURL можно указывать сразу с секретным путём или /panel, если ваша 3x-ui открывается не из корня.\ninbound_id — это именно ID inbound в разделе Inbounds, а не порт панели."

    async def _main_menu_markup(self, is_admin: bool):
        ui = await self._ui_snapshot()
        return build_main_menu(is_admin=is_admin, show_referral=ui["show_referral"], show_trial=ui["show_trial"], labels=await self._user_button_labels(ui))

    async def _ui_snapshot(self) -> dict:
        return await self.store.get_ui_snapshot()

    async def _user_button_labels(self, ui: dict | None = None) -> dict[str, str]:
        ui = ui or await self._ui_snapshot()
        return dict(ui.get("button_labels") or {})

    async def _home_inline_markup(self, is_admin: bool, ui: dict | None = None):
        ui = ui or await self._ui_snapshot()
        return home_inline_keyboard(is_admin=is_admin, show_referral=ui["show_referral"], show_trial=ui["show_trial"], labels=await self._user_button_labels(ui))

    async def _profile_markup(self, user, ui: dict | None = None, page: int = 1):
        ui = ui or await self._ui_snapshot()
        subscriptions = self._profile_subscriptions(user)
        page_items, current_page, total_pages = self._paginate_items(subscriptions, page, page_size=10)
        context = f"profile:{current_page}" if total_pages > 1 else "profile"
        return profile_inline_keyboard(
            self._subscription_actions(page_items, context),
            user.is_admin,
            ui["show_referral"],
            ui["show_trial"],
            labels=await self._user_button_labels(ui),
            page=current_page,
            total_pages=total_pages,
        )

    async def _help_markup(self, is_admin: bool, ui: dict | None = None):
        ui = ui or await self._ui_snapshot()
        return help_inline_keyboard(
            ui["channel_url"],
            ui["support_chat_url"],
            ui["terms_url"],
            is_admin,
            ui["show_referral"],
            ui["show_trial"],
            labels=await self._user_button_labels(ui),
        )

    async def _referral_markup(self, user, ui: dict | None = None):
        ui = ui or await self._ui_snapshot()
        return referral_inline_keyboard(self._invite_link(user), user.is_admin, ui["show_referral"], ui["show_trial"], labels=await self._user_button_labels(ui))

    async def _trial_markup(self, user, ui: dict | None = None):
        ui = ui or await self._ui_snapshot()
        return trial_inline_keyboard(not user.trial_claimed, user.is_admin, ui["show_referral"], ui["show_trial"], labels=await self._user_button_labels(ui))

    async def _load_servers_for_admin(self):
        servers = self._prepare_server_monitoring(await self.store.list_servers_with_monitoring())
        await self._decorate_servers_with_agent_status(servers)
        await self._decorate_servers_with_billing_info(servers)
        await self._decorate_servers_with_runtime_health(servers)
        return servers

    async def _load_server_for_admin(self, server_id: int):
        server = await self.store.get_server_monitoring_details(server_id)
        if not server:
            return None
        self._prepare_server_monitoring([server])
        await self._decorate_servers_with_agent_status([server])
        await self._decorate_servers_with_billing_info([server])
        await self._decorate_servers_with_runtime_health([server])
        return server

    async def _decorate_servers_with_agent_status(self, servers) -> None:
        for server in servers:
            await self._decorate_server_with_agent_status(server)

    async def _decorate_servers_with_billing_info(self, servers) -> None:
        for server in servers:
            await self._decorate_server_with_billing_info(server)

    async def _decorate_servers_with_runtime_health(self, servers) -> None:
        if not servers:
            return
        failure_stats = self.provisioning.get_server_failure_stats(window_minutes=180)
        reissue_counts = await self.store.get_server_reissue_counts([server.id for server in servers])
        for server in servers:
            self._decorate_server_with_runtime_health(server, failure_stats.get(server.id, {}), reissue_counts.get(server.id, 0))

    def _decorate_server_with_runtime_health(self, server, failure_stats: dict | None = None, reissue_count: int = 0) -> None:
        failure_stats = failure_stats or {}
        setattr(server, 'recent_failures_count', int(failure_stats.get('count', 0) or 0))
        setattr(server, 'last_failure_at', failure_stats.get('last_ts'))
        setattr(server, 'last_failure_error', str(failure_stats.get('last_error') or ''))
        setattr(server, 'last_failure_stage', str(failure_stats.get('last_stage') or ''))
        setattr(server, 'reissue_count', int(reissue_count or 0))
        active_users = int(getattr(server, 'active_subscriptions_count', 0) or 0)
        amount_rub = Decimal(str(getattr(server, 'billing_amount_rub', Decimal('0.00')) or '0'))
        if amount_rub > Decimal('0.00') and active_users > 0:
            cost_per_user_rub = (amount_rub / Decimal(active_users)).quantize(Decimal('0.01'))
        else:
            cost_per_user_rub = Decimal('0.00')
        setattr(server, 'cost_per_user_rub', cost_per_user_rub)
        health_score, health_badge_icon, health_badge_label = self._calculate_server_health_score(server)
        setattr(server, 'health_score', health_score)
        setattr(server, 'health_badge_icon', health_badge_icon)
        setattr(server, 'health_badge_label', health_badge_label)

    def _calculate_server_health_score(self, server) -> tuple[int, str, str]:
        score = 100
        if getattr(server, 'health_status', '') != 'online':
            score -= 45
        cpu_percent = int(getattr(server, 'cpu_percent', 0) or 0)
        ram_percent = int(getattr(server, 'ram_percent', 0) or 0)
        memory_percent = int(getattr(server, 'agent_memory_percent', 0) or 0)
        load_factor = max(cpu_percent, ram_percent, memory_percent)
        if load_factor >= 90:
            score -= 18
        elif load_factor >= 75:
            score -= 10
        elif load_factor >= 60:
            score -= 4
        recent_failures = int(getattr(server, 'recent_failures_count', 0) or 0)
        score -= min(recent_failures * 7, 28)
        if getattr(server, 'last_error', ''):
            score -= 8
        if getattr(server, 'agent_configured', False) and not getattr(server, 'agent_online', False):
            score -= 7
        if getattr(server, 'agent_error', ''):
            score -= 5
        score = max(0, min(100, score))
        if score >= 85:
            return score, '🟢', 'отлично'
        if score >= 70:
            return score, '🟡', 'стабильно'
        if score >= 50:
            return score, '🟠', 'под риском'
        return score, '🔴', 'критично'

    async def _decorate_server_with_agent_status(self, server) -> None:
        agent_cfg = await self.store.get_server_agent_config(server.id)
        agent_url = (agent_cfg.get("url") or "").strip()
        agent_token = (agent_cfg.get("token") or "").strip()
        setattr(server, "agent_configured", bool(agent_url and agent_token))
        setattr(server, "agent_online", False)
        setattr(server, "agent_url", agent_url)
        setattr(server, "agent_error", "")
        setattr(server, "agent_host", "")
        setattr(server, "agent_version", "")
        setattr(server, "agent_uptime", "")
        setattr(server, "agent_load", "")
        setattr(server, "agent_memory_percent", 0)
        setattr(server, "agent_disk_percent", 0)
        setattr(server, "agent_services", {})
        if not agent_url or not agent_token:
            return
        try:
            status = await ServerAgentClient(agent_url, agent_token).fetch_status()
        except ServerAgentError as exc:
            setattr(server, "agent_error", str(exc))
            return
        setattr(server, "agent_online", status.online)
        setattr(server, "agent_host", status.host)
        setattr(server, "agent_version", status.version)
        setattr(server, "agent_uptime", status.uptime)
        setattr(server, "agent_load", status.load)
        setattr(server, "agent_memory_percent", status.memory_percent)
        setattr(server, "agent_disk_percent", status.disk_percent)
        setattr(server, "agent_services", status.services)

    async def _decorate_server_with_billing_info(self, server) -> None:
        billing = await self.store.get_server_billing_config(server.id)
        setattr(server, "billing_amount_rub", billing.get("amount_rub", Decimal("0.00")))
        setattr(server, "billing_next_due", billing.get("next_due"))
        setattr(server, "billing_period_days", billing.get("period_days", 30))
        setattr(server, "billing_remind_days", billing.get("remind_days", 3))
        setattr(server, "billing_configured", bool(billing.get("configured")))
        setattr(server, "billing_last_notice", billing.get("last_notice", ""))
        next_due = billing.get("next_due")
        billing_status = "не настроена"
        billing_days_left = None
        if next_due is not None:
            billing_days_left = (next_due - datetime.utcnow().date()).days
            if billing_days_left < 0:
                billing_status = f"просрочена на {abs(billing_days_left)} дн."
            elif billing_days_left == 0:
                billing_status = "сегодня"
            else:
                billing_status = f"через {billing_days_left} дн."
        setattr(server, "billing_status", billing_status)
        setattr(server, "billing_days_left", billing_days_left)

    async def _run_server_agent_command(self, server, command: str) -> str:
        agent_url = getattr(server, "agent_url", "") or ""
        agent_cfg = await self.store.get_server_agent_config(server.id)
        agent_token = (agent_cfg.get("token") or "").strip()
        if not agent_url or not agent_token:
            return "⚠️ Агент не настроен. Сначала подключите Ubuntu-agent для этого сервера."
        try:
            result = await ServerAgentClient(agent_url, agent_token).run_command(command)
        except ServerAgentError as exc:
            return f"⚠️ Команда не выполнена: {exc}"
        return f"✅ {result}"

    async def _run_server_agent_command(self, server, command: str) -> str:
        agent_url = getattr(server, "agent_url", "") or ""
        agent_cfg = await self.store.get_server_agent_config(server.id)
        agent_token = (agent_cfg.get("token") or "").strip()
        if not agent_url or not agent_token:
            return "⚠️ Агент не настроен. Сначала подключите Ubuntu-agent для этого сервера."
        try:
            result = await ServerAgentClient(agent_url, agent_token).run_command(command)
        except ServerAgentError as exc:
            return f"⚠️ Команда не выполнена: {exc}"
        return f"✅ {result}"

    def _server_actions_markup(self, server):
        return server_actions_keyboard(
            server.id,
            server.base_url,
            agent_configured=bool(getattr(server, "agent_configured", False)),
            agent_online=bool(getattr(server, "agent_online", False)),
            billing_configured=bool(getattr(server, "billing_configured", False)),
        )

    async def _visible_payment_methods(self, ui: dict | None = None) -> list[str]:
        ui = ui or await self._ui_snapshot()
        payment_config = await self.store.get_payment_settings_snapshot()
        methods: list[str] = []
        for method in list(ui["payment_methods"]):
            if method == "yookassa" and not (payment_config.get("yookassa_shop_id") and payment_config.get("yookassa_secret_key")):
                continue
            if method == "crypto" and not payment_config.get("crypto_pay_token"):
                continue
            methods.append(method)
        return methods

    async def _payment_methods_for_user(self, user, tariff) -> list[str]:
        if not user or not tariff:
            return []
        methods = await self._visible_payment_methods()
        available: list[str] = []
        for method in methods:
            if method == "stars" and tariff.price_stars <= 0:
                continue
            if method in {"yookassa", "crypto", "balance"} and Decimal(str(tariff.price_rub)) <= 0:
                continue
            if method == "balance" and user.balance < Decimal(str(tariff.price_rub)):
                continue
            available.append(method)
        return available

    async def _render_home_text(self) -> str:
        page = await self.store.get_content("main")
        tariffs = await self.store.list_tariffs(only_active=True)
        lines = [
            f"{self._brand_name()} | личный кабинет доступа",
            "",
            (page.body if page else "Быстрый доступ, спокойная навигация и единая подписка без хаоса из десятков сообщений."),
            "",
            "Что внутри:",
            "• одна подписка собирает все включённые серверы в одном месте;",
            "• ключи по серверам можно открыть, заменить или удалить без лишней путаницы;",
            "• профиль, покупка и продление работают внутри одной логики и не разваливаются на лишние экраны.",
        ]
        if tariffs:
            lines.extend(["", "Витрина тарифов:"])
            lines.extend(self._render_tariff_lines(tariffs))
        lines.extend(["", "Выберите раздел ниже и продолжайте с того места, где остановились."])
        return "\n".join(lines)

    async def _render_buy_text(self, tariffs) -> str:
        page = await self.store.get_content("buy")
        methods = await self._visible_payment_methods()
        method_titles = {
            "stars": "Telegram Stars",
            "yookassa": "YooKassa",
            "crypto": "Crypto",
            "balance": "внутренний баланс",
        }
        starter = self._starter_tariff(tariffs)
        popular = self._popular_tariff(tariffs)
        best_value = self._best_value_tariff(tariffs)
        lines = [
            "🛍️ Витрина доступа",
            "",
            (page.body if page else "Выберите тариф ниже. После оплаты бот создаст новый доступ или аккуратно продлит уже действующий."),
            "",
            "Что вы получаете:",
            "• одну общую ссылку подписки на все включённые серверы;",
            "• отдельные серверные ключи внутри подписки для ручной замены или диагностики;",
            "• возможность продлить доступ без повторной ручной настройки.",
            "",
            f"💳 Оплата: {', '.join(method_titles[item] for item in methods) if methods else 'временно недоступна'}",
        ]
        recommendations: list[str] = []
        if starter:
            recommendations.append(f"🚀 Быстрый старт: {starter.name} — удобно для первого входа.")
        if popular:
            recommendations.append(f"🔥 Оптимальный выбор: {popular.name} — меньше возни с продлениями.")
        if best_value:
            recommendations.append(f"💎 Лучшая цена за 30 дней: {best_value.name} — {format_money(self._tariff_monthly_rub(best_value))} / 30 дн.")
        if recommendations:
            lines.extend(["", "Подсказки по выбору:", *recommendations])
        lines.extend(["", "Доступные тарифы:"])
        lines.extend(self._render_tariff_lines(tariffs))
        return "\n".join(lines)
    async def _render_help_text(self) -> str:
        page = await self.store.get_content("help")
        ui = await self._ui_snapshot()
        profile_label = (ui.get("button_labels") or {}).get("nav_profile", "👤 Мой профиль")
        return "\n".join([
            "❓ Подключение и поддержка",
            "",
            (page.body if page else "Здесь собраны основные шаги, чтобы быстро подключиться и не искать нужную кнопку по всему чату."),
            "",
            "Как пользоваться ботом:",
            f"• откройте {profile_label};",
            "• выберите нужную подписку;",
            "• скопируйте общую ссылку или откройте серверный ключ;",
            "• при проблеме с ключом перевыпустите его прямо из карточки.",
            "",
            "Если срок подходит к концу, продлите доступ заранее и бот обновит всё внутри текущей подписки.",
        ])

    async def _render_device_guides_menu(self) -> str:
        page = await self.store.get_content("devices_menu")
        return page.body if page and page.body else (
            "📱 Подключение по устройствам\n\n"
            'Выберите своё устройство ниже. Внутри будет короткая пошаговая инструкция, как вставить общую ссылку доступа в приложение.\n\n'
            "Общий принцип везде один:\n"
            "• откройте подписку в профиле;\n"
            "• скопируйте общую ссылку;\n"
            "• в клиенте найдите Import / Subscription / URL;\n"
            "• вставьте ссылку и обновите конфигурацию.\n\n"
            "Если приложение не принимает общую ссылку, откройте внутри подписки конкретный серверный ключ и импортируйте его отдельно."
        )

    async def _render_device_guide(self, platform_key: str) -> str:
        key_map = {
            'ios': 'guide_ios',
            'android': 'guide_android',
            'windows': 'guide_windows',
            'macos': 'guide_macos',
        }
        page = await self.store.get_content(key_map.get(platform_key, 'guide_windows'))
        if page and page.body:
            return page.body
        guides = {
            'ios': 'iPhone / iPad\n\n1. Скопируйте общую ссылку из подписки в боте.\n2. Откройте приложение, которое умеет импорт по URL.\n3. Найдите пункт вроде Import, Subscription, Add from URL.\n4. Вставьте ссылку и подтвердите импорт.\n5. После добавления обновите подписку и подключайтесь к нужному серверу.\n\nЕсли клиент не принимает общую ссылку, откройте конкретный серверный ключ внутри подписки и импортируйте его отдельно.',
            'android': 'Android\n\n1. Скопируйте общую ссылку из подписки в боте.\n2. Откройте приложение и выберите импорт из буфера, URL или subscription.\n3. Вставьте ссылку и сохраните конфигурацию.\n4. Обновите список серверов внутри приложения.\n5. Выберите нужный сервер и подключайтесь.\n\nЕсли приложение просит формат, обычно нужен URL / Subscription, а не текстовый файл.',
            'windows': 'Windows\n\n1. Скопируйте общую ссылку в боте.\n2. В приложении найдите Import profile, Add subscription или Import from URL.\n3. Вставьте ссылку и сохраните профиль.\n4. Запустите обновление подписки, если приложение это поддерживает.\n5. После импорта выберите сервер из списка и подключайтесь.\n\nЕсли подписка не импортируется, можно открыть отдельный серверный ключ и добавить его вручную.',
            'macos': "🍎 macOS\n\n1. Скопируйте общую ссылку из подписки.\n2. Откройте клиент и добавьте подписку через URL.\n3. Вставьте ссылку, сохраните профиль и дождитесь загрузки серверов.\n4. При необходимости обновите подписку вручную внутри клиента.\n5. Выберите удобный сервер и подключайтесь.\n\nЕсли клиент работает только с одиночными конфигами, откройте внутри подписки конкретный серверный ключ.",
        }
        return guides.get(platform_key, guides['windows'])

    async def _render_referral_text(self, user) -> str:
        page = await self.store.get_content("referral")
        ui = await self._ui_snapshot()
        invite_link = self._invite_link(user)
        return "\n".join([
            "🎁 Партнёрская программа",
            "",
            (page.body if page else "Приглашайте друзей и получайте аккуратный возврат с каждой их покупки прямо на внутренний баланс."),
            "",
            f"💸 Вознаграждение: {ui['referral_percent']}% с каждой оплаты реферала",
            f"👥 Приглашено пользователей: {len(user.referrals)}",
            f"💰 Накоплено бонусами: {format_money(user.balance)}",
            "",
            "Ваша персональная ссылка:",
            invite_link,
        ])

    async def _render_trial_text(self, user) -> str:
        page = await self.store.get_content("trial")
        ui = await self._ui_snapshot()
        trial_servers = await self.store.list_balanced_servers(trial_only=True)
        lines = [
            "🧪 Тестовый доступ",
            "",
            (page.body if page else "Короткий пробный период, чтобы оценить скорость, стабильность и удобство подключения до покупки."),
            "",
            f"⏳ Срок доступа: {ui['trial_days']} дн.",
            f"🌐 Доступно trial-серверов: {len(trial_servers)}",
        ]
        if user.trial_claimed:
            lines.extend(["", "Пробный доступ уже был использован для этого аккаунта."])
        else:
            lines.extend(["", "После активации бот создаст подписку и сразу покажет рабочую ссылку с доступными trial-серверами."])
        return "\n".join(lines)

    async def _admin_panel_text(self, actor_role: str = 'owner') -> str:
        metrics = await self.store.get_admin_metrics()
        update_status = await self.updater.get_status()
        role_badge = self._admin_role_badge(actor_role)
        role_title = self._admin_role_title(actor_role)
        commit_line = (update_status.latest_commit_message or '???????? ??????? ??????????.').splitlines()[0].strip()
        lines = [
            '?? ????? ??????????',
            '',
            f'???? ???????: {role_badge} {role_title}',
            '',
            '????? ???????:',
            f'?? ?????????????: {metrics.get("users", 0)}',
            f'?? ???????? ??????: {metrics.get("active_users", 0)}',
            f'? ????????? ??????: {metrics.get("pending_payments", 0)}',
            f'??? ???????? ? ????: {metrics.get("servers", 0)}',
            f'??? ???????????????: {metrics.get("admins", 0)}',
            f'?? ????? ?????? ?? 3 ????: {metrics.get("recent_provisioning_failures", 0)}',
            f'?? ??????: {update_status.current_version}',
        ]
        if update_status.update_available:
            lines.extend([
                '',
                f'?? ???????? ??????????: {update_status.latest_version}',
                f'????????? ??????: {(update_status.latest_revision or "")[:7]} ? {commit_line}',
            ])
        elif update_status.check_error:
            lines.extend(['', f'?? ???????? ??????????: {update_status.check_error}'])
        else:
            lines.extend(['', '? ????? ?????????? ???? ???.'])
        lines.extend(['', '???? ??????? ????????? ??? ????? ???? ??????? ??????????.'])
        return '\n'.join(lines)
    def _render_finance_admin(self, analytics: dict) -> str:
        server_lines = []
        for item in analytics.get('server_costs', [])[:10]:
            next_due = item.get('next_due')
            due_text = next_due.strftime('%d.%m.%Y') if next_due else 'не задана'
            status = str(item.get('status', 'scheduled'))
            if status == 'overdue':
                badge = 'ПРОСРОЧЕНО'
            elif status == 'due_soon':
                badge = 'СКОРО ОПЛАТА'
            else:
                badge = 'ПО ГРАФИКУ'
            extra = ''
            if item.get('days_left') is not None:
                days_left = int(item['days_left'])
                extra = f" | {days_left} дн." if days_left >= 0 else f" | просрочка {abs(days_left)} дн."
            active_users = int(item.get('active_users', 0) or 0)
            cpu_text = format_money(item.get('cost_per_user_rub', Decimal('0.00'))) if active_users > 0 else 'нет активных пользователей'
            server_lines.append(
                f"- {item['server_name']} | {format_money(item['amount_rub'])} / {item['period_days']} дн. | {due_text}{extra} | {badge} | users: {active_users} | unit: {cpu_text}"
            )
        server_lines = server_lines or ['- Оплаты серверов пока не настроены.']
        margin = analytics['profit_30d_rub']
        margin_text = 'прибыль' if margin >= 0 else 'убыток'
        forecast_margin = analytics.get('forecast_profit_30d_rub', Decimal('0.00'))
        forecast_text = 'в плюс' if forecast_margin >= Decimal('0.00') else 'в минус'
        renewals = analytics.get('renewals_to_break_even')
        renewals_text = 'нет данных' if renewals is None else str(renewals)
        expensive = analytics.get('most_expensive_server_per_user')
        expensive_line = 'Самый дорогой сервер на 1 активного пользователя пока не определён.'
        if expensive:
            expensive_line = (
                f"🏷️ Самый дорогой сервер на 1 активного пользователя: {expensive['server_name']} • "
                f"{format_money(expensive['cost_per_user_rub'])} • активных: {expensive['active_users']}"
            )
        return "\n".join([
            '💰 Финансы',
            '',
            'Сводка за 30 дней:',
            f"💸 Выручка RUB: {format_money(analytics['revenue_30d_rub'])}",
            f"⭐ Выручка Stars: {format_money(analytics['stars_30d'], 'XTR')}",
            f"🖥️ Серверные расходы / мес.: {format_money(analytics['monthly_server_cost_rub'])}",
            f"📈 Итог: {format_money(margin)} | {margin_text}",
            '',
            'Финансовый прогноз:',
            f"🗓️ Выручка RUB за 7 дней: {format_money(analytics['revenue_7d_rub'])}",
            f"🔮 Прогноз выручки на 30 дней: {format_money(analytics['forecast_revenue_30d_rub'])}",
            f"📊 Прогноз итога месяца: {format_money(analytics['forecast_profit_30d_rub'])} | {forecast_text}",
            f"🧷 До безубыточности: {format_money(analytics['gap_to_break_even_rub'])}",
            f"🔁 Нужно продлений по среднему чеку: {renewals_text}",
            expensive_line,
            '',
            'Платёжный календарь серверов:',
            f"🧾 Настроено оплат: {analytics['configured_server_payments']}",
            f"🟡 Скоро оплатить: {analytics['due_soon_server_payments']}",
            f"🔴 Просрочено: {analytics['overdue_server_payments']}",
            '',
            'Ближайшие расходы:',
            *server_lines,
            '',
            'Кнопками ниже можно выгрузить CSV или Excel.',
        ])

    def _render_analytics_admin(self, analytics: dict) -> str:
        method_lines = [
            f"• {self._payment_method_title(item['method'])} / {item['currency']} — {item['count']} оплат, {format_money(item['amount'], item['currency'])}"
            for item in analytics.get('method_breakdown', [])
        ] or ['• За последние 30 дней успешных внешних оплат пока нет.']
        top_tariff_lines = [
            f"• {item['name']} — {item['count']} продаж"
            for item in analytics.get('top_tariffs', [])
        ] or ['• Продаж по тарифам за последние 30 дней пока нет.']
        server_cost_lines = []
        for item in analytics.get('server_costs', [])[:5]:
            due_text = item['next_due'].strftime('%d.%m.%Y') if item.get('next_due') else 'не задана'
            active_users = int(item.get('active_users', 0) or 0)
            cpu_text = format_money(item.get('cost_per_user_rub', Decimal('0.00'))) if active_users > 0 else 'нет активных пользователей'
            server_cost_lines.append(
                f"• {item['server_name']} — {format_money(item['amount_rub'])} / {item['period_days']} дн. • {due_text} • {item['status']} • себестоимость/пользователь: {cpu_text}"
            )
        server_cost_lines = server_cost_lines or ['• Оплаты серверов пока не настроены.']
        forecast_margin = analytics.get('forecast_profit_30d_rub', Decimal('0.00'))
        forecast_state = 'плюс' if forecast_margin >= Decimal('0.00') else 'минус'
        expensive = analytics.get('most_expensive_server_per_user')
        expensive_line = '• Пока недостаточно активных пользователей, чтобы посчитать самый дорогой сервер на 1 пользователя.'
        if expensive:
            expensive_line = (
                f"• {expensive['server_name']} — {format_money(expensive['cost_per_user_rub'])} на 1 активного пользователя "
                f"({expensive['active_users']} активных, расход {format_money(expensive['amount_rub'])})"
            )
        renewals = analytics.get('renewals_to_break_even')
        renewals_text = 'нет данных' if renewals is None else str(renewals)
        return "\n".join([
            '📈 Аналитика',
            '',
            'Воронка и база:',
            f"👥 Всего пользователей: {analytics['total_users']}",
            f"💳 Хоть раз платили: {analytics['paying_users']}",
            f"📐 Конверсия в оплату: {analytics['conversion_percent']}%",
            f"🆕 Новых за 7 дней: {analytics['new_users_7d']}",
            '',
            'Доступ и удержание:',
            f"🟢 Активных пользователей: {analytics['active_users']}",
            f"📦 Активных подписок: {analytics['active_subscriptions']}",
            f"⏳ Заканчиваются за 24 часа: {analytics['expiring_24h']}",
            f"🔴 Закончилось за 7 дней: {analytics['ended_7d']}",
            '',
            'Продажи:',
            f"💸 Оплат за 24 часа: {analytics['paid_24h_count']} • {format_money(analytics['revenue_24h_rub'])}",
            f"🗓️ Выручка RUB за 7 дней: {format_money(analytics['revenue_7d_rub'])}",
            f"🗓️ Оплат за 30 дней: {analytics['paid_30d_count']} • {format_money(analytics['revenue_30d_rub'])}",
            f"⭐ Stars за 30 дней: {format_money(analytics['stars_30d'], 'XTR')}",
            f"🧾 Средний чек RUB за 30 дней: {format_money(analytics['avg_check_rub_30d'])}",
            '',
            'Прогноз и unit-экономика:',
            f"🔮 Прогноз выручки на 30 дней: {format_money(analytics['forecast_revenue_30d_rub'])}",
            f"📊 Прогноз итога месяца: {format_money(analytics['forecast_profit_30d_rub'])} • {forecast_state}",
            f"🧷 До безубыточности: {format_money(analytics['gap_to_break_even_rub'])}",
            f"🔁 Нужно продлений по среднему чеку: {renewals_text}",
            expensive_line,
            '',
            'Экономика серверов:',
            f"🖥️ Серверных оплат настроено: {analytics['configured_server_payments']}",
            f"💳 Расходы на серверы / мес.: {format_money(analytics['monthly_server_cost_rub'])}",
            f"📈 Прибыль / убыток за 30 дней: {format_money(analytics['profit_30d_rub'])}",
            f"⏰ Скоро оплатить серверы: {analytics['due_soon_server_payments']}",
            f"🚨 Просроченных серверных оплат: {analytics['overdue_server_payments']}",
            *server_cost_lines,
            '',
            'Способы оплаты за 30 дней:',
            *method_lines,
            '',
            'Топ тарифов за 30 дней:',
            *top_tariff_lines,
        ])

    async def _send_analytics_export(self, callback: CallbackQuery, analytics: dict, export_kind: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        if export_kind == "csv":
            filename = f"analytics_{timestamp}.csv"
            payload = self._build_analytics_csv_bytes(analytics)
            caption = "📄 Экспорт аналитики в CSV."
        else:
            filename = f"analytics_{timestamp}.xls"
            payload = self._build_analytics_excel_bytes(analytics)
            caption = "📊 Экспорт аналитики для Excel."
        chat_id = callback.message.chat.id if callback.message else callback.from_user.id
        await self.bot.send_document(chat_id, BufferedInputFile(payload, filename=filename), caption=caption)

    def _analytics_summary_rows(self, analytics: dict) -> list[tuple[str, str]]:
        renewals = analytics.get('renewals_to_break_even')
        expensive = analytics.get('most_expensive_server_per_user')
        return [
            ("Всего пользователей", str(analytics["total_users"])),
            ("Платящих пользователей", str(analytics["paying_users"])),
            ("Конверсия в оплату", f"{analytics['conversion_percent']}%"),
            ("Новых за 7 дней", str(analytics["new_users_7d"])),
            ("Активных пользователей", str(analytics["active_users"])),
            ("Активных подписок", str(analytics["active_subscriptions"])),
            ("Заканчиваются за 24 часа", str(analytics["expiring_24h"])),
            ("Закончилось за 7 дней", str(analytics["ended_7d"])),
            ("Оплат за 24 часа", str(analytics["paid_24h_count"])),
            ("Выручка за 24 часа", format_money(analytics["revenue_24h_rub"])),
            ("Выручка за 7 дней", format_money(analytics["revenue_7d_rub"])),
            ("Оплат за 30 дней", str(analytics["paid_30d_count"])),
            ("Выручка за 30 дней", format_money(analytics["revenue_30d_rub"])),
            ("Stars за 30 дней", format_money(analytics["stars_30d"], "XTR")),
            ("Средний чек RUB за 30 дней", format_money(analytics["avg_check_rub_30d"])),
            ("Серверных оплат настроено", str(analytics["configured_server_payments"])),
            ("Расходы на серверы / мес.", format_money(analytics["monthly_server_cost_rub"])),
            ("Прибыль / убыток за 30 дней", format_money(analytics["profit_30d_rub"])),
            ("Прогноз выручки на 30 дней", format_money(analytics["forecast_revenue_30d_rub"])),
            ("Прогноз итога месяца", format_money(analytics["forecast_profit_30d_rub"])),
            ("До безубыточности", format_money(analytics["gap_to_break_even_rub"])),
            ("Нужно продлений по среднему чеку", 'нет данных' if renewals is None else str(renewals)),
            ("Самый дорогой сервер на пользователя", expensive['server_name'] if expensive else 'нет данных'),
            ("Себестоимость самого дорогого сервера", format_money(expensive['cost_per_user_rub']) if expensive else 'нет данных'),
            ("Скоро оплатить серверы", str(analytics["due_soon_server_payments"])),
            ("Просроченных серверных оплат", str(analytics["overdue_server_payments"])),
        ]

    def _build_analytics_csv_bytes(self, analytics: dict) -> bytes:
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, delimiter=";")
        writer.writerow(["Раздел", "Показатель", "Значение"])
        for label, value in self._analytics_summary_rows(analytics):
            writer.writerow(["Сводка", label, value])
        writer.writerow([])
        writer.writerow(["Серверные расходы"])
        writer.writerow(["Сервер", "Сумма / месяц", "Следующая оплата", "Период", "Напоминать за", "Статус"])
        for item in analytics.get("server_costs", []):
            writer.writerow([
                str(item["server_name"]),
                format_money(item["amount_rub"]),
                item["next_due"].strftime("%d.%m.%Y") if item.get("next_due") else "",
                f"{item['period_days']} дн.",
                f"{item['remind_days']} дн.",
                str(item["status"]),
            ])
        writer.writerow([])
        writer.writerow(["Unit-экономика серверов"])
        writer.writerow(["Сервер", "Активных пользователей", "Расход / месяц", "Себестоимость на 1 пользователя", "Статус"])
        for item in analytics.get("server_unit_economics", []):
            writer.writerow([
                str(item["server_name"]),
                str(item.get("active_users", 0)),
                format_money(item["amount_rub"]),
                format_money(item.get("cost_per_user_rub", Decimal("0.00"))),
                str(item["status"]),
            ])
        writer.writerow([])
        writer.writerow(["Способы оплаты за 30 дней"])
        writer.writerow(["Способ", "Валюта", "Количество оплат", "Сумма"])
        for item in analytics.get("method_breakdown", []):
            writer.writerow([
                self._payment_method_title(str(item["method"])),
                str(item["currency"]),
                str(item["count"]),
                format_money(item["amount"], str(item["currency"])),
            ])
        writer.writerow([])
        writer.writerow(["Топ тарифов за 30 дней"])
        writer.writerow(["Тариф", "Продаж"])
        for item in analytics.get("top_tariffs", []):
            writer.writerow([str(item["name"]), str(item["count"])])
        return buffer.getvalue().encode("utf-8-sig")

    def _build_analytics_excel_bytes(self, analytics: dict) -> bytes:
        def table_html(title: str, headers: list[str], rows: list[list[str]]) -> str:
            header_html = "".join(f"<th>{escape(str(cell))}</th>" for cell in headers)
            body_html = "".join(
                "<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in row) + "</tr>"
                for row in rows
            )
            return f"<h3>{escape(title)}</h3><table border=\"1\" cellspacing=\"0\" cellpadding=\"4\"><thead><tr>{header_html}</tr></thead><tbody>{body_html}</tbody></table>"

        summary_rows = [[label, value] for label, value in self._analytics_summary_rows(analytics)]
        method_rows = [
            [
                self._payment_method_title(str(item["method"])),
                str(item["currency"]),
                str(item["count"]),
                format_money(item["amount"], str(item["currency"])),
            ]
            for item in analytics.get("method_breakdown", [])
        ] or [["Нет данных", "-", "0", "0"]]
        tariff_rows = [
            [str(item["name"]), str(item["count"])]
            for item in analytics.get("top_tariffs", [])
        ] or [["Нет данных", "0"]]
        cost_rows = [
            [
                str(item["server_name"]),
                format_money(item["amount_rub"]),
                item["next_due"].strftime("%d.%m.%Y") if item.get("next_due") else "",
                f"{item['period_days']} дн.",
                f"{item['remind_days']} дн.",
                str(item["status"]),
            ]
            for item in analytics.get("server_costs", [])
        ] or [["Нет данных", "0 ₽", "", "", "", "not_configured"]]
        unit_rows = [
            [
                str(item["server_name"]),
                str(item.get("active_users", 0)),
                format_money(item["amount_rub"]),
                format_money(item.get("cost_per_user_rub", Decimal("0.00"))),
                str(item["status"]),
            ]
            for item in analytics.get("server_unit_economics", [])
        ] or [["Нет данных", "0", "0 ₽", "0 ₽", "not_configured"]]
        html = "".join([
            "<html><head><meta charset=\"utf-8\"></head><body>",
            f"<h2>Экспорт аналитики MyAir</h2><p>Сформировано: {escape(datetime.now().strftime('%d.%m.%Y %H:%M'))}</p>",
            table_html("Сводка", ["Показатель", "Значение"], summary_rows),
            table_html("Серверные расходы", ["Сервер", "Сумма / месяц", "Следующая оплата", "Период", "Напоминать за", "Статус"], cost_rows),
            table_html("Unit-экономика серверов", ["Сервер", "Активных пользователей", "Расход / месяц", "Себестоимость на 1 пользователя", "Статус"], unit_rows),
            table_html("Способы оплаты за 30 дней", ["Способ", "Валюта", "Количество оплат", "Сумма"], method_rows),
            table_html("Топ тарифов за 30 дней", ["Тариф", "Продаж"], tariff_rows),
            "</body></html>",
        ])
        return html.encode("utf-8-sig")

    async def _reserve_access_enabled(self) -> bool:
        return await self.store.get_toggle('section_reserve_access', default=True)

    async def _reserve_access_url(self, user) -> str:
        if not user:
            return ''
        if not await self._reserve_access_enabled():
            return ''
        return build_reserve_access_url(user)

    def _render_reserve_admin(self, visible: bool) -> str:
        public_url = (settings.public_base_url or '').strip() or 'не настроен'
        state = 'показан пользователям' if visible else 'скрыт от пользователей'
        return "\n".join([
            '🌍 Резервный доступ',
            '',
            f'Статус раздела: {state}',
            f'PUBLIC_BASE_URL: {public_url}',
            '',
            'Что делает функция:',
            '• показывает аварийную ссылку вне Telegram;',
            '• открывает веб-кабинет по IP или домену;',
            '• позволяет пользователю открыть подписку и перевыпустить ключ.',
            '',
            'Если раздел скрыт, резервные ссылки не показываются в боте, а страница /access/... перестаёт открываться.',
        ])

    async def _render_profile(self, user, reserve_url: str | None = None) -> str:
        page = await self.store.get_content("profile")
        intro = page.body if page and page.body else "Здесь собраны ваши активные подписки, ключи доступа, общая ссылка и баланс."
        subscriptions = self._profile_subscriptions(user)
        active = [sub for sub in subscriptions if self._is_subscription_active(sub)]
        archived = [sub for sub in subscriptions if not self._is_subscription_active(sub)]
        total_keys = sum(len(getattr(sub, 'keys', []) or []) for sub in subscriptions)
        active_keys = sum(1 for sub in subscriptions for key in getattr(sub, 'keys', []) or [] if self._is_key_alive(key, sub))
        expired_keys = max(total_keys - active_keys, 0)
        lines = [
            '👤 Личный кабинет',
            '',
            intro,
            '',
            f'💰 Баланс: {format_money(user.balance)}',
            f'📦 Подписок: {len(subscriptions)}',
            f'🟢 Рабочих ключей: {active_keys}',
            f'🔴 Архивных ключей: {expired_keys}',
        ]
        if reserve_url:
            lines.extend(['', '🌍 Резервный доступ уже подготовлен и доступен внутри активных подписок.'])
        if active:
            lines.extend(['', 'Активные подписки:'])
            for subscription in active:
                lines.append(f"{self._subscription_state_icon(subscription)} {self._subscription_title(subscription)} • до {subscription.ends_at:%d.%m}")
        if archived:
            lines.extend(['', 'Архив:'])
            for subscription in archived[:4]:
                lines.append(f"{self._subscription_state_icon(subscription)} {self._subscription_title(subscription)} • истекла {subscription.ends_at:%d.%m}")
            if len(archived) > 4:
                lines.append(f"… и ещё {len(archived) - 4}")
        if subscriptions:
            lines.extend(['', 'Откройте нужную подписку кнопкой ниже, чтобы скопировать общую ссылку, открыть конкретный ключ, продлить доступ или удалить истёкший элемент.'])
        else:
            lines.extend(['', 'Активных подписок пока нет. Оформите новый доступ через раздел покупки.'])
        return "\n".join(lines)

    def _render_subscription_block(self, subscription, index: int, admin_view: bool = False) -> list[str]:
        server_names = subscription_server_names(subscription)
        total_used = sum(int(getattr(key, "used_bytes", 0) or 0) for key in getattr(subscription, "keys", []) or [])
        keys = self._sorted_keys(subscription)
        active_keys = sum(1 for key in keys if self._is_key_alive(key, subscription))
        expired_keys = max(len(keys) - active_keys, 0)
        lines = [
            f"{index}. {self._subscription_state_icon(subscription)} {self._subscription_title(subscription)}",
            f"   ⏳ До: {subscription.ends_at:%d.%m.%Y %H:%M}",
            f"   🔑 Ключи: {active_keys} активных / {expired_keys} архивных",
        ]
        if server_names:
            lines.append(f"   🌐 Серверы: {self._subscription_servers_preview(server_names)}")
        if total_used:
            lines.append(f"   📶 Трафик: {format_gb(total_used)}")
        if admin_view:
            lines.append(f"   📌 Статус: {self._subscription_state_text(subscription)}")
        preview_keys = keys[:3]
        for key in preview_keys:
            lines.append(f"   {self._key_state_icon(key, subscription)} {self._key_server_name(key)}")
        extra = len(keys) - len(preview_keys)
        if extra > 0:
            lines.append(f"   … и ещё {extra} ключ(а)")
        if not keys:
            lines.append("   ⚠️ Ключи ещё не синхронизировались с серверами.")
        return lines

    async def _render_subscription_view(self, subscription, notice: str | None = None, reserve_url: str | None = None) -> str:
        page = await self.store.get_content("subscription_detail")
        intro = page.body if page and page.body else "Здесь видны срок действия, общая ссылка подписки, список серверов и все ключи внутри выбранного доступа."
        server_names = subscription_server_names(subscription)
        total_used = sum(int(getattr(key, "used_bytes", 0) or 0) for key in getattr(subscription, "keys", []) or [])
        active_keys = [key for key in getattr(subscription, "keys", []) or [] if self._is_key_alive(key, subscription)]
        expired_keys = [key for key in getattr(subscription, "keys", []) or [] if not self._is_key_alive(key, subscription)]
        lines = [
            f"{self._subscription_state_icon(subscription)} Карточка подписки",
            '',
            intro,
            '',
            f"📦 Тариф: {self._subscription_title(subscription)}",
            f"📌 Статус: {self._subscription_state_text(subscription)}",
            f"⏳ Действует до: {subscription.ends_at:%d.%m.%Y %H:%M}",
            f"🔑 Ключи: {len(active_keys)} активных / {len(expired_keys)} архивных",
        ]
        if server_names:
            lines.append(f"🌐 Серверы: {self._subscription_servers_preview(server_names, limit=6)}")
        lines.append(f"📶 Общий трафик: {format_gb(total_used)}")
        if notice:
            lines.extend(['', notice])
        lines.extend(['', *self._subscription_link_lines(subscription, reserve_url=reserve_url)])
        if getattr(subscription, 'keys', None):
            lines.extend(['', 'Ключи внутри подписки:'])
            for key in self._sorted_keys(subscription):
                lines.append(f"{self._key_state_icon(key, subscription)} {self._key_server_name(key)} • {self._key_state_text(key, subscription)}")
        return "\n".join(lines)

    async def _render_activation_result(self, subscription, vpn_keys: list, is_trial: bool = False, manual: bool = False, extended: bool = False, reserve_url: str | None = None) -> str:
        page = await self.store.get_content("activation_result")
        intro = page.body if page and page.body else "После оплаты, продления или пробного доступа бот показывает итоговую выдачу и быстрый переход к подписке."
        if extended:
            title = "🕒 Подписка продлена"
        elif manual:
            title = "🎛️ Доступ выдан вручную"
        else:
            title = "🧪 Пробный доступ активирован" if is_trial else "✅ Доступ активирован"
        server_names = subscription_server_names(subscription)
        active_count = len([key for key in vpn_keys if getattr(key, 'is_active', True)])
        lines = [
            title,
            '',
            intro,
            '',
            f"📦 Тариф: {self._subscription_title(subscription)}",
            f"⏳ Действует до: {subscription.ends_at:%d.%m.%Y %H:%M}",
            f"🌐 Серверов в доступе: {len(server_names) or len(vpn_keys)}",
            f"🔑 Активных ключей: {active_count}",
        ]
        if server_names:
            lines.append(f"🖥️ Серверы: {self._subscription_servers_preview(server_names)}")
        if not vpn_keys and not self._is_subscription_active(subscription):
            lines.extend(['', '⚠️ Подписка создана, но активные ключи пока не подтянулись. Проверьте панели 3x-ui и попробуйте позже.'])
            return "\n".join(lines)
        lines.extend(['', *self._subscription_link_lines(subscription, reserve_url=reserve_url)])
        lines.extend(['', 'Откройте подписку кнопкой ниже, если нужно посмотреть отдельные ключи по серверам или открыть QR-код подписки.'])
        return "\n".join(lines)

    def _render_tariffs_admin(self, tariffs) -> str:
        lines = [
            "📦 Тарифная витрина",
            "",
            "Здесь можно создавать, редактировать, скрывать и при необходимости удалять тарифы.",
            "Нажмите на тариф ниже, чтобы открыть его карточку с полным управлением.",
        ]
        if not tariffs:
            lines.extend(["", "Тарифов пока нет."])
            return "\n".join(lines)
        lines.extend(["", "Список тарифов:"])
        for tariff in tariffs:
            state = "🟢 активен" if tariff.is_active else "⚫ скрыт"
            lines.append(f"• {tariff.name} — {tariff.days} дн. — {format_money(tariff.price_rub)} / {format_money(tariff.price_stars, 'XTR')} — {state}")
        return "\n".join(lines)

    def _render_tariff_card(self, tariff) -> str:
        state = "🟢 Показывается пользователям" if tariff.is_active else "⚫ Скрыт с витрины"
        return "\n".join([
            "📦 Карточка тарифа",
            "",
            f"Название: {tariff.name}",
            f"Срок: {tariff.days} дн.",
            f"Цена RUB: {format_money(tariff.price_rub)}",
            f"Цена Stars: {format_money(tariff.price_stars, 'XTR')}",
            f"Статус: {state}",
            "",
            f"Описание: {tariff.description or '-'}",
        ])

    def _render_payments_admin(self, toggles, payment_config) -> str:
        state_map = {toggle.key: toggle.is_enabled for toggle in toggles}
        yk_ready = bool(payment_config.get("yookassa_shop_id") and payment_config.get("yookassa_secret_key"))
        crypto_ready = bool(payment_config.get("crypto_pay_token"))
        crypto_assets = ", ".join(payment_config.get("crypto_pay_assets") or settings.crypto_assets)
        lines = [
            "💳 Платёжный центр",
            "",
            "Все способы оплаты и их реквизиты теперь настраиваются прямо отсюда.",
            "Сначала включаете метод, затем при необходимости открываете его настройку кнопкой ниже.",
            "",
            "Текущие статусы:",
            f"• 💰 Баланс аккаунта — {'включено' if state_map.get('payment_balance') else 'скрыто'}",
            f"• ⭐ Telegram Stars — {'включено' if state_map.get('payment_stars') else 'скрыто'}",
            f"• 💳 YooKassa — {'включено' if state_map.get('payment_yookassa') else 'скрыто'} • {'готова' if yk_ready else 'нет ключей'}",
            f"• 🪙 Crypto Pay — {'включено' if state_map.get('payment_crypto') else 'скрыто'} • {'готов' if crypto_ready else 'нет токена'}",
            "",
            f"YooKassa return_url: {payment_config.get('yookassa_return_url') or settings.yookassa_return_url}",
            f"Crypto assets: {crypto_assets}",
        ]
        return "\n".join(lines)

    def _render_users_filters_text(self) -> str:
        return "👥 Пользователи\n\nВыберите фильтр, чтобы быстро открыть нужную аудиторию: всех, активных, без подписки, новых или тех, кто ещё ни разу не покупал."

    def _render_users_list_text(self, filter_key: str, total: int, page: int, filter_counts: dict[str, int] | None = None) -> str:
        counts = filter_counts or {}
        return "\n".join([
            "👥 Пользовательская база",
            "",
            f"Активный фильтр: {self._filter_title(filter_key)}",
            f"Найдено в фильтре: {total}",
            f"Страница: {page}",
            "",
            f"Быстрая сводка: все {counts.get('all', 0)} • активные {counts.get('active', 0)} • без доступа {counts.get('inactive', 0)} • новые {counts.get('new', 0)} • без покупок {counts.get('never', 0)}",
            "Нажимайте фильтры ниже — список обновится в этом же окне без перехода в отдельный раздел.",
        ])

    def _render_admin_user(self, user) -> str:
        subscriptions = self._profile_subscriptions(user)
        active_subscriptions = [sub for sub in subscriptions if self._is_subscription_active(sub)]
        archived_subscriptions = [sub for sub in subscriptions if not self._is_subscription_active(sub)]
        total_keys = sum(len(getattr(sub, 'keys', []) or []) for sub in subscriptions)
        active_keys = sum(1 for sub in subscriptions for key in getattr(sub, 'keys', []) or [] if self._is_key_alive(key, sub))
        archived_keys = max(total_keys - active_keys, 0)
        username = f"@{user.username}" if getattr(user, 'username', None) else '?? ??????'
        full_name = (getattr(user, 'full_name', '') or '').strip() or '??? ?????'
        paid_payments = sum(1 for payment in getattr(user, 'payments', []) or [] if getattr(payment, 'status', '') == 'paid')
        role = self._admin_role_value(user)
        lines = [
            '?? ???????? ????????????',
            '',
            f'?? ?????????? ID: {user.id}',
            f'?? Telegram ID: {user.telegram_id}',
            f'?? ???: {full_name}',
            f'?? Username: {username}',
            f"?? ?????? ???????: {'????????????' if getattr(user, 'is_blocked', False) else '???????'}",
            f'??? ????: {self._admin_role_badge(role)} {self._admin_role_title(role)}',
            '',
            f'?? ??????: {format_money(user.balance)}',
            f'?? ????????: {len(subscriptions)}',
            f'?? ???????? ????????: {len(active_subscriptions)}',
            f'?? ???????? ??????: {active_keys}',
            f'??? ???????? ??????: {archived_keys}',
            f'?? ???????? ?????: {paid_payments}',
            f'?? ???????? ???????: {len(getattr(user, "balance_operations", []) or [])}',
            f'?? ?????????: {len(getattr(user, "referrals", []) or [])}',
        ]
        if active_subscriptions:
            lines.extend(['', '???????? ????????:'])
            for idx, subscription in enumerate(active_subscriptions[:3], start=1):
                lines.extend(self._render_subscription_block(subscription, idx, admin_view=True))
        if archived_subscriptions:
            lines.extend(['', '?????:'])
            for idx, subscription in enumerate(archived_subscriptions[:2], start=1):
                lines.extend(self._render_subscription_block(subscription, idx, admin_view=True))
            if len(archived_subscriptions) > 2:
                lines.append(f'? ? ??? {len(archived_subscriptions) - 2} ???????? ????????')
        if not subscriptions:
            lines.extend(['', '? ???????????? ???? ??? ????????. ????? ?????? ?????? ??????? ????.'])
        return '\n'.join(lines)
    def _render_admin_roles(self, admins) -> str:
        lines = ['??? ???? ???????', '', f'??????????????? ? ???????: {len(admins)}', '']
        if not admins:
            lines.append('???? ??? ?????????????? ???????????????. ????????? ???? ?? ???????? ????????????.')
            return '\n'.join(lines)
        for user in admins:
            role = self._admin_role_value(user)
            lines.append(f"{self._admin_role_badge(role)} {self._admin_display_name(user)} ? {self._admin_role_title(role)}")
        lines.extend(['', '???????? ????????, ????? ?????????? ????? ???? ? ??? ????????????? ???????? ??.'])
        return '\n'.join(lines)
    def _roles_markup(self, admins):
        builder = InlineKeyboardBuilder()
        for user in admins:
            role = self._admin_role_value(user)
            builder.row(InlineKeyboardButton(text=f"{self._admin_role_badge(role)} {self._admin_display_name(user)[:32]}", callback_data=f'adm:role:view:{user.id}'))
        builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
        builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
        return builder.as_markup()

    def _render_role_card(self, target_user) -> str:
        role = self._admin_role_value(target_user)
        lines = [
            '??? ???? ??????????????',
            '',
            f'????????????: {self._admin_display_name(target_user)}',
            f'Telegram ID: {target_user.telegram_id}',
            f'??????? ????: {self._admin_role_badge(role)} {self._admin_role_title(role)}',
            '',
            '??? ???? ??? ????:',
            *[f'? {line}' for line in self._role_scope_lines(role)],
        ]
        return '\n'.join(lines)
    def _role_card_markup(self, target_user, actor_role: str, back_callback: str = 'adm:roles'):
        builder = InlineKeyboardBuilder()
        if self._can_edit_role(actor_role, target_user):
            for role in ['user', 'support', 'finance', 'ops', 'admin']:
                if role == self._admin_role_value(target_user):
                    continue
                builder.row(InlineKeyboardButton(text=f'{self._admin_role_badge(role)} {self._admin_role_title(role)}', callback_data=f'adm:role:set:{target_user.id}:{role}'))
        builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data=back_callback))
        builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
        return builder.as_markup()

    def _render_admin_audit(self, logs) -> str:
        lines = ['?? ?????? ????????', '']
        if not logs:
            lines.append('??????? ???? ???.')
            return '\n'.join(lines)
        for item in logs[:20]:
            actor = self._admin_display_name(getattr(item, 'actor', None)) if getattr(item, 'actor', None) else '????????? ????????'
            target = ''
            if getattr(item, 'target_user', None):
                target = f" -> {self._admin_display_name(item.target_user)}"
            elif getattr(item, 'target_server', None):
                target = f" -> {item.target_server.name}"
            lines.append(f"? {item.created_at:%d.%m %H:%M} ? {actor}: {item.description}{target}")
        return '\n'.join(lines)
    def _audit_markup(self):
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text='?? ???????? ??????', callback_data='adm:audit'))
        builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
        builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
        return builder.as_markup()
    def _render_user_diagnostics(self, user, failures) -> str:
        lines = ['?? ??????????? ????????????', '', f'????????????: {self._admin_display_name(user)}', f'Telegram ID: {user.telegram_id}']
        if not failures:
            lines.extend(['', '???????? ?????? ?????? ?? ????? ???????????? ?? ???????.'])
            return '\n'.join(lines)
        lines.extend(['', '????????? ?????? ??????:'])
        for item in failures[:10]:
            server_name = getattr(getattr(item, 'server', None), 'name', None) or getattr(item, 'server_name', None) or '?????? ?? ??????'
            lines.append(f"? {item.created_at:%d.%m %H:%M} ? {server_name} ? {_provisioning_stage_title(getattr(item, 'stage', 'unknown'))} ? {item.error}")
        return '\n'.join(lines)
    def _user_diagnostics_markup(self, user_id: int, filter_key: str, page: int):
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text='?? ???????? ???????????', callback_data=f'adm:user:diag:{user_id}:{filter_key}:{page}'))
        builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data=f'adm:user:{user_id}:{filter_key}:{page}'))
        builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
        return builder.as_markup()
    def _render_server_failures(self, server, failures) -> str:
        lines = ['?? ??????? ????? ???????', '', f'??????: {server.name}', f'??????: {server.base_url}']
        if not failures:
            lines.extend(['', '???????? ?????? ?????? ?? ????? ??????? ?? ???????.'])
            return '\n'.join(lines)
        lines.extend(['', '????????? ????:'])
        for item in failures[:15]:
            lines.append(f"? {item.created_at:%d.%m %H:%M} ? {_provisioning_stage_title(getattr(item, 'stage', 'unknown'))} ? {item.error}")
        return '\n'.join(lines)
    def _server_failures_markup(self, server_id: int):
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text='?? ???????? ???????', callback_data=f'adm:server:failures:{server_id}'))
        builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data=f'adm:server:view:{server_id}'))
        builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
        return builder.as_markup()
    def _render_operations_text(self, operations) -> str:
        lines = ["📜 Операции пользователя", ""]
        if not operations:
            lines.append("Операций пока нет.")
            return "\n".join(lines)
        lines.extend(self._render_operation(item) for item in operations)
        return "\n".join(lines)

    def _render_user_referrals_text(self, referrals) -> str:
        lines = ["👥 Рефералы пользователя", ""]
        if not referrals:
            lines.append("Рефералов пока нет.")
            return "\n".join(lines)
        for ref in referrals:
            title = ref.full_name or (f"@{ref.username}" if ref.username else str(ref.telegram_id))
            lines.append(f"• {title} — баланс {format_money(ref.balance)}")
        return "\n".join(lines)

    def _render_texts_admin(self, group: str, pages) -> str:
        if group == 'buttons':
            return "\n".join([
                '🔘 Кнопки пользовательской части',
                '',
                'Здесь меняются только подписи кнопок, которые видит обычный пользователь.',
                'В каждой карточке уже указано, где именно используется эта кнопка.',
                'Важно: динамические кнопки со сроком подписки, тарифами и серверами редактируются не здесь.',
                '',
                f'Кнопок доступно: {len(pages)}',
            ])
        return "\n".join([
            '📝 Тексты пользовательской части',
            '',
            'Здесь редактируются все основные пользовательские экраны: главное меню, профиль, покупка, справка, рефералы, пробный доступ, инструкции по устройствам, карточка подписки, карточка ключа и экран после выдачи доступа.',
            '',
            f'Разделов доступно: {len(pages)}',
        ])

    def _render_content_edit_prompt(self, page, group: str) -> str:
        if group == 'buttons':
            return "\n".join([
                '🔘 Редактирование кнопки',
                '',
                page.title,
                '',
                'Текущее значение:',
                page.body or '—',
                '',
                'Отправьте новую надпись одним сообщением. Изменение коснётся только пользовательской части бота.',
            ])
        return "\n".join([
            '📝 Редактирование текста',
            '',
            page.title,
            '',
            'Текущий текст:',
            page.body or '—',
            '',
            'Отправьте новый текст одним сообщением. Поддерживаются переносы строк и эмодзи.',
        ])

    def _render_servers_overview(self, servers) -> str:
        if not servers:
            return "🖥️ Серверный парк\n\nСерверов пока нет. Добавьте первую панель 3x-ui кнопкой ниже."
        overall_active_keys = sum(self._server_monitoring(server)["active_keys"] for server in servers)
        overall_expired_keys = sum(self._server_monitoring(server)["archived_keys"] for server in servers)
        enabled = sum(1 for server in servers if server.is_enabled)
        online = sum(1 for server in servers if server.health_status == "online")
        trial_enabled = sum(1 for server in servers if server.is_trial_available)
        active_users = sum(self._server_monitoring(server)["active_subscriptions"] for server in servers)
        agents = sum(1 for server in servers if getattr(server, "agent_configured", False))
        agent_online = sum(1 for server in servers if getattr(server, "agent_online", False))
        monthly_cost = sum((Decimal(str(getattr(server, "billing_amount_rub", Decimal("0.00")))) for server in servers), Decimal("0.00"))
        due_soon = sum(
            1
            for server in servers
            if getattr(server, "billing_configured", False)
            and getattr(server, "billing_days_left", None) is not None
            and getattr(server, "billing_days_left", 0) >= 0
            and getattr(server, "billing_days_left", 0) <= getattr(server, "billing_remind_days", 3)
        )
        overdue = sum(
            1
            for server in servers
            if getattr(server, "billing_configured", False)
            and getattr(server, "billing_days_left", None) is not None
            and getattr(server, "billing_days_left", 0) < 0
        )
        avg_health = round(sum(int(getattr(server, 'health_score', 0) or 0) for server in servers) / len(servers)) if servers else 0
        risk_count = sum(1 for server in servers if int(getattr(server, 'health_score', 0) or 0) < 70)
        lines = [
            "🖥️ Серверный парк",
            "",
            f"🌐 Онлайн панелей: {online} / {len(servers)}",
            f"🟢 В выдаче: {enabled}",
            f"🧪 В trial: {trial_enabled}",
            f"👥 Активных пользователей: {active_users}",
            f"🔑 Активных ключей: {overall_active_keys}",
            f"🗂️ Архивных ключей: {overall_expired_keys}",
            f"🤖 Агентов подключено: {agents} • online: {agent_online}",
            f"🛡️ Средний health score: {avg_health}/100 • под риском: {risk_count}",
            f"💸 Расходы на серверы / мес.: {format_money(monthly_cost)}",
            f"⏰ Скоро оплатить: {due_soon} • 🔴 Просрочено: {overdue}",
        ]
        for server in servers:
            stats = self._server_monitoring(server)
            health = "🟢 online" if server.health_status == "online" else "🔴 offline"
            visibility = "в выдаче" if server.is_enabled else "скрыт"
            trial = "trial on" if server.is_trial_available else "trial off"
            agent_state = "🤖 online" if getattr(server, "agent_online", False) else ("🤖 настроен" if getattr(server, "agent_configured", False) else "➖ без агента")
            if getattr(server, "billing_configured", False) and getattr(server, "billing_next_due", None):
                billing_line = (
                    f"  💳 {format_money(getattr(server, 'billing_amount_rub', Decimal('0.00')))} / {getattr(server, 'billing_period_days', 30)} дн. "
                    f"• оплата {getattr(server, 'billing_next_due').strftime('%d.%m.%Y')} • {getattr(server, 'billing_status', 'не настроена')}"
                )
            else:
                billing_line = "  💳 оплата сервера не настроена"
            unit_cost = "нет активных пользователей"
            if getattr(server, 'cost_per_user_rub', Decimal('0.00')) > Decimal('0.00'):
                unit_cost = format_money(getattr(server, 'cost_per_user_rub', Decimal('0.00')))
            lines.extend([
                "",
                f"• {server.name}",
                f"  {health} • {visibility} • {trial} • {agent_state}",
                f"  🛡️ {getattr(server, 'health_badge_icon', '⚪')} health {getattr(server, 'health_score', 0)}/100 • {getattr(server, 'health_badge_label', 'нет данных')}",
                f"  🧠 CPU {server.cpu_percent}% • 🧮 RAM {server.ram_percent}% • 🔌 inbound {server.inbound_id}",
                f"  🔑 {stats['active_keys']} активных / {stats['archived_keys']} архивных • 👥 {stats['active_subscriptions']} пользователей",
                f"  ♻️ Перевыпусков: {getattr(server, 'reissue_count', 0)} • ⚠️ сбоев выдачи (3ч): {getattr(server, 'recent_failures_count', 0)}",
                f"  💸 Себестоимость / активного пользователя: {unit_cost}",
                billing_line,
                f"  🕒 проверка: {self._format_checked_at(server.last_checked_at)}",
            ])
            if getattr(server, 'last_failure_at', None):
                lines.append(f"  🚨 последний сбой: {self._format_checked_at(getattr(server, 'last_failure_at', None))} • {getattr(server, 'last_failure_stage', 'unknown')}")
            if getattr(server, 'last_failure_error', ''):
                lines.append(f"  ⚠️ выдача: {getattr(server, 'last_failure_error')[:140]}")
            if getattr(server, "agent_uptime", ""):
                lines.append(f"  ⏱ аптайм: {server.agent_uptime} • 💽 диск {getattr(server, 'agent_disk_percent', 0)}%")
            if server.last_error:
                lines.append(f"  ⚠️ панель: {server.last_error[:140]}")
            if getattr(server, "agent_error", ""):
                lines.append(f"  ⚠️ агент: {getattr(server, 'agent_error')[:140]}")
        return "\n".join(lines)

    def _render_server(self, server, notice: str | None = None) -> str:
        stats = self._server_monitoring(server)
        health = "🟢 online" if server.health_status == "online" else "🔴 offline"
        visibility = "включён в выдачу" if server.is_enabled else "скрыт из выдачи"
        trial = "доступен" if server.is_trial_available else "выключен"
        agent_configured = bool(getattr(server, "agent_configured", False))
        agent_online = bool(getattr(server, "agent_online", False))
        services = getattr(server, "agent_services", {}) or {}
        service_line = ", ".join(f"{name}:{state}" for name, state in services.items()) or "нет данных"
        unit_cost = "нет активных пользователей"
        if getattr(server, 'cost_per_user_rub', Decimal('0.00')) > Decimal('0.00'):
            unit_cost = format_money(getattr(server, 'cost_per_user_rub', Decimal('0.00')))
        lines = [
            f"🖥️ Сервер: {server.name}",
            "",
            f"🛡️ Health score: {getattr(server, 'health_badge_icon', '⚪')} {getattr(server, 'health_score', 0)}/100 • {getattr(server, 'health_badge_label', 'нет данных')}",
            f"🌐 Панель: {health}",
            f"🎚️ Режим выдачи: {visibility}",
            f"🧪 Trial: {trial}",
            f"🔌 Inbound ID: {server.inbound_id}",
            f"🔗 URL панели: {server.base_url}",
            f"🧠 CPU: {server.cpu_percent}%",
            f"🧮 RAM: {server.ram_percent}%",
            f"🔑 Активных ключей: {stats['active_keys']}",
            f"🗂️ Архивных ключей: {stats['archived_keys']}",
            f"👥 Активных пользователей: {stats['active_subscriptions']}",
            f"♻️ Перевыпусков ключей: {getattr(server, 'reissue_count', 0)}",
            f"⚠️ Сбоев выдачи за 3 часа: {getattr(server, 'recent_failures_count', 0)}",
            f"💸 Себестоимость на 1 активного пользователя: {unit_cost}",
            (
                f"💸 Расход: {format_money(getattr(server, 'billing_amount_rub', Decimal('0.00')))} / {getattr(server, 'billing_period_days', 30)} дн."
                if getattr(server, 'billing_configured', False)
                else "💸 Расход: не настроен"
            ),
            (
                f"📅 Следующая оплата: {getattr(server, 'billing_next_due').strftime('%d.%m.%Y')} ({getattr(server, 'billing_status', 'не настроена')})"
                if getattr(server, 'billing_configured', False) and getattr(server, 'billing_next_due', None)
                else "📅 Следующая оплата: не настроена"
            ),
            (
                f"🔔 Напоминать за: {getattr(server, 'billing_remind_days', 3)} дн."
                if getattr(server, 'billing_configured', False)
                else "🔔 Напоминания по оплате: выключены"
            ),
            f"🕒 Последняя проверка: {self._format_checked_at(server.last_checked_at)}",
            f"🤖 Ubuntu-agent: {'online' if agent_online else ('настроен' if agent_configured else 'не подключён')}",
        ]
        if getattr(server, 'last_failure_at', None):
            lines.append(f"🚨 Последний сбой выдачи: {self._format_checked_at(getattr(server, 'last_failure_at', None))} • {getattr(server, 'last_failure_stage', 'unknown')}")
        if getattr(server, 'last_failure_error', ''):
            lines.append(f"⚠️ Причина последнего сбоя: {getattr(server, 'last_failure_error')}")
        if agent_configured:
            lines.extend([
                f"⏱ Аптайм: {getattr(server, 'agent_uptime', '-') or '-'}",
                f"📈 Load: {getattr(server, 'agent_load', '-') or '-'}",
                f"💽 Диск: {getattr(server, 'agent_disk_percent', 0)}%",
                f"🧩 Сервисы: {service_line}",
            ])
        if server.last_error:
            lines.append(f"⚠️ Ошибка панели: {server.last_error}")
        if getattr(server, "agent_error", ""):
            lines.append(f"⚠️ Ошибка агента: {server.agent_error}")
        if notice:
            lines.extend(["", notice])
        if not agent_configured:
            lines.extend([
                "",
                "Подсказка: подключите Ubuntu-agent, чтобы видеть uptime/load/disk, запускать рестарт 3x-ui или Xray и быстро реагировать на проблемы.",
            ])
        return "\n".join(lines)

    def _render_server_command_prompt(self, server) -> str:
        return "\n".join([
            f"⌨️ Команда для {server.name}",
            "",
            "Введите команду, которую нужно выполнить на Ubuntu-сервере через agent.",
            "",
            "Примеры:",
            "• systemctl restart x-ui",
            "• systemctl restart xray",
            "• systemctl status x-ui --no-pager",
            "• journalctl -u xray -n 50 --no-pager",
            "• docker ps",
            "",
            "Команда выполнится на сервере и краткий результат вернётся прямо в карточку сервера.",
        ])

    def _render_server_agent_prompt(self, server, agent_cfg: dict[str, str]) -> str:
        current_url = (agent_cfg.get("url") or "").strip() or "не настроен"
        token_mask = self._mask_secret((agent_cfg.get("token") or "").strip())
        return "\n".join([
            f"🤖 Ubuntu-agent для {server.name}",
            "",
            "Отправьте данные в формате:",
            "http://SERVER_IP:8799|TOKEN",
            "",
            "Чтобы отключить агент для этого сервера, отправьте: off",
            "",
            f"Текущий URL: {current_url}",
            f"Текущий токен: {token_mask}",
            "",
            "Готовый установщик лежит в deploy/server_agent: install.sh, systemd unit и README с шагами для Ubuntu.",
        ])

    def _render_server_billing_prompt(self, server, billing_cfg: dict) -> str:
        amount = format_money(billing_cfg.get("amount_rub", Decimal("0.00")))
        next_due = billing_cfg.get("next_due")
        next_due_text = next_due.strftime("%d.%m.%Y") if next_due else "не задана"
        period_days = int(billing_cfg.get("period_days", 30))
        remind_days = int(billing_cfg.get("remind_days", 3))
        return "\n".join([
            f"💳 Оплата сервера {server.name}",
            "",
            "Отправьте данные в формате:",
            "сумма_в_рублях|дата_следующей_оплаты|период_дней|напомнить_за_дней",
            "",
            "Пример:",
            "1490|25.04.2026|30|3",
            "",
            "Чтобы очистить настройку оплаты для этого сервера, отправьте: off",
            "",
            f"Текущая сумма: {amount}",
            f"Следующая оплата: {next_due_text}",
            f"Период: {period_days} дн.",
            f"Напоминать за: {remind_days} дн.",
            "",
            "Подсказка: указывайте сумму в рублёвом эквиваленте, чтобы аналитика могла считать прибыль или убыток за месяц.",
        ])

    def _render_referral_admin(self, percent: int, visible: bool) -> str:
        state = "показан" if visible else "скрыт"
        return f"🎁 Реферальная программа\n\nПроцент вознаграждения: {percent}%\nВидимость для пользователей: {state}"

    async def _render_trial_admin(self) -> str:
        visible = await self.store.get_toggle("section_trial", default=True)
        days = await self.store.get_int_setting("trial_days", settings.trial_default_days)
        servers = await self.store.list_servers_with_monitoring()
        trial_servers = [server.name for server in servers if server.is_trial_available]
        visibility = "показан" if visible else "скрыт"
        servers_text = ", ".join(trial_servers) if trial_servers else "серверы не выбраны"
        return "\n".join([
            "🧪 Настройки trial",
            "",
            f"Срок доступа: {days} дн.",
            f"Раздел для пользователей: {visibility}",
            f"Серверов в trial: {len(trial_servers)}",
            f"Список: {servers_text}",
        ])

    def _render_broadcast_intro(self) -> str:
        return "📣 Рассылка\n\nВыберите аудиторию, затем отправьте текст. Бот разошлёт сообщение всем подходящим пользователям."
    def _active_subscriptions(self, user) -> list:
        return [sub for sub in self._profile_subscriptions(user) if self._is_subscription_active(sub)]

    def _profile_subscriptions(self, user) -> list:
        subscriptions = list(getattr(user, "subscriptions", []) or [])
        visible_subscriptions = [
            sub for sub in subscriptions
            if self._is_subscription_active(sub) or getattr(sub, "keys", [])
        ]
        active = sorted([sub for sub in visible_subscriptions if self._is_subscription_active(sub)], key=lambda item: item.ends_at)
        archived = sorted([sub for sub in visible_subscriptions if not self._is_subscription_active(sub)], key=lambda item: item.ends_at, reverse=True)
        return active + archived

    def _paginate_items(self, items: list, page: int, page_size: int = 10) -> tuple[list, int, int]:
        if page_size <= 0:
            return list(items), 1, 1
        total = len(items)
        if total == 0:
            return [], 1, 1
        total_pages = max((total + page_size - 1) // page_size, 1)
        current_page = min(max(page, 1), total_pages)
        start = (current_page - 1) * page_size
        end = start + page_size
        return list(items[start:end]), current_page, total_pages

    def _subscription_actions(self, subscriptions: Iterable, back_mode: str) -> list[tuple[str, str]]:
        return self._subscription_action_rows(list(subscriptions), back_mode)

    def _subscription_action_rows(self, subscriptions: list, back_mode: str) -> list[tuple[str, str]]:
        actions: list[tuple[str, str]] = []
        for subscription in subscriptions:
            actions.append((self._subscription_button_label(subscription), f"sub:show:{subscription.id}:{back_mode}"))
        return actions

    def _key_action_rows(self, subscription, context: str) -> list[tuple[str, str]]:
        actions: list[tuple[str, str]] = []
        for key in self._sorted_keys(subscription):
            actions.append((self._key_button_label(key, subscription), f"key:show:{key.id}:{subscription.id}:{context}"))
        return actions

    def _subscription_button_label(self, subscription) -> str:
        icon = self._subscription_state_icon(subscription)
        suffix = subscription.ends_at.strftime("%d.%m")
        prefix = "до" if self._is_subscription_active(subscription) else "истекла"
        return f"{icon} {self._subscription_title(subscription)} • {prefix} {suffix}"

    def _key_button_label(self, key, subscription=None) -> str:
        return f"{self._key_state_icon(key, subscription)} {self._key_server_name(key)}"

    def _subscription_link_lines(self, subscription, reserve_url: str | None = None) -> list[str]:
        if not self._is_subscription_active(subscription):
            return [
                "🔴 Срок этой подписки уже закончился.",
                "При необходимости продлите доступ, чтобы снова получить рабочую общую ссылку и активные ключи.",
            ]
        url = build_subscription_url(subscription)
        if url:
            lines = [
                "🌐 Общая ссылка подписки:",
                url,
                "",
                "Добавьте эту ссылку в клиент с поддержкой Subscription URL, чтобы сразу получить все активные серверы.",
                "Если Telegram не даёт быстро скопировать адрес, зажмите ссылку в тексте сообщения.",
            ]
            if reserve_url:
                lines.extend([
                    "",
                    "🌍 Резервный кабинет:",
                    reserve_url,
                    "Сохраните эту ссылку заранее. Через неё можно открыть свой кабинет даже без Telegram и заменить ключ при необходимости.",
                ])
            return lines
        return [
            "⚠️ Общая ссылка пока не сформировалась.",
            "Проверьте настройки PUBLIC_BASE_URL у бота и доступность активных ключей на серверах.",
        ]

    async def _subscription_detail_markup(self, subscription, context: str, viewer_id: int, reserve_url: str | None = None):
        return subscription_detail_keyboard(
            back_callback=self._subscription_back_callback(context),
            key_actions=self._key_action_rows(subscription, context),
            extend_callback=(f"buy:extend:{subscription.id}" if self._subscription_owner_can_extend(viewer_id, subscription) else None),
            qr_callback=(f"qr:sub:{subscription.id}" if self._is_subscription_active(subscription) and build_subscription_url(subscription) else None),
            labels=await self._user_button_labels(),
        )

    async def _key_detail_markup(self, key, subscription_id: int, context: str, viewer_id: int):
        return key_detail_keyboard(
            back_callback=f"sub:show:{subscription_id}:{context}",
            copy_value=(key.access_url if self._can_copy_key(key) else None),
            replace_callback=(f"key:replace:{key.id}:{subscription_id}:{context}" if self._can_replace_key(key, viewer_id) else None),
            delete_callback=(f"key:delete:{key.id}:{subscription_id}:{context}" if self._can_delete_key(key) else None),
            extend_callback=(f"buy:extend:{subscription_id}" if self._subscription_owner_can_extend(viewer_id, key.subscription) else None),
            qr_callback=(f"qr:key:{key.id}:{subscription_id}" if (getattr(key, 'access_url', '') or '').strip() and not str(getattr(key, 'access_url', '')).startswith('legacy-import://') else None),
            labels=await self._user_button_labels(),
        )

    def _subscription_back_callback(self, context: str) -> str:
        if context.startswith("adminuser:"):
            parts = context.split(":")
            if len(parts) >= 4:
                return f"adm:user:{parts[1]}:{parts[2]}:{parts[3]}"
        if context.startswith("profile"):
            parts = context.split(":")
            if len(parts) >= 2:
                try:
                    page = max(int(parts[1]), 1)
                except ValueError:
                    page = 1
                if page > 1:
                    return f"nav:profile:{page}"
            return "nav:profile"
        return "nav:home"

    def _subscription_owner_can_extend(self, viewer_id: int, subscription) -> bool:
        owner_id = getattr(getattr(subscription, "user", None), "telegram_id", None)
        return viewer_id == owner_id and self._can_extend_subscription(subscription)

    def _can_extend_subscription(self, subscription) -> bool:
        return bool(subscription and not getattr(subscription, "is_trial", False) and self._is_subscription_active(subscription))

    def _can_replace_key(self, key, viewer_id: int) -> bool:
        subscription = getattr(key, "subscription", None)
        owner_id = getattr(getattr(subscription, "user", None), "telegram_id", None)
        owner_allowed = viewer_id == owner_id or viewer_id in settings.admin_ids
        is_active_subscription = bool(subscription and self._is_subscription_active(subscription))
        return owner_allowed and is_active_subscription and (bool(getattr(key, "is_active", False)) or self._needs_key_reissue(key))

    def _can_delete_key(self, key) -> bool:
        subscription = getattr(key, "subscription", None)
        return bool(key and subscription and not self._is_subscription_active(subscription))

    def _can_copy_key(self, key) -> bool:
        value = (key.access_url or "").strip()
        return bool(value and not value.startswith("legacy-import://") and len(value) <= 256)

    def _needs_key_reissue(self, key) -> bool:
        access_url = (getattr(key, "access_url", "") or "").strip()
        label = (getattr(key, "label", "") or "").lower()
        return access_url.startswith("legacy-import://") or "перевыпуск" in label

    def _sorted_keys(self, subscription) -> list:
        keys = list(getattr(subscription, "keys", []) or [])
        return sorted(keys, key=lambda item: (0 if self._is_key_alive(item, subscription) else 1, self._key_server_name(item).lower()))

    def _is_subscription_active(self, subscription) -> bool:
        return bool(subscription and subscription.status == "active" and is_future_datetime(getattr(subscription, "ends_at", None)))

    def _is_key_alive(self, key, subscription=None) -> bool:
        linked_subscription = subscription or getattr(key, "__dict__", {}).get("subscription")
        return bool(key and getattr(key, "is_active", False) and linked_subscription and self._is_subscription_active(linked_subscription))

    def _subscription_state_icon(self, subscription) -> str:
        return "🟢" if self._is_subscription_active(subscription) else "🔴"

    def _subscription_state_text(self, subscription) -> str:
        return "Активна" if self._is_subscription_active(subscription) else "Завершена"

    def _key_state_icon(self, key, subscription=None) -> str:
        return "🟢" if self._is_key_alive(key, subscription) else "🔴"

    def _key_state_text(self, key, subscription=None) -> str:
        linked_subscription = subscription or getattr(key, "__dict__", {}).get("subscription")
        if self._is_key_alive(key, linked_subscription):
            return "Рабочий"
        if self._needs_key_reissue(key):
            return "Нужен перевыпуск"
        if linked_subscription and self._is_subscription_active(linked_subscription):
            return "Заменён или отключён"
        return "Срок истёк"

    def _server_monitoring(self, server) -> dict[str, int]:
        active_keys = 0
        archived_keys = 0
        active_subscriptions: set[int] = set()
        for key in getattr(server, "keys", []) or []:
            if self._is_key_alive(key, getattr(getattr(key, "__dict__", {}), "get", lambda *_: None)("subscription")):
                active_keys += 1
                subscription = getattr(key, "subscription", None)
                if subscription and getattr(subscription, "id", None) is not None:
                    active_subscriptions.add(subscription.id)
            else:
                archived_keys += 1
        return {
            "active_keys": active_keys,
            "archived_keys": archived_keys,
            "active_subscriptions": len(active_subscriptions),
        }

    def _prepare_server_monitoring(self, servers) -> list:
        for server in servers:
            stats = self._server_monitoring(server)
            setattr(server, "active_keys_count", stats["active_keys"])
            setattr(server, "expired_keys_count", stats["archived_keys"])
            setattr(server, "active_subscriptions_count", stats["active_subscriptions"])
        return servers

    def _format_checked_at(self, value) -> str:
        if not value:
            return "ещё не проверялся"
        return value.strftime("%d.%m.%Y %H:%M")

    def _mask_secret(self, value: str, visible: int = 4) -> str:
        raw = (value or "").strip()
        if not raw:
            return "не задан"
        if len(raw) <= visible:
            return "•" * len(raw)
        return ("•" * max(len(raw) - visible, 3)) + raw[-visible:]


    def _render_admin_guide(self, section_key: str = "start") -> str:
        pages = {
            "start": "\n".join([
                "📚 База знаний админки",
                "",
                "Этот раздел встроен прямо в админ-панель и должен обновляться вместе с функционалом бота.",
                "",
                "Как пользоваться админкой:",
                "• открывайте нужный раздел кнопками ниже;",
                "• если бот просит данные, просто отправьте их обычным сообщением в чат;",
                "• после сохранения бот вернёт вас в тот же admin-экран;",
                "• кнопка Назад возвращает на предыдущий admin-экран, а Главное меню — из админки обратно в бот.",
                "",
                "Что смотреть в первую очередь:",
                "• Пользователи — база, фильтры, баланс, ручная выдача доступа;",
                "• Серверы — панели 3x-ui, мониторинг, trial и Ubuntu-agent;",
                "• Тарифы — создание, редактирование, скрытие и удаление;",
                "• Оплаты — включение методов и настройка YooKassa/Crypto Pay;",
                "• Аналитика — конверсия, выручка, топ тарифов и способы оплаты;",
                "• Сервис — рассылка, backup, автоочистка, обновления и резервный доступ по IP.",
            ]),
            "users": "\n".join([
                "👥 Пользователи",
                "",
                "Путь: Админ-панель -> Пользователи",
                "",
                "Как это работает:",
                "• сверху всегда видны live-фильтры;",
                "• при нажатии фильтра список в этом же сообщении сразу перестраивается;",
                "• можно листать страницы и тут же менять фильтр без выхода назад.",
                "",
                "Фильтры:",
                "• Все — вся база;",
                "• Активные — есть активная подписка;",
                "• Без доступа — нет активной подписки;",
                "• Новые — зарегистрировались за последние 7 дней;",
                "• Без покупок — ни разу не покупали.",
                "",
                "В карточке пользователя:",
                "• Выдать баланс — отправьте сумму сообщением;",
                "• Выдать доступ — отправьте количество дней;",
                "• Операции — история движения баланса;",
                "• Рефералы — список приглашённых;",
                "• Заблокировать/Разблокировать — меняет доступ к боту.",
            ]),
            "servers": "\n".join([
                "🖥️ Серверы",
                "",
                "Путь: Админ-панель -> Серверы",
                "",
                "Как добавить сервер 3x-ui:",
                "• нажмите Добавить сервер;",
                "• отправьте одну строку формата:",
                "Название|https://panel.example.com:port/path|username|password|inbound_id",
                "",
                "Важно:",
                "• inbound_id — это ID из раздела Inbounds в 3x-ui;",
                "• это не порт панели;",
                "• URL можно указывать сразу с секретным путём панели.",
                "",
                "Что можно в карточке сервера:",
                "• проверить один сервер или все сразу;",
                "• включить/скрыть сервер из выдачи;",
                "• включить/выключить trial на сервере;",
                "• обновить трафик ключей;",
                "• удалить сервер, если к нему ещё не привязаны данные.",
                "",
                "Ubuntu-agent:",
                "• нужен для uptime/load/disk/services и удалённых команд;",
                "• готовые файлы лежат в deploy/server_agent;",
                "• после установки отправьте в боте строку вида http://SERVER_IP:8799|TOKEN;",
                "• после подключения доступны рестарт 3x-ui, рестарт Xray и своя команда.",
            ]),
            "tariffs": "\n".join([
                "📦 Тарифы",
                "",
                "Путь: Админ-панель -> Тарифы",
                "",
                "Создание и редактирование:",
                "• нажмите Создать тариф или откройте существующий и нажмите Редактировать;",
                "• отправьте данные форматом:",
                "Название|дни|цена RUB|цена Stars|описание",
                "",
                "Пример:",
                "Месяц|30|199.00|150|Доступ на 30 дней",
                "",
                "Что можно делать с карточкой тарифа:",
                "• редактировать;",
                "• скрывать/показывать на витрине;",
                "• удалять, если тариф ещё не участвовал в оплатах или подписках.",
            ]),
            "payments": "\n".join([
                "💳 Оплаты",
                "",
                "Путь: Админ-панель -> Оплаты",
                "",
                "Здесь настраивается сразу две вещи:",
                "• видимость метода для пользователей;",
                "• реквизиты метода прямо из админки.",
                "",
                "YooKassa:",
                "• нажмите кнопку настройки YooKassa;",
                "• отправьте: shop_id|secret_key|return_url;",
                "• чтобы отключить и очистить данные, отправьте off.",
                "",
                "Crypto Pay:",
                "• нажмите кнопку настройки Crypto;",
                "• отправьте: token|testnet(true/false)|USDT,TON,BTC;",
                "• чтобы отключить и очистить данные, отправьте off.",
                "",
                "Важно:",
                "• даже если ключи заполнены, метод должен быть ещё и включён тумблером;",
                "• Stars и Баланс дополнительных ключей не требуют.",
            ]),
            "finance": "\n".join([
                "💰 Финансы",
                "",
                "Путь: Админ-панель -> Финансы",
                "",
                "Что показывает раздел:",
                "• выручку за 30 дней в RUB и Stars;",
                "• ежемесячные расходы на серверы;",
                "• итоговую прибыль или убыток за то же окно 30 дней;",
                "• календарь ближайших и просроченных серверных оплат.",
                "",
                "Как использовать:",
                "• в карточке сервера задайте сумму, дату следующей оплаты, период и за сколько дней напоминать;",
                "• после оплаты откройте сервер и нажмите «Отметить оплату»;",
                "• здесь удобно быстро смотреть экономику проекта и ближайшие обязательные расходы.",
            ]),
            "analytics": "\n".join([
                "📈 Аналитика",
                "",
                "Путь: Админ-панель -> Аналитика",
                "",
                "Что показывает раздел:",
                "• размер базы и сколько пользователей уже платили;",
                "• активную аудиторию и сколько подписок скоро закончится;",
                "• выручку за 24 часа и 30 дней;",
                "• средний чек, топ тарифов и разрез по способам оплаты.",
                "",
                "Практическое применение:",
                "• смотреть, какие тарифы реально продаются;",
                "• понимать, какой способ оплаты приносит деньги;",
                "• отслеживать просадку продлений и рост новых пользователей.",
            ]),
            "texts": "\n".join([
                "📝 Тексты и витрина",
                "",
                "Путь: Админ-панель -> Тексты",
                "",
                "Здесь редактируются тексты пользовательских разделов: главное меню, покупка, справка, профиль, рефералы, trial.",
                "",
                "Как редактировать:",
                "• откройте нужный раздел;",
                "• отправьте новый текст обычным сообщением;",
                "• бот сохранит его и вернёт вас к списку разделов.",
                "",
                "Примечание:",
                "• этот раздел влияет на пользовательскую витрину;",
                "• база знаний Инструкции — отдельный admin-раздел и вручную отсюда не редактируется.",
            ]),
            "programs": "\n".join([
                "🎁 Рефералы и Trial",
                "",
                "Рефералы:",
                "• путь: Админ-панель -> Рефералы;",
                "• можно изменить процент;",
                "• можно показать или скрыть раздел для пользователей.",
                "",
                "Trial:",
                "• путь: Админ-панель -> Trial;",
                "• можно задать срок в днях;",
                "• можно показать или скрыть раздел для пользователей;",
                "• сами trial-серверы включаются в разделе Серверы через кнопку Trial on/off на конкретном сервере.",
            ]),
            "service": "\n".join([
                "🗄️ Сервис: рассылка, backup, автоочистка, обновления",
                "",
                "Рассылка:",
                "• выберите аудиторию;",
                "• потом отправьте текст сообщением;",
                "• бот разошлёт его подходящим пользователям.",
                "",
                "Backup:",
                "• ежедневные backup отправляются администраторам автоматически;",
                "• кнопка в разделе создаёт backup сразу вручную.",
                "",
                "Автоочистка:",
                "• бот сам архивирует старые pending-оплаты, отключает истёкшие доступы и чистит старые логи/backup;",
                "• это уменьшает мусор в базе и поддерживает быстрый отклик.",
                "",
                "Резервный доступ по IP:",
                "• работает без обязательного домена, если PUBLIC_BASE_URL указывает на внешний IP бота;",
                "• пользователь получает аварийную ссылку /access/... и может открыть кабинет даже вне Telegram;",
                "• из резервного кабинета можно открыть общую подписку и перевыпустить ключ.",
                "",
                "Обновления:",
                "• раздел показывает текущую версию и статус trigger-механизма;",
                "• если серверная схема обновления настроена, можно обновить бота прямо кнопкой.",
                "",
                "Рекомендованный порядок первичной настройки:",
                "1. Добавить серверы.",
                "2. Проверить их статус.",
                "3. Настроить оплаты.",
                "4. Создать тарифы.",
                "5. Проверить одного тестового пользователя.",
            ]),
        }
        return pages.get(section_key, pages["start"])

    def _render_updates_admin(self, status, result_text: str | None = None, error_text: str | None = None) -> str:
        short_current = status.current_revision[:7] if status.current_revision else '?'
        short_latest = status.latest_revision[:7] if status.latest_revision else '?'
        commit_line = (status.latest_commit_message or '???????? ??????? ??????????.').splitlines()[0].strip()
        lines = [
            '?? ??????????',
            '',
            f'??????? ??????: {status.current_version}',
            f'??????? ???????: {short_current}',
            f'?????: {status.image_name or "?? ??????"}',
            f'GitHub: {status.repository or "?? ????????"}',
            f'??????? ??????????: {"?????????" if status.trigger_configured else "?? ????????"}',
            '',
            f'???????? ??????????: {1 if status.update_available else 0}',
        ]
        if status.update_available:
            lines.extend(['', '????????? ?????????:', f'`{short_latest}` ?????? {status.latest_version}: {commit_line}'])
        elif status.check_error:
            lines.extend(['', f'?? ?????? ???????? GitHub: {status.check_error}'])
        else:
            lines.extend(['', '? ????? ?????? ?? ???????.'])
        if result_text:
            lines.extend(['', f'? {result_text}'])
        if error_text:
            lines.extend(['', f'?? {error_text}'])
        if not status.trigger_configured:
            lines.extend(['', '??? ?????? ?????????? ??????? UPDATE_TRIGGER_URL ? UPDATE_TRIGGER_TOKEN ? ?????????.'])
        return '\n'.join(lines)
    async def _render_key_view(self, key, notice: str | None = None) -> str:
        page = await self.store.get_content("key_detail")
        intro = page.body if page and page.body else "Здесь можно быстро забрать адрес ключа, показать QR и при необходимости заменить нерабочий доступ."
        subscription = key.subscription
        lines = [
            f"{self._key_state_icon(key, subscription)} Ключ сервера",
            '',
            intro,
            '',
            f"🌐 Сервер: {self._key_server_name(key)}",
            f"📦 Подписка: {self._subscription_title(subscription)}" if subscription else "📦 Подписка: -",
            f"📌 Статус: {self._key_state_text(key, subscription)}",
            f"⏳ Действует до: {subscription.ends_at:%d.%m.%Y %H:%M}" if subscription else "⏳ Действует до: -",
            f"📶 Использовано: {format_gb(key.used_bytes)}",
        ]
        if notice:
            lines.extend(['', notice])
        if self._is_key_alive(key, subscription):
            if len((key.access_url or '').strip()) > 256:
                lines.extend(['', key.access_url, '', 'Ключ получился длинным, поэтому Telegram показывает его целиком в тексте. Скопируйте его вручную или используйте QR ниже.'])
            else:
                lines.extend(['', 'Нажмите на адрес в кнопке ниже — ключ скопируется в одно касание.'])
        else:
            if self._needs_key_reissue(key) and subscription and self._is_subscription_active(subscription):
                lines.extend(['', 'Этот ключ импортирован из старой базы без готовой рабочей ссылки. Нажмите «Заменить ключ», чтобы бот выпустил новый рабочий ключ в текущем формате.'])
            else:
                lines.extend(['', 'Этот ключ уже не работает. После окончания срока его можно удалить кнопкой ниже или продлить саму подписку.'])
        return "\n".join(lines)

    def _key_server_name(self, key) -> str:
        return getattr(getattr(key, "server", None), "name", None) or key.label.split(" / ")[-1]

    def _key_preview(self, value: str, limit: int = 74) -> str:
        text = (value or "").strip()
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def _subscription_title(self, subscription) -> str:
        if getattr(subscription, "is_trial", False):
            return "Пробный доступ"
        if getattr(subscription, "tariff", None):
            return subscription.tariff.name
        return "Ручной доступ"

    def _subscription_servers_preview(self, server_names: list[str], limit: int = 4) -> str:
        if not server_names:
            return "Серверы пока не подтянулись"
        preview = ", ".join(server_names[:limit])
        if len(server_names) > limit:
            preview += f" и ещё {len(server_names) - limit}"
        return preview

    def _render_operation(self, operation) -> str:
        sign = "+" if operation.amount >= 0 else ""
        return f"• {operation.created_at:%d.%m.%Y %H:%M} — {operation.kind} — {sign}{format_money(operation.amount)} — после операции {format_money(operation.balance_after)}"

    def _filter_title(self, filter_key: str) -> str:
        return {
            "all": "Все пользователи",
            "active": "С активной подпиской",
            "inactive": "Без активной подписки",
            "never": "Никогда не покупали",
            "new": "Новые за 7 дней",
        }.get(filter_key, filter_key)

    def _starter_tariff(self, tariffs):
        active = [tariff for tariff in tariffs if getattr(tariff, 'is_active', True)]
        return min(active, key=lambda item: item.days, default=None)

    def _popular_tariff(self, tariffs):
        active = [tariff for tariff in tariffs if getattr(tariff, 'is_active', True)]
        if not active:
            return None
        preferred = [tariff for tariff in active if tariff.days >= 60]
        pool = preferred or active
        return min(pool, key=lambda item: abs(item.days - 90))

    def _best_value_tariff(self, tariffs):
        active = [tariff for tariff in tariffs if getattr(tariff, 'is_active', True) and tariff.price_rub]
        if not active:
            return None
        return min(active, key=lambda item: self._tariff_monthly_rub(item))

    def _tariff_monthly_rub(self, tariff) -> Decimal:
        if not tariff or not tariff.days:
            return Decimal('0.00')
        return (Decimal(str(tariff.price_rub or 0)) * Decimal('30') / Decimal(str(tariff.days))).quantize(Decimal('0.01'))

    def _tariff_upsell_lines(self, current_tariff, tariffs) -> list[str]:
        if not current_tariff:
            return []
        better_candidates = [
            tariff for tariff in tariffs
            if tariff.id != current_tariff.id and tariff.days > current_tariff.days and self._tariff_monthly_rub(tariff) < self._tariff_monthly_rub(current_tariff)
        ]
        lines: list[str] = []
        if better_candidates:
            best = min(better_candidates, key=lambda item: self._tariff_monthly_rub(item))
            lines.append(
                f"💡 Если нужен более выгодный запас по цене, {best.name} выходит по {format_money(self._tariff_monthly_rub(best))} / 30 дн. вместо {format_money(self._tariff_monthly_rub(current_tariff))}."
            )
        popular = self._popular_tariff(tariffs)
        if popular and popular.id != current_tariff.id and popular.days > current_tariff.days:
            lines.append(f"🔥 Чаще всего берут {popular.name}: дольше срок и меньше возни с продлением.")
        return lines[:2]

    def _render_tariff_lines(self, tariffs) -> list[str]:
        lines: list[str] = []
        starter = self._starter_tariff(tariffs)
        popular = self._popular_tariff(tariffs)
        best_value = self._best_value_tariff(tariffs)
        for tariff in tariffs:
            prices: list[str] = []
            if tariff.price_stars:
                prices.append(format_money(tariff.price_stars, "XTR"))
            if tariff.price_rub:
                prices.append(format_money(tariff.price_rub))
            badges: list[str] = []
            if starter and tariff.id == starter.id:
                badges.append('🚀')
            if popular and tariff.id == popular.id:
                badges.append('🔥')
            if best_value and tariff.id == best_value.id:
                badges.append('💎')
            badge_prefix = ''.join(badges) + ' ' if badges else '• '
            lines.append(f"{badge_prefix}{tariff.name} — {tariff.days} дн. — {' / '.join(prices)}")
        return lines
    def _invite_link(self, user) -> str:
        username = settings.bot_username.strip().replace("@", "")
        if not username or username == "your_myair_bot":
            return user.invite_code
        return f"https://t.me/{username}?start={user.invite_code}"

    def _brand_name(self) -> str:
        return "MyAir"

    def _payment_method_title(self, method: str) -> str:
        return {
            "stars": "Telegram Stars",
            "yookassa": "YooKassa",
            "crypto": "Crypto",
            "balance": "Баланс аккаунта",
        }.get(method, method)
