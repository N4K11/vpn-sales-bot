from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from math import ceil

from aiogram.types import CallbackQuery, InlineKeyboardButton, LinkPreviewOptions, Message, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder

import app.bot.keyboards as kb
import app.bot.controller as controller_module
from app.bot.controller import BotController
from app.bot.states import PromoCodeState
from app.config import settings
from app.utils import format_money

BROKEN_MARKERS = ("Р ", "РЎ", "РІР‚", "Гђ", "Г‘", "пїЅ")
DEFAULT_LABELS = dict(kb.DEFAULT_USER_BUTTON_LABELS)


def repair_text(value):
    if value is None:
        return value
    text = str(value)
    for _ in range(3):
        try:
            candidate = text.encode("cp1251").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if candidate == text:
            break
        text = candidate
    return text


def looks_broken_text(value) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    if any(marker in text for marker in BROKEN_MARKERS):
        return True
    letters = [ch for ch in text if ch.isalpha()]
    return bool(letters) and text.count("?") >= max(3, len(letters) // 3)


def safe_text(value, fallback: str = "") -> str:
    text = repair_text(value or "")
    if not text or looks_broken_text(text):
        return fallback
    return text


def display_user(user) -> str:
    username = (getattr(user, "username", "") or "").strip()
    if username:
        return f"@{username}"
    full_name = (getattr(user, "full_name", "") or "").strip()
    if full_name:
        return full_name
    telegram_id = getattr(user, "telegram_id", None)
    return str(telegram_id) if telegram_id else "пользователь"


def admin_role_title(role: str) -> str:
    return {
        "owner": "Владелец",
        "admin": "Администратор",
        "support": "Поддержка",
        "finance": "Финансы",
        "ops": "Серверы",
        "user": "Пользователь",
    }.get((role or "user").strip().lower(), role or "Пользователь")


def tariff_title(tariff) -> str:
    return safe_text(getattr(tariff, "name", None), f"Тариф #{getattr(tariff, 'id', '')}")


def server_title(server) -> str:
    return safe_text(getattr(server, "name", None), f"Сервер #{getattr(server, 'id', '')}")


_ORIGINAL_SAFE_EDIT = BotController._safe_edit_message_text
_ORIGINAL_SAFE_ANSWER = BotController._safe_answer_callback
_ORIGINAL_HANDLE_NAV = BotController.handle_nav_callbacks
_ORIGINAL_HANDLE_BUY = BotController.handle_buy_callbacks
_ORIGINAL_HANDLE_ADMIN = BotController.handle_admin_callbacks
_ORIGINAL_UI_SNAPSHOT = BotController._ui_snapshot


async def _patched_safe_edit_message_text(self, message, text, reply_markup=None):
    await _ORIGINAL_SAFE_EDIT(self, message, repair_text(text), reply_markup)


async def _patched_safe_answer_callback(self, callback, text=None, show_alert: bool = False):
    await _ORIGINAL_SAFE_ANSWER(self, callback, repair_text(text), show_alert)


async def _patched_send_inline_screen(self, message: Message, text: str, reply_markup) -> None:
    cleanup = await message.answer("\u2060", reply_markup=ReplyKeyboardRemove())
    try:
        await cleanup.edit_text(
            repair_text(text),
            reply_markup=reply_markup,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except Exception:
        await message.answer(
            repair_text(text),
            reply_markup=reply_markup,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )


async def _patched_ui_snapshot(self):
    ui = await _ORIGINAL_UI_SNAPSHOT(self)
    labels = dict(DEFAULT_LABELS)
    for key, value in (ui.get("button_labels") or {}).items():
        cleaned = safe_text(value, labels.get(key, ""))
        if cleaned:
            labels[key] = cleaned
    ui["button_labels"] = labels
    return ui


async def _patched_user_button_labels(self, ui: dict | None = None):
    snapshot = ui or await self._ui_snapshot()
    return dict(snapshot.get("button_labels") or DEFAULT_LABELS)


async def _render_home_text(self):
    page = await self.store.get_content("main")
    body = safe_text(getattr(page, "body", None))
    if body:
        return body
    return "MyAir\n\nВыберите нужный раздел ниже."


async def _render_buy_text(self, tariffs):
    page = await self.store.get_content("buy")
    body = safe_text(getattr(page, "body", None))
    if body:
        return body
    if not tariffs:
        return "Подключить Air сейчас нельзя: нет доступных тарифов."
    lines = ["Подключить Air", "", "Выберите тариф, затем удобный способ оплаты.", "", "Доступные планы:"]
    for tariff in tariffs:
        lines.append(f"• {tariff_title(tariff)} — {int(getattr(tariff, 'days', 0) or 0)} дн.")
    lines.extend(["", "Промокод можно применить отдельной кнопкой ниже."])
    return "\n".join(lines)


async def _render_help_text(self):
    page = await self.store.get_content("help")
    body = safe_text(getattr(page, "body", None))
    if body:
        return body
    return "Справка\n\nЗдесь можно открыть канал, поддержку и инструкции по подключению."


async def _render_referral_text(self, user):
    page = await self.store.get_content("referral")
    body = safe_text(getattr(page, "body", None))
    invite_link = self._invite_link(user.telegram_id)
    if body:
        return f"{body}\n\nВаша ссылка:\n{invite_link}"
    percent = int(await self.store.get_int_setting("referral_percent", 0) or 0)
    referrals = getattr(user, "referrals", []) or []
    return "\n".join([
        "Реферальная программа",
        "",
        f"Вознаграждение: {percent}% с каждой оплаты реферала.",
        f"Приглашено пользователей: {len(referrals)}",
        f"Накоплено бонусами: {format_money(getattr(user, 'bonus_balance', Decimal('0')) or Decimal('0'))}",
        "",
        "Ваша ссылка:",
        invite_link,
    ])


async def _render_trial_text(self, user):
    page = await self.store.get_content("trial")
    body = safe_text(getattr(page, "body", None))
    if body:
        return body
    days = int(await self.store.get_int_setting("trial_days", 0) or 0)
    status = "Пробный доступ уже был использован для этого аккаунта." if getattr(user, "trial_used", False) else "Если доступ включён, его можно активировать кнопкой ниже."
    return f"Пробный доступ\n\nСрок: {days} дн.\n\n{status}"


async def _admin_panel_text(self, actor_role: str = "owner") -> str:
    metrics = await self.store.get_admin_metrics()
    return "\n".join([
        "Центр управления",
        "",
        "Пульс системы:",
        f"Пользователей: {metrics.get('users', 0)}",
        f"Активный доступ: {metrics.get('active_users', 0)}",
        f"Ожидающие оплаты: {metrics.get('pending_payments', 0)}",
        f"Серверов в базе: {metrics.get('servers', 0)}",
        f"Администраторов: {metrics.get('admins', 0)}",
        f"Сбоев за 3 часа: {metrics.get('recent_provisioning_failures', 0)}",
        f"Версия: {getattr(settings, 'app_version', 'dev')}",
        "",
        f"Ваш уровень доступа: {admin_role_title(actor_role)}",
    ])


def _subscription_lines(subscription) -> list[str]:
    now = datetime.utcnow()
    ends_at = getattr(subscription, "ends_at", None)
    is_active = bool(getattr(subscription, "status", "") == "active" and ends_at and ends_at > now)
    active_keys = [key for key in (getattr(subscription, "vpn_keys", []) or []) if getattr(key, "status", "") != "expired"]
    archive_keys = [key for key in (getattr(subscription, "vpn_keys", []) or []) if getattr(key, "status", "") == "expired"]
    seen = []
    for key in getattr(subscription, "vpn_keys", []) or []:
        server = getattr(key, "server", None)
        if server:
            name = server_title(server)
            if name not in seen:
                seen.append(name)
    title = safe_text(getattr(subscription, "title", None), "Подписка")
    lines = [f"{'🟢' if is_active else '🔴'} {title}"]
    if ends_at:
        lines.append(f"До {ends_at:%d.%m.%Y %H:%M}")
    lines.append(f"Ключи: {len(active_keys)} активных / {len(archive_keys)} архивных")
    if seen:
        lines.append(f"Серверы: {', '.join(seen)}")
    lines.append(f"Статус: {'Активна' if is_active else 'Истекла'}")
    return lines


async def _send_profile_screen(self, target, tg_user, page: int = 1):
    user = await self.store.get_user_summary(tg_user.id)
    if not user:
        text = "Профиль пока недоступен. Попробуйте ещё раз через минуту."
        markup = kb.back_keyboard("nav:home", labels=await self._user_button_labels())
        if isinstance(target, Message):
            await self._send_inline_screen(target, text, markup)
        else:
            await self._safe_edit_message_text(target.message, text, markup)
            await self._safe_answer_callback(target)
        return
    subscriptions = self._profile_subscriptions(user)
    total_pages = max(1, len(subscriptions) or 1)
    page = min(max(page, 1), total_pages)
    current = subscriptions[page - 1:page]
    lines = ["Мой профиль", "", f"Пользователь: {display_user(user)}", f"Баланс: {format_money(getattr(user, 'balance', Decimal('0')) or Decimal('0'))}"]
    if current:
        for item in current:
            lines.extend(["", *_subscription_lines(item)])
    else:
        lines.extend(["", "Активных подписок пока нет."])
    actions = self._subscription_actions(current, back_mode="profile")
    markup = kb.profile_inline_keyboard(actions, bool(getattr(user, 'is_admin', False)), True, True, labels=await self._user_button_labels(), page=page, total_pages=total_pages)
    text = "\n".join(lines).strip()
    if isinstance(target, Message):
        await self._send_inline_screen(target, text, markup)
    else:
        await self._safe_edit_message_text(target.message, text, markup)
        await self._safe_answer_callback(target)


async def _patched_start(self, message: Message, command=None) -> None:
    referral_code = command.args.strip() if command and command.args else None
    user = await self.store.get_or_create_user(message.from_user, referral_code=referral_code)
    if await self._deny_blocked_message(message, user):
        return
    await self._send_inline_screen(message, await self._render_home_text(), await self._home_inline_markup(user.is_admin))


async def _patched_show_menu(self, message: Message, state) -> None:
    await state.clear()
    user = await self.store.get_or_create_user(message.from_user)
    if await self._deny_blocked_message(message, user):
        return
    await self._send_inline_screen(message, await self._render_home_text(), await self._home_inline_markup(user.is_admin))


async def _patched_show_profile(self, message: Message, state):
    await state.clear()
    user = await self.store.get_or_create_user(message.from_user)
    if await self._deny_blocked_message(message, user):
        return
    await _send_profile_screen(self, message, message.from_user, 1)


async def _patched_show_buy(self, message: Message, state):
    await state.clear()
    user = await self.store.get_or_create_user(message.from_user)
    if await self._deny_blocked_message(message, user):
        return
    tariffs = await self.store.list_tariffs(only_active=True)
    await self._send_inline_screen(message, await self._render_buy_text(tariffs), kb.tariffs_keyboard(tariffs, labels=await self._user_button_labels()))


async def _patched_show_help(self, message: Message, state):
    await state.clear()
    user = await self.store.get_or_create_user(message.from_user)
    if await self._deny_blocked_message(message, user):
        return
    ui = await self._ui_snapshot()
    await self._send_inline_screen(message, await self._render_help_text(), kb.help_inline_keyboard(ui['channel_url'], ui['support_chat_url'], ui['terms_url'], user.is_admin, ui['show_referral'], ui['show_trial'], labels=await self._user_button_labels(ui)))


async def _patched_show_referrals(self, message: Message, state):
    await state.clear()
    user = await self.store.get_or_create_user(message.from_user)
    if await self._deny_blocked_message(message, user):
        return
    await self._send_inline_screen(message, await self._render_referral_text(user), kb.referral_inline_keyboard(self._invite_link(user.telegram_id), user.is_admin, True, True, labels=await self._user_button_labels()))


async def _patched_show_trial(self, message: Message, state):
    await state.clear()
    user = await self.store.get_or_create_user(message.from_user)
    if await self._deny_blocked_message(message, user):
        return
    can_activate = not getattr(user, 'trial_used', False)
    await self._send_inline_screen(message, await self._render_trial_text(user), kb.trial_inline_keyboard(can_activate, user.is_admin, True, True, labels=await self._user_button_labels()))


async def _patched_show_admin_panel_message(self, message: Message, state):
    await state.clear()
    actor = await self._admin_actor(message.from_user)
    role = self._admin_role_value(actor)
    if role == "user":
        await message.answer("Доступ к админ-панели закрыт.")
        return
    await self._send_inline_screen(message, await self._admin_panel_text(role), kb.admin_panel_keyboard(role))


def _tariffs_keyboard_clean(tariffs, extend_subscription_id: int | None = None, back_callback: str = "nav:home", labels: dict | None = None, promo_applied: str | None = None):
    lb = dict(DEFAULT_LABELS)
    lb.update(labels or {})
    builder = InlineKeyboardBuilder()
    for tariff in tariffs:
        callback = f"buy:tariff:{tariff.id}"
        if extend_subscription_id:
            callback = f"buy:tariff:{tariff.id}:extend:{extend_subscription_id}"
        builder.row(InlineKeyboardButton(text=f"{tariff_title(tariff)} • {int(getattr(tariff, 'days', 0) or 0)} дн.", callback_data=callback))
    promo_callback = "buy:promo:clear" if promo_applied else "buy:promo"
    promo_text = f"Промокод: {promo_applied}" if promo_applied else lb.get("buy_promo", "Промокод")
    builder.row(InlineKeyboardButton(text=promo_text, callback_data=promo_callback))
    builder.row(InlineKeyboardButton(text=lb.get("nav_back", kb.BACK_LABEL), callback_data=back_callback))
    if back_callback != "nav:home":
        builder.row(InlineKeyboardButton(text=lb.get("nav_home", kb.HOME_LABEL), callback_data="nav:home"))
    return builder.as_markup()


def _payment_methods_keyboard_clean(tariff_id: int, methods: list[str], extend_subscription_id: int | None = None, back_callback: str = "buy:back", labels: dict | None = None, gift_active: bool = False):
    lb = dict(DEFAULT_LABELS)
    lb.update(labels or {})
    builder = InlineKeyboardBuilder()
    label_map = {
        "balance": lb.get("pay_balance", "С баланса"),
        "stars": lb.get("pay_stars", "Stars"),
        "yookassa": lb.get("pay_yookassa", "YooKassa"),
        "crypto": lb.get("pay_crypto", "Crypto"),
    }
    for method in methods:
        callback = f"buy:method:{method}:{tariff_id}"
        if extend_subscription_id:
            callback = f"buy:method:{method}:{tariff_id}:extend:{extend_subscription_id}"
        builder.row(InlineKeyboardButton(text=label_map.get(method, method), callback_data=callback))
    builder.row(InlineKeyboardButton(text=lb.get("nav_back", kb.BACK_LABEL), callback_data=back_callback))
    builder.row(InlineKeyboardButton(text=lb.get("nav_home", kb.HOME_LABEL), callback_data="nav:home"))
    return builder.as_markup()


def _admin_panel_keyboard_clean(role: str = "owner"):
    builder = InlineKeyboardBuilder()
    if role in {"owner", "admin", "support", "finance"}:
        builder.row(InlineKeyboardButton(text="Пользователи", callback_data="adm:users:filters"))
    if role in {"owner", "admin", "ops"}:
        builder.row(InlineKeyboardButton(text="Серверы", callback_data="adm:servers"))
    if role in {"owner", "admin", "finance"}:
        builder.row(InlineKeyboardButton(text="Тарифы", callback_data="adm:tariffs"), InlineKeyboardButton(text="Оплаты", callback_data="adm:payments"))
        builder.row(InlineKeyboardButton(text="Финансы", callback_data="adm:finance"), InlineKeyboardButton(text="Аналитика", callback_data="adm:analytics"))
    if role in {"owner", "admin", "support"}:
        builder.row(InlineKeyboardButton(text="Тексты", callback_data="adm:texts"))
        builder.row(InlineKeyboardButton(text="Промокоды", callback_data="adm:promos"))
        builder.row(InlineKeyboardButton(text="Инструкции", callback_data="adm:guide"), InlineKeyboardButton(text="Резерв", callback_data="adm:reserve"))
    if role in {"owner", "admin"}:
        builder.row(InlineKeyboardButton(text="Рефералы", callback_data="adm:referral"), InlineKeyboardButton(text="Пробный доступ", callback_data="adm:trial"))
        builder.row(InlineKeyboardButton(text="Рассылка", callback_data="adm:broadcast"), InlineKeyboardButton(text="Бэкапы", callback_data="adm:backup"))
    if role == "owner":
        builder.row(InlineKeyboardButton(text="Роли", callback_data="adm:roles"), InlineKeyboardButton(text="Журнал", callback_data="adm:audit"))
    builder.row(InlineKeyboardButton(text="Обновления", callback_data="adm:updates"))
    builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()


async def _show_tariff_checkout(self, callback: CallbackQuery, state, tariff_id: int, extend_subscription_id: int | None = None):
    data = await state.get_data()
    promo_code = str(data.get("buy_promo_code") or "").strip()
    promo_discount_percent = int(data.get("buy_promo_discount_percent") or 0)
    promo_bonus_days = int(data.get("buy_promo_bonus_days") or 0)
    tariff = await self.store.get_tariff(tariff_id)
    user = await self.store.get_user_by_telegram_id(callback.from_user.id)
    if not tariff or not user:
        await self._safe_answer_callback(callback, "Не удалось открыть тариф.", show_alert=True)
        return
    target_subscription = await self.store.get_subscription_details(extend_subscription_id) if extend_subscription_id else None
    renewal_discount_percent = await self.store.get_int_setting("renewal_discount_percent", 0) if target_subscription else 0
    price_rub, price_stars, discount_lines = self._discounted_tariff_prices(tariff, is_extension=bool(target_subscription), promo_discount_percent=promo_discount_percent, renewal_discount_percent=renewal_discount_percent)
    methods = await self._payment_methods_for_user(user, tariff, price_rub=price_rub, price_stars=price_stars)
    if not methods:
        await self._safe_edit_message_text(callback.message, "Сейчас недоступен ни один способ оплаты для этого тарифа.", kb.back_keyboard("buy:back", labels=await self._user_button_labels()))
        await self._safe_answer_callback(callback)
        return
    total_days = int(getattr(tariff, "days", 0) or 0) + promo_bonus_days
    lines = [
        "Счёт готов",
        "",
        f"Тариф: {tariff_title(tariff)}",
        f"Срок доступа: {total_days} дн.",
        f"Стоимость в рублях: {format_money(price_rub)}",
        f"Стоимость в Stars: {format_money(price_stars, 'XTR')}",
        f"Баланс аккаунта: {format_money(getattr(user, 'balance', Decimal('0')) or Decimal('0'))}",
    ]
    if promo_code:
        lines.append(f"Промокод: {promo_code}")
    if discount_lines:
        lines.extend(["", *[repair_text(line) for line in discount_lines]])
    lines.extend(["", "Выберите способ оплаты ниже."])
    back_callback = f"buy:extend:{extend_subscription_id}" if extend_subscription_id else "buy:back"
    await state.update_data(buy_selected_tariff_id=tariff.id, buy_extend_subscription_id=extend_subscription_id or 0)
    await self._safe_edit_message_text(callback.message, "\n".join(lines), kb.payment_methods_keyboard(tariff.id, methods, extend_subscription_id=extend_subscription_id, back_callback=back_callback, labels=await self._user_button_labels()))
    await self._safe_answer_callback(callback)


async def _patched_receive_promo_code(self, message: Message, state):
    code = (message.text or "").strip()
    data = await state.get_data()
    tariff_id = int(data.get("buy_selected_tariff_id") or 0)
    extend_subscription_id = int(data.get("buy_extend_subscription_id") or 0)
    user = await self.store.get_user_by_telegram_id(message.from_user.id)
    if not user or not code:
        await state.clear()
        await message.answer("Промокод не найден.")
        return
    promo, error = await self._resolve_promo(code, user.id, bool(extend_subscription_id))
    if error or not promo:
        await state.set_state(PromoCodeState.waiting_code)
        await message.answer(error or "Промокод не найден.", reply_markup=kb.back_keyboard(f"buy:tariff:{tariff_id}" if tariff_id else "buy:back", labels=await self._user_button_labels()))
        return
    await state.update_data(
        buy_promo_code=code,
        buy_promo_title=safe_text(getattr(promo, "title", None), code),
        buy_promo_discount_percent=int(getattr(promo, "discount_percent", 0) or 0),
        buy_promo_bonus_days=int(getattr(promo, "bonus_days", 0) or 0),
        buy_promo_id=int(getattr(promo, "id", 0) or 0),
    )
    await state.set_state(None)
    await message.answer(f"Промокод {code} применён.")
    if tariff_id:
        fake = type("FakeCallback", (), {"from_user": message.from_user, "message": message, "data": f"buy:tariff:{tariff_id}"})()
        await _show_tariff_checkout(self, fake, state, tariff_id, extend_subscription_id or None)


async def _patched_handle_buy_callbacks(self, callback: CallbackQuery, state):
    data = callback.data or ""
    if data == "buy:back":
        tariffs = await self.store.list_tariffs(only_active=True)
        await state.clear()
        await self._safe_edit_message_text(callback.message, await self._render_buy_text(tariffs), kb.tariffs_keyboard(tariffs, labels=await self._user_button_labels()))
        await self._safe_answer_callback(callback)
        return
    if data == "buy:promo":
        await state.set_state(PromoCodeState.waiting_code)
        await self._safe_edit_message_text(callback.message, "Введите промокод одним сообщением.\n\nЕсли промокод подходит, скидка применится к выбранному тарифу.", kb.back_keyboard("buy:back", labels=await self._user_button_labels()))
        await self._safe_answer_callback(callback)
        return
    if data == "buy:promo:clear":
        await state.update_data(buy_promo_code=None, buy_promo_title=None, buy_promo_discount_percent=0, buy_promo_bonus_days=0, buy_promo_id=0)
        tariffs = await self.store.list_tariffs(only_active=True)
        await self._safe_edit_message_text(callback.message, await self._render_buy_text(tariffs), kb.tariffs_keyboard(tariffs, labels=await self._user_button_labels()))
        await self._safe_answer_callback(callback)
        return
    parts = data.split(":")
    if len(parts) >= 3 and parts[1] == "tariff":
        tariff_id = int(parts[2])
        extend_subscription_id = int(parts[4]) if len(parts) >= 5 and parts[3] == "extend" else None
        await _show_tariff_checkout(self, callback, state, tariff_id, extend_subscription_id)
        return
    await _ORIGINAL_HANDLE_BUY(self, callback, state)


async def _patched_handle_nav_callbacks(self, callback: CallbackQuery, state):
    data = callback.data or ""
    if data == "nav:home":
        await state.clear()
        user = await self.store.get_or_create_user(callback.from_user)
        await self._safe_edit_message_text(callback.message, await self._render_home_text(), await self._home_inline_markup(user.is_admin))
        await self._safe_answer_callback(callback)
        return
    if data.startswith("nav:profile"):
        page = 1
        parts = data.split(":")
        if len(parts) >= 3 and parts[2].isdigit():
            page = int(parts[2])
        await state.clear()
        await _send_profile_screen(self, callback, callback.from_user, page)
        return
    if data == "nav:buy":
        tariffs = await self.store.list_tariffs(only_active=True)
        await state.clear()
        await self._safe_edit_message_text(callback.message, await self._render_buy_text(tariffs), kb.tariffs_keyboard(tariffs, labels=await self._user_button_labels()))
        await self._safe_answer_callback(callback)
        return
    if data == "nav:help":
        await state.clear()
        user = await self.store.get_or_create_user(callback.from_user)
        ui = await self._ui_snapshot()
        await self._safe_edit_message_text(callback.message, await self._render_help_text(), kb.help_inline_keyboard(ui['channel_url'], ui['support_chat_url'], ui['terms_url'], user.is_admin, ui['show_referral'], ui['show_trial'], labels=await self._user_button_labels(ui)))
        await self._safe_answer_callback(callback)
        return
    if data == "nav:referral":
        await state.clear()
        user = await self.store.get_or_create_user(callback.from_user)
        await self._safe_edit_message_text(callback.message, await self._render_referral_text(user), kb.referral_inline_keyboard(self._invite_link(user.telegram_id), user.is_admin, True, True, labels=await self._user_button_labels()))
        await self._safe_answer_callback(callback)
        return
    if data == "nav:trial":
        await state.clear()
        user = await self.store.get_or_create_user(callback.from_user)
        await self._safe_edit_message_text(callback.message, await self._render_trial_text(user), kb.trial_inline_keyboard(not getattr(user, 'trial_used', False), user.is_admin, True, True, labels=await self._user_button_labels()))
        await self._safe_answer_callback(callback)
        return
    if data == "nav:admin":
        await state.clear()
        actor = await self._admin_actor(callback.from_user)
        role = self._admin_role_value(actor)
        if role == "user":
            await self._safe_answer_callback(callback, "Доступ закрыт.", show_alert=True)
            return
        await self._safe_edit_message_text(callback.message, await self._admin_panel_text(role), kb.admin_panel_keyboard(role))
        await self._safe_answer_callback(callback)
        return
    await _ORIGINAL_HANDLE_NAV(self, callback, state)


async def _patched_handle_admin_callbacks(self, callback: CallbackQuery, state):
    if not await self._assert_admin_callback(callback):
        return
    actor = await self._admin_actor(callback.from_user)
    role = self._admin_role_value(actor)
    data = callback.data or ""
    if data == "adm:panel":
        await self._safe_edit_message_text(callback.message, await self._admin_panel_text(role), kb.admin_panel_keyboard(role))
        await self._safe_answer_callback(callback)
        return
    if data == "adm:roles":
        users = await self.store.list_admin_users()
        lines = ["Роли", ""]
        if not users:
            lines.append("Администраторы пока не назначены.")
        else:
            for user in users:
                lines.append(f"• {display_user(user)} — {admin_role_title(self._admin_role_value(user))}")
        await self._safe_edit_message_text(callback.message, "\n".join(lines), kb.back_keyboard("adm:panel"))
        await self._safe_answer_callback(callback)
        return
    if data.startswith("adm:server:failures:"):
        server_id = int(data.rsplit(":", 1)[-1])
        failures = await self.store.list_provisioning_failures(server_id=server_id, limit=15)
        server = await self.store.get_server(server_id)
        lines = [f"История сбоев: {server_title(server) if server else f'Сервер #{server_id}'}", ""]
        if not failures:
            lines.append("За последнее время сбоев нет.")
        else:
            for item in failures[:10]:
                details = safe_text(getattr(item, 'error', None), 'Без текста ошибки')
                lines.append(f"• {item.created_at:%d.%m %H:%M} — {details}")
        await self._safe_edit_message_text(callback.message, "\n".join(lines), kb.back_keyboard("adm:panel"))
        await self._safe_answer_callback(callback)
        return
    await _ORIGINAL_HANDLE_ADMIN(self, callback, state)


def _blocked_access_text(self) -> str:
    return "Доступ к боту ограничен. Обратитесь в поддержку."


def patch_main_symbols(namespace: dict) -> None:
    if "HOME_LABEL" in namespace:
        namespace["HOME_LABEL"] = kb.HOME_LABEL
    if "BACK_LABEL" in namespace:
        namespace["BACK_LABEL"] = kb.BACK_LABEL


def _analytics_keyboard_clean():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Обновить аналитику", callback_data="adm:analytics"))
    builder.row(
        InlineKeyboardButton(text="Экспорт CSV", callback_data="adm:analytics:csv"),
        InlineKeyboardButton(text="Экспорт Excel", callback_data="adm:analytics:xls"),
    )
    builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data="adm:panel"))
    builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()


def _finance_keyboard_clean():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Обновить финансы", callback_data="adm:finance"))
    builder.row(
        InlineKeyboardButton(text="Экспорт CSV", callback_data="adm:finance:csv"),
        InlineKeyboardButton(text="Экспорт Excel", callback_data="adm:finance:xls"),
    )
    builder.row(
        InlineKeyboardButton(text="К серверам", callback_data="adm:servers"),
        InlineKeyboardButton(text="К аналитике", callback_data="adm:analytics"),
    )
    builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data="adm:panel"))
    builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()


def _admin_guide_keyboard_clean(current_section: str = "start"):
    builder = InlineKeyboardBuilder()

    def tab(key: str, title: str):
        prefix = "• " if key == current_section else ""
        return InlineKeyboardButton(text=f"{prefix}{title}", callback_data=f"adm:guide:{key}")

    builder.row(tab("start", "Быстрый старт"), tab("users", "Пользователи"))
    builder.row(tab("servers", "Серверы"), tab("tariffs", "Тарифы"))
    builder.row(tab("payments", "Оплаты"), tab("finance", "Финансы"))
    builder.row(tab("analytics", "Аналитика"), tab("texts", "Тексты"))
    builder.row(tab("programs", "Реф / trial"), tab("service", "Сервис"))
    builder.row(tab("reserve", "Резерв"))
    builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data="adm:panel"))
    builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()


def _tariffs_admin_keyboard_clean(tariffs):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ Создать тариф", callback_data="adm:tariff:add"))
    for tariff in tariffs:
        status = "🟢" if getattr(tariff, "is_active", False) else "⚫"
        days = int(getattr(tariff, "days", 0) or 0)
        builder.row(InlineKeyboardButton(text=f"{status} {tariff_title(tariff)} • {days} дн.", callback_data=f"adm:tariff:view:{tariff.id}"))
    builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data="adm:panel"))
    builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()


def _tariff_detail_keyboard_clean(tariff_id: int, is_active: bool):
    builder = InlineKeyboardBuilder()
    toggle_text = "Скрыть тариф" if is_active else "Показать тариф"
    builder.row(
        InlineKeyboardButton(text="Редактировать", callback_data=f"adm:tariff:edit:{tariff_id}"),
        InlineKeyboardButton(text=toggle_text, callback_data=f"adm:tariff:toggle:{tariff_id}"),
    )
    builder.row(InlineKeyboardButton(text="Удалить", callback_data=f"adm:tariff:delete:{tariff_id}"))
    builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data="adm:tariffs"))
    builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()


def _toggles_keyboard_clean(toggles, payment_config: dict | None = None):
    builder = InlineKeyboardBuilder()
    payment_config = payment_config or {}
    state_map = {toggle.key: toggle.is_enabled for toggle in toggles}

    def state_label(key: str, title: str) -> str:
        return f"{title} • {'включено' if state_map.get(key, False) else 'скрыто'}"

    yookassa_ready = bool(payment_config.get("yookassa_shop_id") and payment_config.get("yookassa_secret_key"))
    crypto_ready = bool(payment_config.get("crypto_pay_token"))
    builder.row(
        InlineKeyboardButton(text=state_label("payment_balance", "Баланс"), callback_data="adm:toggle:payment_balance"),
        InlineKeyboardButton(text=state_label("payment_stars", "Stars"), callback_data="adm:toggle:payment_stars"),
    )
    builder.row(
        InlineKeyboardButton(text=state_label("payment_yookassa", "YooKassa"), callback_data="adm:toggle:payment_yookassa"),
        InlineKeyboardButton(text=state_label("payment_crypto", "Crypto"), callback_data="adm:toggle:payment_crypto"),
    )
    builder.row(
        InlineKeyboardButton(text=f"Настроить YooKassa {'• готово' if yookassa_ready else '• не настроено'}", callback_data="adm:paymentcfg:yookassa"),
        InlineKeyboardButton(text=f"Настроить Crypto {'• готово' if crypto_ready else '• не настроено'}", callback_data="adm:paymentcfg:crypto"),
    )
    builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data="adm:panel"))
    builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()


def _user_filter_button_text_clean(filter_key: str, active_filter: str, counts: dict[str, int]) -> str:
    labels = {"all": "Все", "active": "Активные", "inactive": "Без доступа", "new": "Новые", "never": "Без покупок"}
    prefix = "• " if filter_key == active_filter else ""
    return f"{prefix}{labels.get(filter_key, filter_key)} {counts.get(filter_key, 0)}"


def _append_users_filter_rows_clean(builder: InlineKeyboardBuilder, active_filter: str, counts: dict[str, int]) -> None:
    builder.row(
        InlineKeyboardButton(text=_user_filter_button_text_clean("all", active_filter, counts), callback_data="adm:users:all:1"),
        InlineKeyboardButton(text=_user_filter_button_text_clean("active", active_filter, counts), callback_data="adm:users:active:1"),
    )
    builder.row(
        InlineKeyboardButton(text=_user_filter_button_text_clean("inactive", active_filter, counts), callback_data="adm:users:inactive:1"),
        InlineKeyboardButton(text=_user_filter_button_text_clean("new", active_filter, counts), callback_data="adm:users:new:1"),
    )
    builder.row(InlineKeyboardButton(text=_user_filter_button_text_clean("never", active_filter, counts), callback_data="adm:users:never:1"))


def _users_filters_keyboard_clean(active_filter: str = "all", filter_counts: dict[str, int] | None = None):
    builder = InlineKeyboardBuilder()
    _append_users_filter_rows_clean(builder, active_filter, filter_counts or {})
    builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data="adm:panel"))
    builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()


def _users_list_keyboard_clean(users, filter_key: str, page: int, total: int, page_size: int, filter_counts: dict[str, int] | None = None):
    builder = InlineKeyboardBuilder()
    counts = filter_counts or {}
    _append_users_filter_rows_clean(builder, filter_key, counts)
    if users:
        for user in users:
            label = getattr(user, "full_name", None) or getattr(user, "username", None) or str(getattr(user, "telegram_id", ""))
            prefix = "⛔" if getattr(user, "is_blocked", False) else "👤"
            builder.row(InlineKeyboardButton(text=f"{prefix} {safe_text(label, 'Пользователь')[:35]}", callback_data=f"adm:user:{user.id}:{filter_key}:{page}"))
    else:
        builder.row(InlineKeyboardButton(text="Пользователи не найдены", callback_data="noop"))
    total_pages = max(1, ceil(total / page_size))
    pagination_row = []
    if page > 1:
        pagination_row.append(InlineKeyboardButton(text="◀️", callback_data=f"adm:users:{filter_key}:{page - 1}"))
    pagination_row.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        pagination_row.append(InlineKeyboardButton(text="▶️", callback_data=f"adm:users:{filter_key}:{page + 1}"))
    builder.row(*pagination_row)
    builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data="adm:panel"))
    builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()


def _user_actions_keyboard_clean(user_id: int, is_blocked: bool, filter_key: str, page: int, can_manage_block: bool = True, can_grant_balance: bool = True, can_grant_access: bool = True, can_view_diagnostics: bool = True, can_manage_role: bool = False):
    builder = InlineKeyboardBuilder()
    top_row = []
    if can_grant_balance:
        top_row.append(InlineKeyboardButton(text="Выдать баланс", callback_data=f"adm:user:balance:{user_id}:{filter_key}:{page}"))
    if can_grant_access:
        top_row.append(InlineKeyboardButton(text="Выдать доступ", callback_data=f"adm:user:key:{user_id}:{filter_key}:{page}"))
    if top_row:
        builder.row(*top_row)
    builder.row(
        InlineKeyboardButton(text="Операции", callback_data=f"adm:user:ops:{user_id}:{filter_key}:{page}"),
        InlineKeyboardButton(text="Рефералы", callback_data=f"adm:user:refs:{user_id}:{filter_key}:{page}"),
    )
    extra_row = []
    if can_view_diagnostics:
        extra_row.append(InlineKeyboardButton(text="Диагностика", callback_data=f"adm:user:diag:{user_id}:{filter_key}:{page}"))
    if can_manage_role:
        extra_row.append(InlineKeyboardButton(text="Роль", callback_data=f"adm:user:role:{user_id}:{filter_key}:{page}"))
    if extra_row:
        builder.row(*extra_row)
    if can_manage_block:
        builder.row(InlineKeyboardButton(text=("Разблокировать" if is_blocked else "Заблокировать"), callback_data=f"adm:user:block:{user_id}:{filter_key}:{page}"))
    builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data=f"adm:users:{filter_key}:{page}"))
    builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()


def _text_group_keyboard_clean(group: str, contents, current_section: str | None = None):
    builder = InlineKeyboardBuilder()
    if group == "all":
        texts_prefix = "• " if current_section == "texts" else ""
        buttons_prefix = "• " if current_section == "buttons" else ""
        builder.row(
            InlineKeyboardButton(text=f"{texts_prefix}Тексты", callback_data="adm:texts:texts"),
            InlineKeyboardButton(text=f"{buttons_prefix}Кнопки", callback_data="adm:texts:buttons"),
        )
    if contents:
        icon = "📝" if group == "texts" else "🔘"
        for page in contents:
            builder.row(InlineKeyboardButton(text=f"{icon} {safe_text(page.title, page.key)}", callback_data=f"adm:text:{group}:{page.key}"))
    else:
        builder.row(InlineKeyboardButton(text="Пока ничего не найдено", callback_data="noop"))
    builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data="adm:panel"))
    builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()


def _servers_keyboard_clean(servers):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="➕ Добавить сервер", callback_data="adm:server:add"),
        InlineKeyboardButton(text="Проверить все", callback_data="adm:server:refresh"),
    )
    builder.row(InlineKeyboardButton(text="Обновить трафик ключей", callback_data="adm:server:usage"))
    for server in servers:
        health_icon = getattr(server, "health_badge_icon", "⚪")
        health_score = getattr(server, "health_score", 0)
        active_keys = getattr(server, "active_keys_count", 0)
        expired_keys = getattr(server, "expired_keys_count", 0)
        subscriptions = getattr(server, "active_subscriptions_count", 0)
        builder.row(InlineKeyboardButton(text=f"{health_icon} {server_title(server)} • {health_score}/100 • ключи {active_keys}/{expired_keys} • пользователей {subscriptions}", callback_data=f"adm:server:view:{server.id}"))
    builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data="adm:panel"))
    builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()


def _server_actions_keyboard_clean(server_id: int, panel_url: str | None = None, agent_configured: bool = False, agent_online: bool = False, billing_configured: bool = False):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Проверить сервер", callback_data=f"adm:server:refreshone:{server_id}"),
        InlineKeyboardButton(text="В выдачу / скрыть", callback_data=f"adm:server:toggle:{server_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="Трафик ключей", callback_data="adm:server:usage"),
        InlineKeyboardButton(text="Trial on/off", callback_data=f"adm:server:trial:{server_id}"),
    )
    builder.row(
        InlineKeyboardButton(text=("Оплата настроена" if billing_configured else "Настроить оплату"), callback_data=f"adm:server:billingcfg:{server_id}"),
        InlineKeyboardButton(text="Отметить оплату", callback_data=f"adm:server:billingpaid:{server_id}"),
    )
    builder.row(InlineKeyboardButton(text="История сбоев", callback_data=f"adm:server:failures:{server_id}"))
    if agent_configured:
        builder.row(
            InlineKeyboardButton(text=f"Агент Ubuntu • {'online' if agent_online else 'offline'}", callback_data=f"adm:server:agentstatus:{server_id}"),
            InlineKeyboardButton(text="Перенастроить агент", callback_data=f"adm:server:agentcfg:{server_id}"),
        )
        builder.row(
            InlineKeyboardButton(text="Рестарт 3x-ui", callback_data=f"adm:server:agentcmd:{server_id}:restart_3x_ui"),
            InlineKeyboardButton(text="Рестарт Xray", callback_data=f"adm:server:agentcmd:{server_id}:restart_xray"),
        )
        builder.row(
            InlineKeyboardButton(text="Своя команда", callback_data=f"adm:server:agentcustom:{server_id}"),
            InlineKeyboardButton(text="Отключить агент", callback_data=f"adm:server:agentclear:{server_id}"),
        )
    else:
        builder.row(InlineKeyboardButton(text="Подключить агент Ubuntu", callback_data=f"adm:server:agentcfg:{server_id}"))
    if panel_url:
        builder.row(InlineKeyboardButton(text="Открыть панель", url=panel_url))
    builder.row(InlineKeyboardButton(text="Удалить сервер", callback_data=f"adm:server:delete:{server_id}"))
    builder.row(
        InlineKeyboardButton(text=kb.BACK_LABEL, callback_data="adm:servers"),
        InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"),
    )
    return builder.as_markup()


def _referral_admin_keyboard_clean(is_visible: bool):
    visibility = "включено" if is_visible else "скрыто"
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Изменить процент", callback_data="adm:referral:edit"))
    builder.row(InlineKeyboardButton(text=f"Раздел • {visibility}", callback_data="adm:toggle:section_referral"))
    builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data="adm:panel"))
    builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()


def _trial_admin_keyboard_clean():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Настроить пробный доступ", callback_data="adm:trial:edit"))
    builder.row(InlineKeyboardButton(text="Показать / скрыть раздел", callback_data="adm:toggle:section_trial"))
    builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data="adm:panel"))
    builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()


def _reserve_admin_keyboard_clean(is_visible: bool):
    visibility = "включено" if is_visible else "скрыто"
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=f"Раздел • {visibility}", callback_data="adm:toggle:section_reserve_access"))
    builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data="adm:panel"))
    builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()


def _backup_keyboard_clean():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Создать резервную копию", callback_data="adm:backup:run"))
    builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data="adm:panel"))
    builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()


def _broadcast_filters_keyboard_clean():
    builder = InlineKeyboardBuilder()
    for key, title in [("all", "Всем"), ("active", "С активным доступом"), ("inactive", "Без активного доступа"), ("never", "Без покупок"), ("new", "Новые")]:
        builder.row(InlineKeyboardButton(text=title, callback_data=f"adm:broadcast:{key}"))
    builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data="adm:panel"))
    builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()


def _updates_admin_keyboard_clean(can_trigger: bool, update_available: bool = False):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Проверить обновления", callback_data="adm:updates"))
    if can_trigger:
        builder.row(InlineKeyboardButton(text=("Обновить бота" if update_available else "Переустановить текущую версию"), callback_data="adm:updates:run"))
    builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data="adm:panel"))
    builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()


def _update_notice_keyboard_clean(can_trigger: bool):
    builder = InlineKeyboardBuilder()
    if can_trigger:
        builder.row(InlineKeyboardButton(text="Обновить бота", callback_data="adm:updates:run"))
    builder.row(InlineKeyboardButton(text="Открыть раздел обновлений", callback_data="adm:updates"))
    return builder.as_markup()
def apply_ui_hotfixes() -> None:
    BotController._safe_edit_message_text = _patched_safe_edit_message_text
    BotController._safe_answer_callback = _patched_safe_answer_callback
    BotController._send_inline_screen = _patched_send_inline_screen
    BotController._ui_snapshot = _patched_ui_snapshot
    BotController._user_button_labels = _patched_user_button_labels
    BotController._render_home_text = _render_home_text
    BotController._render_buy_text = _render_buy_text
    BotController._render_help_text = _render_help_text
    BotController._render_referral_text = _render_referral_text
    BotController._render_trial_text = _render_trial_text
    BotController._admin_panel_text = _admin_panel_text
    BotController._blocked_access_text = _blocked_access_text
    BotController.start = _patched_start
    BotController.show_menu = _patched_show_menu
    BotController.show_profile = _patched_show_profile
    BotController.show_buy = _patched_show_buy
    BotController.show_help = _patched_show_help
    BotController.show_referrals = _patched_show_referrals
    BotController.show_trial = _patched_show_trial
    BotController.show_admin_panel_message = _patched_show_admin_panel_message
    BotController.receive_promo_code = _patched_receive_promo_code
    BotController.handle_buy_callbacks = _patched_handle_buy_callbacks
    BotController.handle_nav_callbacks = _patched_handle_nav_callbacks
    BotController.handle_admin_callbacks = _patched_handle_admin_callbacks
    kb.tariffs_keyboard = _tariffs_keyboard_clean
    kb.payment_methods_keyboard = _payment_methods_keyboard_clean
    kb.admin_panel_keyboard = _admin_panel_keyboard_clean
    kb.analytics_keyboard = _analytics_keyboard_clean
    kb.finance_keyboard = _finance_keyboard_clean
    kb.admin_guide_keyboard = _admin_guide_keyboard_clean
    kb.tariffs_admin_keyboard = _tariffs_admin_keyboard_clean
    kb.tariff_detail_keyboard = _tariff_detail_keyboard_clean
    kb.toggles_keyboard = _toggles_keyboard_clean
    kb.users_filters_keyboard = _users_filters_keyboard_clean
    kb.users_list_keyboard = _users_list_keyboard_clean
    kb.user_actions_keyboard = _user_actions_keyboard_clean
    kb.text_group_keyboard = _text_group_keyboard_clean
    kb.servers_keyboard = _servers_keyboard_clean
    kb.server_actions_keyboard = _server_actions_keyboard_clean
    kb.referral_admin_keyboard = _referral_admin_keyboard_clean
    kb.trial_admin_keyboard = _trial_admin_keyboard_clean
    kb.reserve_admin_keyboard = _reserve_admin_keyboard_clean
    kb.backup_keyboard = _backup_keyboard_clean
    kb.broadcast_filters_keyboard = _broadcast_filters_keyboard_clean
    kb.updates_admin_keyboard = _updates_admin_keyboard_clean
    kb.update_notice_keyboard = _update_notice_keyboard_clean
    controller_module.tariffs_keyboard = _tariffs_keyboard_clean
    controller_module.payment_methods_keyboard = _payment_methods_keyboard_clean
    controller_module.admin_panel_keyboard = _admin_panel_keyboard_clean
    controller_module.analytics_keyboard = _analytics_keyboard_clean
    controller_module.finance_keyboard = _finance_keyboard_clean
    controller_module.admin_guide_keyboard = _admin_guide_keyboard_clean
    controller_module.tariffs_admin_keyboard = _tariffs_admin_keyboard_clean
    controller_module.tariff_detail_keyboard = _tariff_detail_keyboard_clean
    controller_module.toggles_keyboard = _toggles_keyboard_clean
    controller_module.users_filters_keyboard = _users_filters_keyboard_clean
    controller_module.users_list_keyboard = _users_list_keyboard_clean
    controller_module.user_actions_keyboard = _user_actions_keyboard_clean
    controller_module.text_group_keyboard = _text_group_keyboard_clean
    controller_module.servers_keyboard = _servers_keyboard_clean
    controller_module.server_actions_keyboard = _server_actions_keyboard_clean
    controller_module.referral_admin_keyboard = _referral_admin_keyboard_clean
    controller_module.trial_admin_keyboard = _trial_admin_keyboard_clean
    controller_module.reserve_admin_keyboard = _reserve_admin_keyboard_clean
    controller_module.backup_keyboard = _backup_keyboard_clean
    controller_module.broadcast_filters_keyboard = _broadcast_filters_keyboard_clean
    controller_module.updates_admin_keyboard = _updates_admin_keyboard_clean
    controller_module.update_notice_keyboard = _update_notice_keyboard_clean


