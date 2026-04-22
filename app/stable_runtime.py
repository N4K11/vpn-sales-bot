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

BROKEN_MARKERS = ("Р ", "РЎ", "РІР", "Гђ", "Г‘", "пїЅ", "????")
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


def to_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def money(value, currency: str = "RUB") -> str:
    return format_money(value or Decimal("0"), currency)


def display_user(user) -> str:
    if not user:
        return "пользователь"
    username = (getattr(user, "username", "") or "").strip()
    if username:
        return f"@{username}"
    full_name = (getattr(user, "full_name", "") or "").strip()
    if full_name:
        return full_name
    telegram_id = getattr(user, "telegram_id", None)
    return str(telegram_id) if telegram_id else "пользователь"


def role_title(role: str) -> str:
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


def subscription_keys(subscription) -> list:
    return list(getattr(subscription, "keys", None) or getattr(subscription, "vpn_keys", None) or [])


def subscription_active(subscription) -> bool:
    ends_at = getattr(subscription, "ends_at", None)
    return bool(getattr(subscription, "status", "") == "active" and ends_at and ends_at > datetime.utcnow())


def subscription_title(subscription) -> str:
    tariff = getattr(subscription, "tariff", None)
    if tariff:
        return tariff_title(tariff)
    return "Пробный доступ" if getattr(subscription, "is_trial", False) else "Доступ"


def subscription_servers(subscription) -> list[str]:
    names: list[str] = []
    for key in subscription_keys(subscription):
        server = getattr(key, "server", None)
        name = server_title(server) if server else safe_text(getattr(key, "label", ""), "Сервер")
        if name and name not in names:
            names.append(name)
    return names


def subscription_button(subscription) -> tuple[str, str]:
    icon = "🟢" if subscription_active(subscription) else "🔴"
    ends_at = getattr(subscription, "ends_at", None)
    date = f"до {ends_at:%d.%m}" if ends_at else "без даты"
    return f"{icon} {subscription_title(subscription)} • {date}", f"sub:show:{subscription.id}:profile"


def key_button(key, subscription) -> tuple[str, str]:
    server = getattr(key, "server", None)
    active = getattr(key, "is_active", False) and subscription_active(subscription)
    icon = "🟢" if active else "🔴"
    label = server_title(server) if server else safe_text(getattr(key, "label", ""), f"Ключ #{getattr(key, 'id', '')}")
    return f"{icon} {label}", f"key:show:{key.id}:sub:{subscription.id}"


def profile_subscriptions(user) -> list:
    items = list(getattr(user, "subscriptions", []) or [])
    items.sort(key=lambda item: getattr(item, "ends_at", datetime.min) or datetime.min, reverse=True)
    return items


def subscription_url(subscription) -> str:
    try:
        from app.services.subscription_links import build_subscription_url
        return build_subscription_url(subscription)
    except Exception:
        return ""


def reserve_url(user) -> str:
    try:
        from app.services.subscription_links import build_reserve_access_url
        return build_reserve_access_url(user)
    except Exception:
        return ""


def activation_text(subscription, keys: list, reserve: str = "", extended: bool = False) -> str:
    title = "Доступ продлён" if extended else ("Пробный доступ активирован" if getattr(subscription, "is_trial", False) else "Доступ активирован")
    url = subscription_url(subscription)
    servers = subscription_servers(subscription)
    active_keys = [key for key in keys if getattr(key, "is_active", True)]
    lines = [
        f"✅ {title}",
        "",
        f"Тариф: {subscription_title(subscription)}",
        f"Действует до: {subscription.ends_at:%d.%m.%Y %H:%M}",
        f"Активных ключей: {len(active_keys) or len(keys)}",
    ]
    if servers:
        lines.append(f"Серверы: {', '.join(servers)}")
    if url:
        lines.extend(["", "Общая ссылка подписки:", url])
    if reserve:
        lines.extend(["", "Резервный кабинет:", reserve])
    lines.extend(["", "Сохраните ссылки заранее."])
    return "\n".join(lines)


async def safe_send_inline(self, message: Message, text: str, reply_markup) -> None:
    cleanup = await message.answer("\u2060", reply_markup=ReplyKeyboardRemove())
    try:
        await cleanup.edit_text(repair_text(text), reply_markup=reply_markup, link_preview_options=LinkPreviewOptions(is_disabled=True))
    except Exception:
        await message.answer(repair_text(text), reply_markup=reply_markup, link_preview_options=LinkPreviewOptions(is_disabled=True))


async def safe_edit(self, message, text: str, reply_markup=None) -> None:
    try:
        await message.edit_text(repair_text(text), reply_markup=reply_markup, link_preview_options=LinkPreviewOptions(is_disabled=True))
    except Exception:
        await message.answer(repair_text(text), reply_markup=reply_markup, link_preview_options=LinkPreviewOptions(is_disabled=True))


async def safe_answer(self, callback: CallbackQuery, text: str | None = None, show_alert: bool = False) -> None:
    try:
        await callback.answer(repair_text(text) if text else None, show_alert=show_alert)
    except Exception:
        pass


async def user_labels(self, ui: dict | None = None) -> dict:
    snapshot = ui or await self._ui_snapshot()
    labels = dict(DEFAULT_LABELS)
    for key, value in (snapshot.get("button_labels") or {}).items():
        cleaned = safe_text(value, labels.get(key, ""))
        if cleaned:
            labels[key] = cleaned
    return labels


async def ui_snapshot(self) -> dict:
    try:
        original = await self.store.get_ui_settings()
    except Exception:
        original = {}
    labels = dict(DEFAULT_LABELS)
    for key, value in (original.get("button_labels") or {}).items():
        cleaned = safe_text(value, labels.get(key, ""))
        if cleaned:
            labels[key] = cleaned
    return {
        **original,
        "button_labels": labels,
        "channel_url": original.get("channel_url") or settings.channel_url,
        "support_chat_url": original.get("support_chat_url") or settings.support_chat_url,
        "terms_url": original.get("terms_url") or settings.terms_url,
        "show_referral": bool(original.get("show_referral", True)),
        "show_trial": bool(original.get("show_trial", True)),
    }


async def home_markup(self, is_admin: bool):
    ui = await self._ui_snapshot()
    return kb.home_inline_keyboard(is_admin, ui["show_referral"], ui["show_trial"], labels=await self._user_button_labels(ui))


async def render_home(self) -> str:
    page = await self.store.get_content("main")
    return safe_text(getattr(page, "body", None), "MyAir\n\nВыберите нужный раздел ниже.")


async def render_buy(self, tariffs) -> str:
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


async def render_help(self) -> str:
    page = await self.store.get_content("help")
    return safe_text(getattr(page, "body", None), "Справка\n\nЗдесь можно открыть канал, поддержку и инструкции по подключению.")


async def render_referral(self, user) -> str:
    page = await self.store.get_content("referral")
    invite = self._invite_link(user.telegram_id)
    body = safe_text(getattr(page, "body", None))
    if body:
        return f"{body}\n\nВаша ссылка:\n{invite}"
    percent = int(await self.store.get_int_setting("referral_percent", 0) or 0)
    return "\n".join([
        "Реферальная программа",
        "",
        f"Вознаграждение: {percent}% с каждой оплаты реферала.",
        f"Приглашено пользователей: {len(getattr(user, 'referrals', []) or [])}",
        f"Баланс: {money(getattr(user, 'balance', Decimal('0')))}",
        "",
        "Ваша ссылка:",
        invite,
    ])


async def render_trial(self, user) -> str:
    page = await self.store.get_content("trial")
    body = safe_text(getattr(page, "body", None))
    if body:
        return body
    days = int(await self.store.get_int_setting("trial_days", 0) or 0)
    used = bool(getattr(user, "trial_claimed", False) or getattr(user, "trial_used", False))
    status = "Пробный доступ уже был использован для этого аккаунта." if used else "Если доступ включён, его можно активировать кнопкой ниже."
    return f"Пробный доступ\n\nСрок: {days} дн.\n\n{status}"


async def admin_panel_text(self, actor_role: str = "owner") -> str:
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
        f"Ваш уровень доступа: {role_title(actor_role)}",
    ])


async def start(self, message: Message, command=None) -> None:
    referral_code = command.args.strip() if command and command.args else None
    user = await self.store.get_or_create_user(message.from_user, referral_code=referral_code)
    if await self._deny_blocked_message(message, user):
        return
    await self._send_inline_screen(message, await self._render_home_text(), await self._home_inline_markup(user.is_admin))


async def show_menu(self, message: Message, state) -> None:
    await state.clear()
    user = await self.store.get_or_create_user(message.from_user)
    if await self._deny_blocked_message(message, user):
        return
    await self._send_inline_screen(message, await self._render_home_text(), await self._home_inline_markup(user.is_admin))


async def send_profile(self, target, tg_user, page: int = 1) -> None:
    user = await self.store.get_user_summary(tg_user.id)
    if not user:
        text = "Профиль пока недоступен. Попробуйте ещё раз."
        markup = kb.back_keyboard("nav:home", labels=await self._user_button_labels())
        if isinstance(target, Message):
            await self._send_inline_screen(target, text, markup)
        else:
            await self._safe_edit_message_text(target.message, text, markup)
            await self._safe_answer_callback(target)
        return
    subscriptions = profile_subscriptions(user)
    page_size = 8
    total_pages = max(1, ceil(len(subscriptions) / page_size))
    page = min(max(page, 1), total_pages)
    current = subscriptions[(page - 1) * page_size:page * page_size]
    active_count = sum(1 for item in subscriptions if subscription_active(item))
    lines = [
        "Мой профиль",
        "",
        f"Пользователь: {display_user(user)}",
        f"Баланс: {money(getattr(user, 'balance', Decimal('0')))}",
        f"Подписок: {len(subscriptions)}",
        f"Активных: {active_count}",
    ]
    actions = [subscription_button(item) for item in current]
    lines.append("")
    lines.append("Откройте нужную подписку кнопкой ниже." if actions else "Подписок пока нет.")
    markup = kb.profile_inline_keyboard(actions, bool(getattr(user, "is_admin", False)), True, True, labels=await self._user_button_labels(), page=page, total_pages=total_pages)
    if isinstance(target, Message):
        await self._send_inline_screen(target, "\n".join(lines), markup)
    else:
        await self._safe_edit_message_text(target.message, "\n".join(lines), markup)
        await self._safe_answer_callback(target)


async def show_profile(self, message: Message, state):
    await state.clear()
    user = await self.store.get_or_create_user(message.from_user)
    if await self._deny_blocked_message(message, user):
        return
    await send_profile(self, message, message.from_user, 1)


async def show_buy_screen(self, target, state, extend_subscription_id: int | None = None) -> None:
    tariffs = await self.store.list_tariffs(only_active=True)
    data = await state.get_data()
    promo = str(data.get("buy_promo_code") or "").strip()
    text = await self._render_buy_text(tariffs)
    if promo:
        text += f"\n\nПромокод применён: {promo}"
    markup = kb.tariffs_keyboard(tariffs, extend_subscription_id=extend_subscription_id, back_callback=f"sub:show:{extend_subscription_id}:profile" if extend_subscription_id else "nav:home", labels=await self._user_button_labels(), promo_applied=promo or None)
    if isinstance(target, Message):
        await self._send_inline_screen(target, text, markup)
    else:
        await self._safe_edit_message_text(target.message, text, markup)
        await self._safe_answer_callback(target)


async def show_buy(self, message: Message, state):
    await state.clear()
    user = await self.store.get_or_create_user(message.from_user)
    if await self._deny_blocked_message(message, user):
        return
    await show_buy_screen(self, message, state)


async def show_help(self, message: Message, state):
    await state.clear()
    user = await self.store.get_or_create_user(message.from_user)
    if await self._deny_blocked_message(message, user):
        return
    ui = await self._ui_snapshot()
    await self._send_inline_screen(message, await self._render_help_text(), kb.help_inline_keyboard(ui["channel_url"], ui["support_chat_url"], ui["terms_url"], user.is_admin, ui["show_referral"], ui["show_trial"], labels=await self._user_button_labels(ui)))


async def show_referrals(self, message: Message, state):
    await state.clear()
    user = await self.store.get_or_create_user(message.from_user)
    if await self._deny_blocked_message(message, user):
        return
    await self._send_inline_screen(message, await self._render_referral_text(user), kb.referral_inline_keyboard(self._invite_link(user.telegram_id), user.is_admin, True, True, labels=await self._user_button_labels()))


async def show_trial(self, message: Message, state):
    await state.clear()
    user = await self.store.get_or_create_user(message.from_user)
    if await self._deny_blocked_message(message, user):
        return
    can_activate = not bool(getattr(user, "trial_claimed", False) or getattr(user, "trial_used", False))
    await self._send_inline_screen(message, await self._render_trial_text(user), kb.trial_inline_keyboard(can_activate, user.is_admin, True, True, labels=await self._user_button_labels()))


async def show_admin(self, message: Message, state):
    await state.clear()
    actor = await self._admin_actor(message.from_user)
    role = self._admin_role_value(actor)
    if role == "user":
        await message.answer("Доступ к админ-панели закрыт.")
        return
    await self._send_inline_screen(message, await self._admin_panel_text(role), kb.admin_panel_keyboard(role))

async def show_tariff_checkout(self, target, state, tariff_id: int, extend_subscription_id: int | None = None) -> None:
    tg_user = target.from_user
    data = await state.get_data()
    promo_code = str(data.get("buy_promo_code") or "").strip()
    promo_discount = to_int(data.get("buy_promo_discount_percent"), 0)
    promo_bonus = to_int(data.get("buy_promo_bonus_days"), 0)
    tariff = await self.store.get_tariff(tariff_id)
    user = await self.store.get_user_by_telegram_id(tg_user.id)
    if not tariff or not user:
        if isinstance(target, Message):
            await target.answer("Не удалось открыть тариф.")
        else:
            await self._safe_answer_callback(target, "Не удалось открыть тариф.", show_alert=True)
        return
    target_subscription = await self.store.get_subscription_details(extend_subscription_id) if extend_subscription_id else None
    if extend_subscription_id and (not target_subscription or getattr(getattr(target_subscription, "user", None), "telegram_id", None) != tg_user.id):
        await self._safe_answer_callback(target, "Эту подписку нельзя продлить из вашего аккаунта.", show_alert=True)
        return
    renewal_discount = await self.store.get_int_setting("renewal_discount_percent", 0) if target_subscription else 0
    price_rub, price_stars, discount_lines = self._discounted_tariff_prices(tariff, is_extension=bool(target_subscription), promo_discount_percent=promo_discount, renewal_discount_percent=renewal_discount)
    methods = await self._payment_methods_for_user(user, tariff, price_rub=price_rub, price_stars=price_stars)
    methods = [method for method in methods if method in {"balance", "yookassa", "crypto"}]
    if price_rub <= 0 and "balance" not in methods:
        methods.insert(0, "balance")
    if not methods:
        text = "Для этого тарифа сейчас нет доступных способов оплаты."
        markup = kb.back_keyboard("buy:back", labels=await self._user_button_labels())
        if isinstance(target, Message):
            await target.answer(text, reply_markup=markup)
        else:
            await self._safe_edit_message_text(target.message, text, markup)
            await self._safe_answer_callback(target)
        return
    lines = [
        "Счёт готов",
        "",
        f"Тариф: {tariff_title(tariff)}",
        f"Срок доступа: {int(getattr(tariff, 'days', 0) or 0) + promo_bonus} дн.",
        f"Сумма: {money(price_rub)}",
        f"Баланс аккаунта: {money(getattr(user, 'balance', Decimal('0')))}",
    ]
    if promo_code:
        lines.append(f"Промокод: {promo_code}")
    if discount_lines:
        lines.extend(["", *[repair_text(line) for line in discount_lines]])
    lines.extend(["", "Выберите способ оплаты."])
    await state.update_data(buy_selected_tariff_id=tariff.id, buy_extend_subscription_id=extend_subscription_id or 0)
    markup = kb.payment_methods_keyboard(tariff.id, methods, extend_subscription_id=extend_subscription_id, back_callback=f"buy:extend:{extend_subscription_id}" if extend_subscription_id else "buy:back", labels=await self._user_button_labels())
    if isinstance(target, Message):
        await target.answer("\n".join(lines), reply_markup=markup)
    else:
        await self._safe_edit_message_text(target.message, "\n".join(lines), markup)
        await self._safe_answer_callback(target)


async def process_payment_method(self, callback: CallbackQuery, state, method: str, tariff_id: int, extend_subscription_id: int | None = None) -> None:
    from app.services.payments import PaymentGatewayError
    tariff = await self.store.get_tariff(tariff_id)
    user = await self.store.get_user_by_telegram_id(callback.from_user.id)
    if not tariff or not user:
        await self._safe_answer_callback(callback, "Не удалось подготовить оплату.", show_alert=True)
        return
    data = await state.get_data()
    promo_discount = to_int(data.get("buy_promo_discount_percent"), 0)
    renewal_discount = await self.store.get_int_setting("renewal_discount_percent", 0) if extend_subscription_id else 0
    price_rub, _price_stars, _discount_lines = self._discounted_tariff_prices(tariff, is_extension=bool(extend_subscription_id), promo_discount_percent=promo_discount, renewal_discount_percent=renewal_discount)
    ext_part = f"-ext{extend_subscription_id}-" if extend_subscription_id else "-"
    payload = f"air-{user.id}-{tariff.id}{ext_part}{int(datetime.utcnow().timestamp())}"
    description = f"MyAir {tariff_title(tariff)}"
    if method == "balance":
        payment, error = await self.store.create_balance_payment(user.id, tariff.id, price_rub, payload, description)
        if error or not payment:
            await self._safe_answer_callback(callback, safe_text(error, "Недостаточно средств на балансе."), show_alert=True)
            return
        from app.main import activate_paid_payment
        ok = await activate_paid_payment(self.bot, self.store, self.provisioning, payment.id)
        text = "Оплата прошла. Доступ отправлен отдельным сообщением." if ok else "Оплата списана, но доступ не выдался. Проверьте серверы и журнал сбоев."
        await self._safe_edit_message_text(callback.message, text, kb.back_keyboard("nav:profile", labels=await self._user_button_labels()))
        await self._safe_answer_callback(callback)
        return
    if method not in {"yookassa", "crypto"}:
        await self._safe_answer_callback(callback, "Этот способ оплаты временно отключён.", show_alert=True)
        return
    payment = await self.store.create_payment(user.id, tariff.id, method, price_rub, "RUB", payload)
    try:
        invoice = await self.payments.create_invoice(payment.id)
    except PaymentGatewayError as exc:
        await self._safe_edit_message_text(callback.message, f"Не удалось создать счёт.\n\n{safe_text(str(exc), 'Проверьте настройки оплаты.')}", kb.back_keyboard(f"buy:tariff:{tariff.id}", labels=await self._user_button_labels()))
        await self._safe_answer_callback(callback)
        return
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Перейти к оплате", url=invoice.payment_url))
    builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data=f"buy:tariff:{tariff.id}"), InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    lines = [
        "Счёт готов",
        "",
        f"Тариф: {tariff_title(tariff)}",
        f"Способ оплаты: {'YooKassa' if method == 'yookassa' else 'Crypto Bot'}",
        f"Сумма: {money(price_rub)}",
        "",
        "После подтверждения оплаты бот автоматически активирует доступ и пришлёт ссылку.",
    ]
    await self._safe_edit_message_text(callback.message, "\n".join(lines), builder.as_markup())
    await self._safe_answer_callback(callback)


async def receive_promo_code(self, message: Message, state):
    code = (message.text or "").strip()
    data = await state.get_data()
    tariff_id = to_int(data.get("buy_selected_tariff_id"), 0)
    extend_subscription_id = to_int(data.get("buy_extend_subscription_id"), 0)
    user = await self.store.get_user_by_telegram_id(message.from_user.id)
    if not user or not code:
        await message.answer("Введите промокод текстом или вернитесь назад.", reply_markup=kb.back_keyboard("buy:back", labels=await self._user_button_labels()))
        return
    promo, error = await self._resolve_promo(code, user.id, bool(extend_subscription_id))
    if error or not promo:
        await state.set_state(PromoCodeState.waiting_code)
        await message.answer(safe_text(error, "Промокод не найден."), reply_markup=kb.back_keyboard(f"buy:tariff:{tariff_id}" if tariff_id else "buy:back", labels=await self._user_button_labels()))
        return
    await state.update_data(
        buy_promo_code=code,
        buy_promo_title=safe_text(getattr(promo, "title", None), code),
        buy_promo_discount_percent=int(getattr(promo, "discount_percent", 0) or 0),
        buy_promo_bonus_days=int(getattr(promo, "bonus_days", 0) or 0),
        buy_promo_id=int(getattr(promo, "id", 0) or 0),
    )
    await state.set_state(None)
    if tariff_id:
        await show_tariff_checkout(self, message, state, tariff_id, extend_subscription_id or None)
    else:
        await show_buy_screen(self, message, state, extend_subscription_id or None)


async def handle_buy_callbacks(self, callback: CallbackQuery, state):
    data = callback.data or ""
    parts = data.split(":")
    if data == "buy:back":
        await state.update_data(buy_selected_tariff_id=0, buy_extend_subscription_id=0)
        await show_buy_screen(self, callback, state)
        return
    if data.startswith("buy:extend:"):
        subscription_id = to_int(parts[-1], 0)
        await state.update_data(buy_extend_subscription_id=subscription_id, buy_selected_tariff_id=0)
        await show_buy_screen(self, callback, state, subscription_id)
        return
    if data == "buy:promo":
        await state.set_state(PromoCodeState.waiting_code)
        await self._safe_edit_message_text(callback.message, "Введите промокод одним сообщением.", kb.back_keyboard("buy:back", labels=await self._user_button_labels()))
        await self._safe_answer_callback(callback)
        return
    if data == "buy:promo:clear":
        await state.update_data(buy_promo_code=None, buy_promo_title=None, buy_promo_discount_percent=0, buy_promo_bonus_days=0, buy_promo_id=0)
        await show_buy_screen(self, callback, state)
        return
    if len(parts) >= 3 and parts[1] == "tariff":
        tariff_id = to_int(parts[2], 0)
        extend_id = to_int(parts[4], 0) if len(parts) >= 5 and parts[3] == "extend" else None
        await show_tariff_checkout(self, callback, state, tariff_id, extend_id)
        return
    if len(parts) >= 4 and parts[1] == "method":
        method = parts[2]
        tariff_id = to_int(parts[3], 0)
        extend_id = to_int(parts[5], 0) if len(parts) >= 6 and parts[4] == "extend" else None
        await process_payment_method(self, callback, state, method, tariff_id, extend_id)
        return
    await self._safe_answer_callback(callback, "Действие сейчас недоступно.", show_alert=True)


async def handle_nav_callbacks(self, callback: CallbackQuery, state):
    data = callback.data or ""
    await state.clear()
    if data == "nav:home":
        user = await self.store.get_or_create_user(callback.from_user)
        await self._safe_edit_message_text(callback.message, await self._render_home_text(), await self._home_inline_markup(user.is_admin))
        await self._safe_answer_callback(callback)
        return
    if data.startswith("nav:profile"):
        parts = data.split(":")
        page = to_int(parts[2], 1) if len(parts) >= 3 else 1
        await send_profile(self, callback, callback.from_user, page)
        return
    if data == "nav:buy":
        await show_buy_screen(self, callback, state)
        return
    if data == "nav:help":
        user = await self.store.get_or_create_user(callback.from_user)
        ui = await self._ui_snapshot()
        await self._safe_edit_message_text(callback.message, await self._render_help_text(), kb.help_inline_keyboard(ui["channel_url"], ui["support_chat_url"], ui["terms_url"], user.is_admin, ui["show_referral"], ui["show_trial"], labels=await self._user_button_labels(ui)))
        await self._safe_answer_callback(callback)
        return
    if data == "nav:referral":
        user = await self.store.get_or_create_user(callback.from_user)
        await self._safe_edit_message_text(callback.message, await self._render_referral_text(user), kb.referral_inline_keyboard(self._invite_link(user.telegram_id), user.is_admin, True, True, labels=await self._user_button_labels()))
        await self._safe_answer_callback(callback)
        return
    if data == "nav:trial":
        user = await self.store.get_or_create_user(callback.from_user)
        can_activate = not bool(getattr(user, "trial_claimed", False) or getattr(user, "trial_used", False))
        await self._safe_edit_message_text(callback.message, await self._render_trial_text(user), kb.trial_inline_keyboard(can_activate, user.is_admin, True, True, labels=await self._user_button_labels()))
        await self._safe_answer_callback(callback)
        return
    if data == "nav:admin":
        actor = await self._admin_actor(callback.from_user)
        role = self._admin_role_value(actor)
        if role == "user":
            await self._safe_answer_callback(callback, "Доступ закрыт.", show_alert=True)
            return
        await self._safe_edit_message_text(callback.message, await self._admin_panel_text(role), kb.admin_panel_keyboard(role))
        await self._safe_answer_callback(callback)
        return
    await self._safe_answer_callback(callback, "Раздел не найден.", show_alert=True)


async def fallback_message(self, message: Message, state):
    text = (repair_text(message.text or "") or "").strip().lower()
    if not text or looks_broken_text(text):
        await show_menu(self, message, state)
        return
    if text.startswith("/start") or text.startswith("/menu") or "глав" in text:
        await show_menu(self, message, state)
    elif "проф" in text:
        await show_profile(self, message, state)
    elif "подключ" in text or "air" in text or "тариф" in text:
        await show_buy(self, message, state)
    elif "справ" in text or "поддерж" in text:
        await show_help(self, message, state)
    elif "рефер" in text or "партн" in text:
        await show_referrals(self, message, state)
    elif "проб" in text or "trial" in text:
        await show_trial(self, message, state)
    elif "админ" in text:
        await show_admin(self, message, state)
    else:
        await show_menu(self, message, state)

async def handle_help_callbacks(self, callback: CallbackQuery):
    data = callback.data or ""
    labels = await self._user_button_labels()
    if data == "help:devices":
        page = await self.store.get_content("devices_menu")
        text = safe_text(getattr(page, "body", None), "Как подключить\n\nВыберите вашу платформу.")
        await self._safe_edit_message_text(callback.message, text, kb.device_guides_menu_keyboard(labels=labels))
        await self._safe_answer_callback(callback)
        return
    guide_map = {
        "help:ios": ("guide_ios", "iOS"),
        "help:android": ("guide_android", "Android"),
        "help:windows": ("guide_windows", "Windows"),
        "help:macos": ("guide_macos", "macOS"),
    }
    if data in guide_map:
        key, title = guide_map[data]
        page = await self.store.get_content(key)
        text = safe_text(getattr(page, "body", None), f"{title}\n\nОткройте ссылку подписки в приложении с поддержкой Subscription URL.")
        await self._safe_edit_message_text(callback.message, text, kb.device_guide_keyboard(labels=labels))
        await self._safe_answer_callback(callback)
        return
    await self._safe_answer_callback(callback)


async def handle_trial_callbacks(self, callback: CallbackQuery):
    user = await self.store.get_or_create_user(callback.from_user)
    if await self._deny_blocked_callback(callback, user):
        return
    if callback.data != "trial:activate":
        await self._safe_answer_callback(callback)
        return
    subscription, keys, error = await self.provisioning.grant_trial(callback.from_user.id)
    if error or not subscription:
        await self._safe_answer_callback(callback, safe_text(error, "Не удалось активировать пробный доступ."), show_alert=True)
        return
    text = activation_text(subscription, keys, reserve_url(user))
    await self._safe_edit_message_text(callback.message, text, kb.access_result_keyboard([subscription_button(subscription)], labels=await self._user_button_labels()))
    await self._safe_answer_callback(callback, "Пробный доступ активирован.")


async def handle_subscription_callbacks(self, callback: CallbackQuery):
    data = callback.data or ""
    parts = data.split(":")
    if len(parts) < 3 or parts[1] != "show":
        await self._safe_answer_callback(callback)
        return
    subscription_id = to_int(parts[2], 0)
    subscription = await self.store.get_subscription_details(subscription_id)
    if not subscription:
        await self._safe_answer_callback(callback, "Подписка не найдена.", show_alert=True)
        return
    owner = getattr(subscription, "user", None)
    actor = await self._admin_actor(callback.from_user)
    is_admin = self._admin_role_value(actor) != "user"
    if not is_admin and getattr(owner, "telegram_id", None) != callback.from_user.id:
        await self._safe_answer_callback(callback, "Это не ваша подписка.", show_alert=True)
        return
    keys = subscription_keys(subscription)
    active_keys = [key for key in keys if getattr(key, "is_active", False)]
    archive_keys = [key for key in keys if not getattr(key, "is_active", False)]
    url = subscription_url(subscription)
    reserve = reserve_url(owner) if owner else ""
    lines = [
        subscription_title(subscription),
        "",
        f"Статус: {'активна' if subscription_active(subscription) else 'истекла'}",
        f"Действует до: {subscription.ends_at:%d.%m.%Y %H:%M}",
        f"Ключи: {len(active_keys)} активных / {len(archive_keys)} архивных",
    ]
    servers = subscription_servers(subscription)
    if servers:
        lines.append(f"Серверы: {', '.join(servers)}")
    if url:
        lines.extend(["", "Общая ссылка подписки:", url])
    if reserve:
        lines.extend(["", "Резервный кабинет:", reserve])
    markup = kb.subscription_detail_keyboard(
        "nav:profile",
        key_actions=[key_button(key, subscription) for key in keys],
        copy_value=None,
        extend_callback=f"buy:extend:{subscription.id}",
        reserve_url=reserve or None,
        qr_callback=f"qr:sub:{subscription.id}",
        reserve_qr_callback=f"qr:reserve:{owner.id}" if owner and reserve else None,
        labels=await self._user_button_labels(),
    )
    await self._safe_edit_message_text(callback.message, "\n".join(lines), markup)
    await self._safe_answer_callback(callback)


async def handle_key_callbacks(self, callback: CallbackQuery):
    data = callback.data or ""
    parts = data.split(":")
    if len(parts) < 3:
        await self._safe_answer_callback(callback)
        return
    action = parts[1]
    key_id = to_int(parts[2], 0)
    key = await self.store.get_key_details(key_id)
    if not key or not getattr(key, "subscription", None):
        await self._safe_answer_callback(callback, "Ключ не найден.", show_alert=True)
        return
    subscription = key.subscription
    owner = getattr(subscription, "user", None)
    actor = await self._admin_actor(callback.from_user)
    is_admin = self._admin_role_value(actor) != "user"
    if not is_admin and getattr(owner, "telegram_id", None) != callback.from_user.id:
        await self._safe_answer_callback(callback, "Это не ваш ключ.", show_alert=True)
        return
    if action == "replace":
        key, subscription, error = await self.provisioning.replace_key(key_id)
        if error or not key:
            await self._safe_answer_callback(callback, safe_text(error, "Не удалось заменить ключ."), show_alert=True)
            return
    elif action == "delete":
        subscription, error = await self.provisioning.delete_expired_key(key_id)
        if error:
            await self._safe_answer_callback(callback, safe_text(error, "Не удалось удалить ключ."), show_alert=True)
            return
        await self._safe_answer_callback(callback, "Ключ удалён.")
        await send_profile(self, callback, callback.from_user, 1)
        return
    elif action != "show":
        await self._safe_answer_callback(callback)
        return
    server = getattr(key, "server", None)
    active = getattr(key, "is_active", False) and subscription_active(subscription)
    lines = [
        f"{'🟢' if active else '🔴'} Ключ",
        "",
        f"Сервер: {server_title(server) if server else 'не указан'}",
        f"Статус: {'активен' if active else 'неактивен'}",
        f"Действует до: {subscription.ends_at:%d.%m.%Y %H:%M}",
        "",
        "VLESS / ссылка ключа:",
        (getattr(key, "access_url", "") or "Ссылка отсутствует."),
    ]
    markup = kb.key_detail_keyboard(
        f"sub:show:{subscription.id}:profile",
        copy_value=None,
        replace_callback=f"key:replace:{key.id}" if active else None,
        delete_callback=f"key:delete:{key.id}" if not subscription_active(subscription) else None,
        extend_callback=f"buy:extend:{subscription.id}",
        qr_callback=f"qr:key:{key.id}",
        labels=await self._user_button_labels(),
    )
    await self._safe_edit_message_text(callback.message, "\n".join(lines), markup)
    await self._safe_answer_callback(callback)


async def assert_admin_callback(self, callback: CallbackQuery) -> bool:
    actor = await self._admin_actor(callback.from_user)
    if self._admin_role_value(actor) == "user":
        await self._safe_answer_callback(callback, "Доступ закрыт.", show_alert=True)
        return False
    return True


def admin_panel_keyboard(role: str = "owner"):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Пользователи", callback_data="adm:users:all:1"), InlineKeyboardButton(text="Серверы", callback_data="adm:servers"))
    builder.row(InlineKeyboardButton(text="Тарифы", callback_data="adm:tariffs"), InlineKeyboardButton(text="Оплаты", callback_data="adm:payments"))
    builder.row(InlineKeyboardButton(text="Финансы", callback_data="adm:finance"), InlineKeyboardButton(text="Аналитика", callback_data="adm:analytics"))
    builder.row(InlineKeyboardButton(text="Тексты", callback_data="adm:texts"), InlineKeyboardButton(text="Резерв", callback_data="adm:reserve"))
    builder.row(InlineKeyboardButton(text="Рефералы", callback_data="adm:referral"), InlineKeyboardButton(text="Trial", callback_data="adm:trial"))
    if role == "owner":
        builder.row(InlineKeyboardButton(text="Роли", callback_data="adm:roles"), InlineKeyboardButton(text="Журнал", callback_data="adm:audit"))
    builder.row(InlineKeyboardButton(text="Бэкапы", callback_data="adm:backup"), InlineKeyboardButton(text="Обновления", callback_data="adm:updates"))
    builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()


def users_list_keyboard(users, filter_key: str, page: int, total: int, page_size: int, filter_counts: dict[str, int] | None = None):
    builder = InlineKeyboardBuilder()
    counts = filter_counts or {}
    labels = {"all": "Все", "active": "Активные", "inactive": "Неактивные", "new": "Новые", "never": "Без покупок"}
    builder.row(InlineKeyboardButton(text=f"{'• ' if filter_key == 'all' else ''}{labels['all']} {counts.get('all', 0)}", callback_data="adm:users:all:1"), InlineKeyboardButton(text=f"{'• ' if filter_key == 'active' else ''}{labels['active']} {counts.get('active', 0)}", callback_data="adm:users:active:1"))
    builder.row(InlineKeyboardButton(text=f"{'• ' if filter_key == 'inactive' else ''}{labels['inactive']} {counts.get('inactive', 0)}", callback_data="adm:users:inactive:1"), InlineKeyboardButton(text=f"{'• ' if filter_key == 'new' else ''}{labels['new']} {counts.get('new', 0)}", callback_data="adm:users:new:1"))
    builder.row(InlineKeyboardButton(text=f"{'• ' if filter_key == 'never' else ''}{labels['never']} {counts.get('never', 0)}", callback_data="adm:users:never:1"))
    for user in users:
        icon = "⛔" if getattr(user, "is_blocked", False) else "👤"
        builder.row(InlineKeyboardButton(text=f"{icon} {display_user(user)[:35]}", callback_data=f"adm:user:show:{user.id}:{filter_key}:{page}"))
    if not users:
        builder.row(InlineKeyboardButton(text="Пользователи не найдены", callback_data="noop"))
    total_pages = max(1, ceil(total / page_size))
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="←", callback_data=f"adm:users:{filter_key}:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="→", callback_data=f"adm:users:{filter_key}:{page + 1}"))
    builder.row(*nav)
    builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data="adm:panel"))
    builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()


def user_actions_keyboard(user_id: int, is_blocked: bool, filter_key: str, page: int, **_kwargs):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Операции", callback_data=f"adm:user:ops:{user_id}:{filter_key}:{page}"), InlineKeyboardButton(text="Рефералы", callback_data=f"adm:user:refs:{user_id}:{filter_key}:{page}"))
    builder.row(InlineKeyboardButton(text="Роль", callback_data=f"adm:user:role:{user_id}:{filter_key}:{page}"), InlineKeyboardButton(text="Диагностика", callback_data=f"adm:user:diag:{user_id}:{filter_key}:{page}"))
    builder.row(InlineKeyboardButton(text=("Разблокировать" if is_blocked else "Заблокировать"), callback_data=f"adm:user:block:{user_id}:{filter_key}:{page}"))
    builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data=f"adm:users:{filter_key}:{page}"))
    builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()


def servers_keyboard(servers):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Проверить все", callback_data="adm:server:refresh"), InlineKeyboardButton(text="Трафик ключей", callback_data="adm:server:usage"))
    for server in servers:
        health = getattr(server, "health_status", "unknown")
        icon = "🟢" if health == "online" else "🔴" if health == "offline" else "⚪"
        builder.row(InlineKeyboardButton(text=f"{icon} {server_title(server)} • CPU {getattr(server, 'cpu_percent', 0)}% • RAM {getattr(server, 'ram_percent', 0)}%", callback_data=f"adm:server:view:{server.id}"))
    builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data="adm:panel"))
    builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()


def server_actions_keyboard(server_id: int, panel_url: str | None = None, agent_configured: bool = False, agent_online: bool = False, billing_configured: bool = False):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Проверить сервер", callback_data=f"adm:server:refreshone:{server_id}"), InlineKeyboardButton(text="В выдачу / скрыть", callback_data=f"adm:server:toggle:{server_id}"))
    builder.row(InlineKeyboardButton(text="Trial on/off", callback_data=f"adm:server:trial:{server_id}"), InlineKeyboardButton(text="История сбоев", callback_data=f"adm:server:failures:{server_id}"))
    if panel_url:
        builder.row(InlineKeyboardButton(text="Открыть панель", url=panel_url))
    builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data="adm:servers"), InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()

async def render_users_admin(self, callback, filter_key: str = "all", page: int = 1):
    users, total = await self.store.list_users(filter_key=filter_key, page=page, page_size=8)
    counts = await self.store.get_user_filter_counts()
    titles = {"all": "Все", "active": "Активные", "inactive": "Неактивные", "never": "Без покупок", "new": "Новые"}
    lines = ["Пользователи", "", f"Фильтр: {titles.get(filter_key, filter_key)}", f"Всего: {total}", f"Страница: {page}", ""]
    if users:
        for user in users:
            status = "⛔" if getattr(user, "is_blocked", False) else "👤"
            lines.append(f"{status} {display_user(user)} • ID {user.id}")
    else:
        lines.append("Пользователи не найдены.")
    await self._safe_edit_message_text(callback.message, "\n".join(lines), kb.users_list_keyboard(users, filter_key, page, total, 8, counts))
    await self._safe_answer_callback(callback)


async def render_user_card(self, callback, user_id: int, filter_key: str = "all", page: int = 1, notice: str | None = None):
    user = await self.store.get_user_admin_summary(user_id)
    if not user:
        await self._safe_answer_callback(callback, "Пользователь не найден.", show_alert=True)
        return
    subscriptions = profile_subscriptions(user)
    active = sum(1 for item in subscriptions if subscription_active(item))
    lines = [
        "Карточка пользователя",
        "",
        f"ID: {user.id}",
        f"Telegram ID: {user.telegram_id}",
        f"Пользователь: {display_user(user)}",
        f"Роль: {role_title(getattr(user, 'admin_role', 'user'))}",
        f"Статус: {'заблокирован' if getattr(user, 'is_blocked', False) else 'активен'}",
        f"Баланс: {money(getattr(user, 'balance', Decimal('0')))}",
        f"Подписок: {len(subscriptions)}",
        f"Активных подписок: {active}",
    ]
    if subscriptions:
        lines.extend(["", "Последние подписки:"])
        for sub in subscriptions[:5]:
            lines.append(f"• {subscription_button(sub)[0]}")
    await self._safe_edit_message_text(callback.message, "\n".join(lines), kb.user_actions_keyboard(user.id, getattr(user, "is_blocked", False), filter_key, page, can_manage_role=True))
    await self._safe_answer_callback(callback, notice)


async def render_servers_admin(self, callback):
    servers = await self.store.list_servers_with_monitoring()
    online = sum(1 for server in servers if getattr(server, "health_status", "") == "online")
    enabled = sum(1 for server in servers if getattr(server, "is_enabled", False))
    lines = ["Серверы", "", f"Online: {online}/{len(servers)}", f"В выдаче: {enabled}", ""]
    if not servers:
        lines.append("Серверы не добавлены.")
    for server in servers:
        health = getattr(server, "health_status", "unknown")
        icon = "🟢" if health == "online" else "🔴" if health == "offline" else "⚪"
        lines.append(f"{icon} {server_title(server)} • CPU {getattr(server, 'cpu_percent', 0)}% • RAM {getattr(server, 'ram_percent', 0)}%")
        if getattr(server, "last_error", ""):
            lines.append(f"Ошибка: {safe_text(getattr(server, 'last_error', ''), 'не указана')}")
    await self._safe_edit_message_text(callback.message, "\n".join(lines), kb.servers_keyboard(servers))
    await self._safe_answer_callback(callback)


async def render_server_card(self, callback, server_id: int, notice: str | None = None):
    server = await self.store.get_server(server_id)
    if not server:
        await self._safe_answer_callback(callback, "Сервер не найден.", show_alert=True)
        return
    billing = await self.store.get_server_billing_config(server_id)
    lines = [
        f"Сервер: {server_title(server)}",
        "",
        f"Адрес панели: {getattr(server, 'base_url', '')}",
        f"В выдаче: {'да' if getattr(server, 'is_enabled', False) else 'нет'}",
        f"Trial: {'да' if getattr(server, 'is_trial_available', False) else 'нет'}",
        f"Статус: {getattr(server, 'health_status', 'unknown')}",
        f"CPU: {getattr(server, 'cpu_percent', 0)}%",
        f"RAM: {getattr(server, 'ram_percent', 0)}%",
    ]
    if getattr(server, "last_checked_at", None):
        lines.append(f"Проверка: {server.last_checked_at:%d.%m.%Y %H:%M}")
    if getattr(server, "last_error", ""):
        lines.append(f"Ошибка: {safe_text(server.last_error, 'не указана')}")
    if billing:
        lines.extend(["", f"Оплата сервера: {money(billing.get('amount_rub'))}", f"Следующая дата: {billing.get('next_due') or 'не указана'}"])
    await self._safe_edit_message_text(callback.message, "\n".join(lines), kb.server_actions_keyboard(server.id, panel_url=getattr(server, "base_url", ""), billing_configured=bool(billing and billing.get("amount_rub"))))
    await self._safe_answer_callback(callback, notice)


async def handle_admin_callbacks(self, callback: CallbackQuery, state):
    if not await self._assert_admin_callback(callback):
        return
    await state.clear()
    actor = await self._admin_actor(callback.from_user)
    role = self._admin_role_value(actor)
    data = callback.data or ""
    parts = data.split(":")
    if data == "adm:panel":
        await self._safe_edit_message_text(callback.message, await self._admin_panel_text(role), kb.admin_panel_keyboard(role))
        await self._safe_answer_callback(callback)
        return
    if data in {"adm:users", "adm:users:filters"} or (len(parts) >= 4 and parts[:2] == ["adm", "users"]):
        filter_key = parts[2] if len(parts) >= 3 and parts[2] not in {"", "filters"} else "all"
        page = to_int(parts[3], 1) if len(parts) >= 4 else 1
        await render_users_admin(self, callback, filter_key, page)
        return
    if len(parts) >= 4 and parts[:2] == ["adm", "user"]:
        action = parts[2]
        user_id = to_int(parts[3], 0)
        filter_key = parts[4] if len(parts) >= 5 else "all"
        page = to_int(parts[5], 1) if len(parts) >= 6 else 1
        if action == "block":
            await self.store.toggle_user_blocked(user_id)
            await render_user_card(self, callback, user_id, filter_key, page, "Статус изменён.")
            return
        if action in {"ops", "refs", "diag", "role", "balance", "key"}:
            await render_user_card(self, callback, user_id, filter_key, page, "Раздел открыт в безопасном режиме.")
            return
        await render_user_card(self, callback, user_id, filter_key, page)
        return
    if data == "adm:servers":
        await render_servers_admin(self, callback)
        return
    if data == "adm:server:refresh":
        await self.provisioning.refresh_servers()
        await render_servers_admin(self, callback)
        return
    if data == "adm:server:usage":
        await self.provisioning.refresh_key_usage()
        await self._safe_answer_callback(callback, "Трафик ключей обновлён.")
        return
    if len(parts) >= 4 and parts[:3] == ["adm", "server", "view"]:
        await render_server_card(self, callback, to_int(parts[3], 0))
        return
    if len(parts) >= 4 and parts[:3] == ["adm", "server", "refreshone"]:
        server_id = to_int(parts[3], 0)
        await self.provisioning.refresh_server(server_id)
        await render_server_card(self, callback, server_id, "Сервер проверен.")
        return
    if len(parts) >= 4 and parts[:3] == ["adm", "server", "toggle"]:
        server_id = to_int(parts[3], 0)
        await self.store.toggle_server_enabled(server_id)
        await render_server_card(self, callback, server_id, "Статус выдачи изменён.")
        return
    if len(parts) >= 4 and parts[:3] == ["adm", "server", "trial"]:
        server_id = to_int(parts[3], 0)
        await self.store.toggle_server_trial(server_id)
        await render_server_card(self, callback, server_id, "Trial изменён.")
        return
    if len(parts) >= 4 and parts[:3] == ["adm", "server", "failures"]:
        server_id = to_int(parts[3], 0)
        failures = await self.store.list_provisioning_failures(server_id=server_id, limit=15)
        server = await self.store.get_server(server_id)
        lines = [f"История сбоев: {server_title(server) if server else f'Сервер #{server_id}'}", ""]
        if not failures:
            lines.append("Сбоев не найдено.")
        for item in failures:
            lines.append(f"• {item.created_at:%d.%m %H:%M} — {safe_text(getattr(item, 'error', ''), 'ошибка без текста')}")
        await self._safe_edit_message_text(callback.message, "\n".join(lines), kb.back_keyboard(f"adm:server:view:{server_id}"))
        await self._safe_answer_callback(callback)
        return
    if data.startswith("adm:server:"):
        await self._safe_answer_callback(callback, "Это действие сейчас скрыто от выполнения.", show_alert=True)
        return
    if data == "adm:tariffs":
        tariffs = await self.store.list_tariffs(only_active=False)
        lines = ["Тарифы", ""]
        if not tariffs:
            lines.append("Тарифы не созданы.")
        for tariff in tariffs:
            status = "активен" if getattr(tariff, "is_active", False) else "скрыт"
            lines.append(f"• {tariff_title(tariff)} — {int(tariff.days)} дн. — {money(tariff.price_rub)} — {status}")
        await self._safe_edit_message_text(callback.message, "\n".join(lines), kb.tariffs_admin_keyboard(tariffs))
        await self._safe_answer_callback(callback)
        return
    if len(parts) >= 4 and parts[:3] == ["adm", "tariff", "view"]:
        tariff = await self.store.get_tariff(to_int(parts[3], 0))
        if not tariff:
            await self._safe_answer_callback(callback, "Тариф не найден.", show_alert=True)
            return
        text = "\n".join(["Тариф", "", f"Название: {tariff_title(tariff)}", f"Дней: {tariff.days}", f"Цена: {money(tariff.price_rub)}", f"Stars: {getattr(tariff, 'price_stars', 0)}", f"Статус: {'активен' if tariff.is_active else 'скрыт'}", "", safe_text(getattr(tariff, "description", ""), "")])
        await self._safe_edit_message_text(callback.message, text, kb.tariff_detail_keyboard(tariff.id, tariff.is_active))
        await self._safe_answer_callback(callback)
        return
    if len(parts) >= 4 and parts[:3] == ["adm", "tariff", "toggle"]:
        tariff = await self.store.toggle_tariff(to_int(parts[3], 0))
        if tariff:
            text = "\n".join(["Тариф", "", f"Название: {tariff_title(tariff)}", f"Дней: {tariff.days}", f"Цена: {money(tariff.price_rub)}", f"Stars: {getattr(tariff, 'price_stars', 0)}", f"Статус: {'активен' if tariff.is_active else 'скрыт'}", "", safe_text(getattr(tariff, "description", ""), "")])
            await self._safe_edit_message_text(callback.message, text, kb.tariff_detail_keyboard(tariff.id, tariff.is_active))
            await self._safe_answer_callback(callback, "Статус тарифа изменён.")
        return
    if len(parts) >= 4 and parts[:3] == ["adm", "tariff", "delete"]:
        ok, message = await self.store.delete_tariff(to_int(parts[3], 0))
        await self._safe_answer_callback(callback, safe_text(message, "Готово." if ok else "Не удалось удалить тариф."), show_alert=not ok)
        tariffs = await self.store.list_tariffs(only_active=False)
        lines = ["Тарифы", ""]
        if not tariffs:
            lines.append("Тарифы не созданы.")
        for tariff in tariffs:
            status = "активен" if getattr(tariff, "is_active", False) else "скрыт"
            lines.append(f"• {tariff_title(tariff)} — {int(tariff.days)} дн. — {money(tariff.price_rub)} — {status}")
        await self._safe_edit_message_text(callback.message, "\n".join(lines), kb.tariffs_admin_keyboard(tariffs))
        return
    if data.startswith("adm:tariff:"):
        await self._safe_answer_callback(callback, "Создание и редактирование требуют текстового сценария и сейчас не выполняются.", show_alert=True)
        return
    if data == "adm:payments":
        config = await self.store.get_payment_settings_snapshot()
        lines = ["Оплаты", "", f"Баланс: {'включён' if await self.store.get_toggle('payment_balance', True) else 'выключен'}", f"YooKassa: {'настроена' if config.get('yookassa_shop_id') and config.get('yookassa_secret_key') else 'не настроена'}", f"Crypto: {'настроен' if config.get('crypto_pay_token') else 'не настроен'}"]
        await self._safe_edit_message_text(callback.message, "\n".join(lines), kb.back_keyboard("adm:panel"))
        await self._safe_answer_callback(callback)
        return
    if data in {"adm:analytics", "adm:finance"}:
        analytics = await self.store.get_analytics_snapshot()
        paid_month = analytics.get("paid_amount_month") or analytics.get("revenue_month") or Decimal("0")
        costs_month = analytics.get("server_costs_month") or Decimal("0")
        try:
            result = Decimal(str(paid_month or 0)) - Decimal(str(costs_month or 0))
        except Exception:
            result = Decimal("0")
        lines = ["Финансы и аналитика" if data == "adm:finance" else "Аналитика", "", f"Пользователей: {analytics.get('total_users', 0)}", f"Платящих: {analytics.get('paying_users', 0)}", f"Доход за 30 дней: {money(paid_month)}", f"Расходы серверов: {money(costs_month)}", f"Итог: {money(result)}"]
        await self._safe_edit_message_text(callback.message, "\n".join(lines), kb.back_keyboard("adm:panel"))
        await self._safe_answer_callback(callback)
        return
    if data.startswith("adm:texts") or data.startswith("adm:text:"):
        await self._safe_edit_message_text(callback.message, "Тексты и кнопки\n\nРаздел доступен, но редактирование оставлено в безопасном режиме до полной миграции.", kb.back_keyboard("adm:panel"))
        await self._safe_answer_callback(callback)
        return
    if data == "adm:roles":
        users = await self.store.list_admin_users()
        lines = ["Роли", ""]
        if not users:
            lines.append("Администраторы не назначены.")
        for user in users:
            lines.append(f"• {display_user(user)} — {role_title(self._admin_role_value(user))}")
        await self._safe_edit_message_text(callback.message, "\n".join(lines), kb.back_keyboard("adm:panel"))
        await self._safe_answer_callback(callback)
        return
    if data == "adm:audit":
        logs = await self.store.list_admin_action_logs(limit=20)
        lines = ["Журнал действий", ""]
        if not logs:
            lines.append("Записей пока нет.")
        for item in logs:
            lines.append(f"• {item.created_at:%d.%m %H:%M} — {safe_text(getattr(item, 'action', ''), 'действие')}")
        await self._safe_edit_message_text(callback.message, "\n".join(lines), kb.back_keyboard("adm:panel"))
        await self._safe_answer_callback(callback)
        return
    if data in {"adm:referral", "adm:trial", "adm:reserve"}:
        key = {"adm:referral": "section_referral", "adm:trial": "section_trial", "adm:reserve": "section_reserve_access"}[data]
        visible = await self.store.get_toggle(key, True)
        title = {"adm:referral": "Рефералы", "adm:trial": "Пробный доступ", "adm:reserve": "Резервный кабинет"}[data]
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=f"Раздел • {'показан' if visible else 'скрыт'}", callback_data=f"adm:toggle:{key}"))
        builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data="adm:panel"))
        builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
        await self._safe_edit_message_text(callback.message, f"{title}\n\nРаздел: {'показан' if visible else 'скрыт'}", builder.as_markup())
        await self._safe_answer_callback(callback)
        return
    if data.startswith("adm:toggle:"):
        enabled = await self.store.toggle_feature(data.split(":", 2)[2])
        await self._safe_edit_message_text(callback.message, await self._admin_panel_text(role), kb.admin_panel_keyboard(role))
        await self._safe_answer_callback(callback, "Включено." if enabled else "Выключено.")
        return
    if data == "adm:backup":
        await self._safe_edit_message_text(callback.message, "Резервные копии\n\nМожно создать свежий backup базы.", kb.backup_keyboard())
        await self._safe_answer_callback(callback)
        return
    if data == "adm:backup:run":
        backup_path = await self.backups.create_backup()
        await self._safe_edit_message_text(callback.message, f"Backup создан:\n{backup_path}", kb.backup_keyboard())
        await self._safe_answer_callback(callback, "Backup создан.")
        return
    if data.startswith("adm:updates"):
        if data == "adm:updates:run" and self.updater:
            await self.updater.trigger_update()
            await self._safe_answer_callback(callback, "Обновление запущено.")
            return
        latest = await self.updater.check_updates() if self.updater else None
        text = "Обновления\n\n"
        text += safe_text(getattr(latest, "summary", None), "Проверка обновлений доступна.") if latest else "Сервис обновлений не настроен или обновлений нет."
        await self._safe_edit_message_text(callback.message, text, kb.updates_admin_keyboard(bool(self.updater), bool(latest)))
        await self._safe_answer_callback(callback)
        return
    await self._safe_answer_callback(callback, "Этот раздел пока не подключён в безопасном режиме.", show_alert=True)


def update_notice_keyboard(can_trigger: bool):
    builder = InlineKeyboardBuilder()
    if can_trigger:
        builder.row(InlineKeyboardButton(text="Обновить бота", callback_data="adm:updates:run"))
    builder.row(InlineKeyboardButton(text="Открыть раздел обновлений", callback_data="adm:updates"))
    return builder.as_markup()


def patch_main_symbols(namespace: dict) -> None:
    namespace["update_notice_keyboard"] = update_notice_keyboard
    namespace["access_result_keyboard"] = kb.access_result_keyboard
    namespace["build_subscription_action_rows"] = lambda subscription: [subscription_button(subscription)]
    namespace["display_user_name"] = display_user
    namespace["render_payment_activation_message"] = lambda subscription, vpn_keys, extended=False, reserve_url="": activation_text(subscription, vpn_keys, reserve_url, extended)
    namespace["render_gift_purchase_activation_message"] = lambda subscription: "\n".join(["Подарочный доступ активирован", "", f"Получатель: {display_user(getattr(subscription, 'user', None))}", f"Тариф: {subscription_title(subscription)}", f"Действует до: {subscription.ends_at:%d.%m.%Y %H:%M}"])
    namespace["render_abandoned_payment_message"] = lambda payment: "\n".join(["Оплата ещё не завершена", "", f"Тариф: {tariff_title(payment.tariff) if getattr(payment, 'tariff', None) else 'Подписка'}", f"Сумма: {money(payment.amount, payment.currency)}", "", "Откройте прежнюю ссылку и завершите оплату."])


def apply_stable_runtime() -> None:
    BotController._safe_edit_message_text = safe_edit
    BotController._safe_answer_callback = safe_answer
    BotController._send_inline_screen = safe_send_inline
    BotController._ui_snapshot = ui_snapshot
    BotController._user_button_labels = user_labels
    BotController._home_inline_markup = home_markup
    BotController._render_home_text = render_home
    BotController._render_buy_text = render_buy
    BotController._render_help_text = render_help
    BotController._render_referral_text = render_referral
    BotController._render_trial_text = render_trial
    BotController._admin_panel_text = admin_panel_text
    BotController._assert_admin_callback = assert_admin_callback
    BotController.start = start
    BotController.show_menu = show_menu
    BotController.show_profile = show_profile
    BotController.show_buy = show_buy
    BotController.show_help = show_help
    BotController.show_referrals = show_referrals
    BotController.show_trial = show_trial
    BotController.show_admin_panel_message = show_admin
    BotController.fallback_message = fallback_message
    BotController.receive_promo_code = receive_promo_code
    BotController.handle_buy_callbacks = handle_buy_callbacks
    BotController.handle_nav_callbacks = handle_nav_callbacks
    BotController.handle_help_callbacks = handle_help_callbacks
    BotController.handle_trial_callbacks = handle_trial_callbacks
    BotController.handle_subscription_callbacks = handle_subscription_callbacks
    BotController.handle_key_callbacks = handle_key_callbacks
    BotController.handle_admin_callbacks = handle_admin_callbacks
    for module in (kb, controller_module):
        module.admin_panel_keyboard = admin_panel_keyboard
        module.users_list_keyboard = users_list_keyboard
        module.user_actions_keyboard = user_actions_keyboard
        module.servers_keyboard = servers_keyboard
        module.server_actions_keyboard = server_actions_keyboard
        module.update_notice_keyboard = update_notice_keyboard

