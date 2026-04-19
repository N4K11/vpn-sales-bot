from __future__ import annotations

from aiogram.types import InlineKeyboardButton, LinkPreviewOptions, Message, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder

import app.bot.keyboards as kb
from app.bot.controller import BotController
from app.config import settings
from app.utils import format_money

BROKEN_MARKERS = ("Р ", "РЎ", "Ð", "Ñ", "вЂ", "�")


def repair_text(value):
    if value is None:
        return value
    repaired = str(value)
    for _ in range(3):
        try:
            candidate = repaired.encode("cp1251").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if candidate == repaired:
            break
        repaired = candidate
    return repaired


def looks_broken_text(value: str | None) -> bool:
    if not value:
        return False
    text = str(value).strip()
    if not text:
        return False
    if any(marker in text for marker in BROKEN_MARKERS):
        return True
    return text.count("?") >= max(3, len(text) // 3)


def clean_button_labels(labels: dict[str, str] | None = None) -> dict[str, str]:
    merged = kb.DEFAULT_USER_BUTTON_LABELS.copy()
    for key, value in (labels or {}).items():
        cleaned = (repair_text(value) or "").strip() if value else ""
        if cleaned and not looks_broken_text(cleaned):
            merged[key] = cleaned
    return merged


def _body_or_fallback(page, fallback: str) -> str:
    body = (getattr(page, "body", "") or "").strip() if page else ""
    body = repair_text(body) if body else ""
    if not body or looks_broken_text(body):
        return fallback
    return body


async def _patched_send_inline_screen(self: BotController, message: Message, text: str, reply_markup) -> None:
    cleanup = await message.answer("\u2060", reply_markup=ReplyKeyboardRemove())
    try:
        await cleanup.delete()
    except Exception:
        pass
    await message.answer(
        self._repair_ui_text(repair_text(text)) or "",
        reply_markup=reply_markup,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


async def _patched_ui_snapshot(self: BotController) -> dict:
    snapshot = await _ORIGINAL_UI_SNAPSHOT(self)
    result = dict(snapshot or {})
    result["button_labels"] = clean_button_labels(result.get("button_labels") or {})
    return result


async def _patched_user_button_labels(self: BotController, ui: dict | None = None) -> dict[str, str]:
    ui = ui or await self._ui_snapshot()
    return clean_button_labels((ui or {}).get("button_labels") or {})


def _patched_admin_role_title(self: BotController, role: str) -> str:
    return {
        "owner": "Владелец",
        "admin": "Администратор",
        "support": "Поддержка",
        "finance": "Финансы",
        "ops": "Операции",
        "user": "Пользователь",
    }.get((role or "").strip().lower(), "Пользователь")


def _patched_payment_method_title(self: BotController, method: str) -> str:
    return {
        "stars": "Telegram Stars",
        "yookassa": "YooKassa",
        "crypto": "Crypto",
        "balance": "Баланс аккаунта",
    }.get(method, method)


def _patched_tariff_upsell_lines(self: BotController, current_tariff, tariffs) -> list[str]:
    if not current_tariff or not tariffs:
        return []
    candidates = [item for item in tariffs if getattr(item, "days", 0) > getattr(current_tariff, "days", 0)]
    if not candidates:
        return []
    target = sorted(candidates, key=lambda item: getattr(item, "days", 0))[0]
    return ["", f"Подсказка: {target.name} обычно выгоднее по цене за день и даёт больше запаса по сроку."]


def _patched_render_tariff_lines(self: BotController, tariffs) -> list[str]:
    lines: list[str] = []
    starter = self._starter_tariff(tariffs)
    popular = self._popular_tariff(tariffs)
    best_value = self._best_value_tariff(tariffs)
    for tariff in tariffs:
        badges: list[str] = []
        if starter and tariff.id == starter.id:
            badges.append("старт")
        if popular and tariff.id == popular.id:
            badges.append("популярный")
        if best_value and tariff.id == best_value.id:
            badges.append("выгодный")
        badge_suffix = f" • {' • '.join(badges)}" if badges else ""
        rub = repair_text(format_money(getattr(tariff, "price_rub", 0)))
        stars = int(getattr(tariff, "price_stars", 0) or 0)
        stars_suffix = f" / {stars} ⭐" if stars > 0 else ""
        lines.append(f"• {tariff.name} — {tariff.days} дн. — {rub}{stars_suffix}{badge_suffix}")
    return lines


async def _patched_render_home_text(self: BotController) -> str:
    page = await self.store.get_content("main")
    tariffs = await self.store.list_tariffs(only_active=True)
    intro = _body_or_fallback(page, f"{self._brand_name()} — быстрый доступ к профилю, оплате и подключению.")
    lines = [
        f"{self._brand_name()} | личный кабинет",
        "",
        intro,
        "",
        "Что внутри:",
        "• профиль и действующие доступы;",
        "• одна ссылка доступа на все включённые серверы;",
        "• продление, резервный кабинет и инструкции по подключению.",
    ]
    if tariffs:
        lines.extend(["", "Тарифы:"])
        lines.extend(self._render_tariff_lines(tariffs))
    lines.extend(["", "Выберите раздел ниже."])
    return "\n".join(lines)


async def _patched_render_buy_text(self: BotController, tariffs) -> str:
    page = await self.store.get_content("buy")
    intro = _body_or_fallback(page, "Выберите тариф, а затем удобный способ оплаты.")
    methods = await self._visible_payment_methods()
    method_titles = [self._payment_method_title(method) for method in methods]
    lines = [
        "Подключить Air",
        "",
        intro,
        "",
    ]
    if method_titles:
        lines.append(f"Способы оплаты: {', '.join(method_titles)}")
    if tariffs:
        lines.extend(["", "Доступные тарифы:"])
        lines.extend(self._render_tariff_lines(tariffs))
    return "\n".join(lines)


async def _patched_render_help_text(self: BotController) -> str:
    page = await self.store.get_content("help")
    intro = _body_or_fallback(page, "Здесь собраны быстрые ответы, канал и поддержка.")
    lines = [
        "Справка Air",
        "",
        intro,
        "",
        "Что можно сделать внутри бота:",
        "• открыть профиль и свои доступы;",
        "• скопировать ссылку доступа;",
        "• продлить срок или открыть резервный кабинет;",
        "• посмотреть короткие инструкции по подключению.",
    ]
    return "\n".join(lines)


async def _patched_render_device_guides_menu(self: BotController) -> str:
    page = await self.store.get_content("devices_menu")
    intro = _body_or_fallback(page, "Выберите устройство и откройте короткую инструкцию по подключению.")
    return "\n".join(["Как подключить Air", "", intro])


async def _patched_render_device_guide(self: BotController, platform_key: str) -> str:
    titles = {
        "ios": "iPhone / iPad",
        "android": "Android",
        "windows": "Windows",
        "macos": "macOS",
    }
    content_keys = {
        "ios": "guide_ios",
        "android": "guide_android",
        "windows": "guide_windows",
        "macos": "guide_macos",
    }
    defaults = {
        "ios": "1. Установите совместимый клиент.\n2. Скопируйте ссылку доступа в профиле.\n3. Импортируйте ссылку как Subscription URL.\n4. Обновите профиль в клиенте.",
        "android": "1. Установите совместимый клиент.\n2. Скопируйте ссылку доступа в профиле.\n3. Добавьте её как Subscription URL.\n4. Выберите нужный сервер и подключитесь.",
        "windows": "1. Откройте клиент на Windows.\n2. Скопируйте ссылку доступа в профиле.\n3. Импортируйте её как Subscription URL.\n4. Обновите список серверов и подключитесь.",
        "macos": "1. Откройте клиент на macOS.\n2. Скопируйте ссылку доступа в профиле.\n3. Импортируйте её как Subscription URL.\n4. Обновите список серверов и подключитесь.",
    }
    title = titles.get(platform_key, "Устройство")
    page = await self.store.get_content(content_keys.get(platform_key, "")) if platform_key in content_keys else None
    body = _body_or_fallback(page, defaults.get(platform_key, "Откройте профиль и импортируйте ссылку доступа в клиент."))
    return "\n".join([title, "", body])


async def _patched_render_referral_text(self: BotController, user) -> str:
    page = await self.store.get_content("referral")
    ui = await self._ui_snapshot()
    intro = _body_or_fallback(page, "Приглашайте друзей и получайте бонусы на внутренний баланс.")
    referrals = getattr(user, "referrals", None) or []
    invite_link = self._invite_link(user)
    balance = repair_text(format_money(getattr(user, "balance", 0)))
    percent = ui.get("referral_percent", settings.referral_percent)
    return "\n".join([
        "Партнёрская программа",
        "",
        intro,
        "",
        f"Вознаграждение: {percent}% с каждой оплаты реферала",
        f"Приглашено пользователей: {len(referrals)}",
        f"Накоплено бонусами: {balance}",
        "",
        "Ваша персональная ссылка:",
        invite_link,
    ])


async def _patched_render_trial_text(self: BotController, user) -> str:
    page = await self.store.get_content("trial")
    ui = await self._ui_snapshot()
    intro = _body_or_fallback(page, "Здесь можно активировать тестовый доступ, если он открыт администратором.")
    trial_days = int(ui.get("trial_days") or settings.trial_default_days)
    trial_servers = await self.store.list_balanced_servers(trial_only=True)
    lines = [
        "Тестовый доступ",
        "",
        intro,
        "",
        f"Срок доступа: {trial_days} дн.",
        f"Доступно trial-серверов: {len(trial_servers)}",
        "",
    ]
    if getattr(user, "trial_claimed", False):
        lines.append("Пробный доступ уже был использован для этого аккаунта.")
    elif not trial_servers:
        lines.append("Сейчас нет серверов, доступных для trial.")
    else:
        lines.append("Нажмите кнопку ниже, чтобы активировать пробный период.")
    return "\n".join(lines)


def _patched_updates_admin_keyboard(can_trigger: bool, update_available: bool = False):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🧾 Открыть журнал обновлений", callback_data="adm:updates"))
    if can_trigger:
        builder.row(InlineKeyboardButton(text=("♻️ Установить обновление" if update_available else "🔎 Проверить обновления"), callback_data="adm:updates:run"))
    builder.row(InlineKeyboardButton(text=kb.BACK_LABEL, callback_data="adm:panel"))
    builder.row(InlineKeyboardButton(text=kb.HOME_LABEL, callback_data="nav:home"))
    return builder.as_markup()


def _patched_update_notice_keyboard(can_trigger: bool):
    builder = InlineKeyboardBuilder()
    if can_trigger:
        builder.row(InlineKeyboardButton(text="♻️ Обновить бота", callback_data="adm:updates:run"))
    builder.row(InlineKeyboardButton(text="🧾 Открыть раздел обновлений", callback_data="adm:updates"))
    return builder.as_markup()


def patch_main_symbols(namespace: dict) -> None:
    def _wrap(name: str):
        original = namespace.get(name)
        if not callable(original):
            return
        def _wrapped(*args, __original=original, **kwargs):
            result = __original(*args, **kwargs)
            if isinstance(result, str):
                return repair_text(result)
            if isinstance(result, list):
                return [repair_text(item) if isinstance(item, str) else item for item in result]
            return result
        namespace[name] = _wrapped

    for func_name in (
        "build_subscription_action_rows",
        "display_user_name",
        "render_subscription_link_lines",
        "render_payment_activation_message",
        "render_gift_purchase_activation_message",
        "render_abandoned_payment_message",
        "render_expiry_warning_message",
        "render_provisioning_alert_message",
        "render_update_notification",
    ):
        _wrap(func_name)
    namespace["update_notice_keyboard"] = kb.update_notice_keyboard


_ORIGINAL_UI_SNAPSHOT = BotController._ui_snapshot


def apply_ui_hotfixes() -> None:
    kb._user_labels = clean_button_labels
    kb.updates_admin_keyboard = _patched_updates_admin_keyboard
    kb.update_notice_keyboard = _patched_update_notice_keyboard
    BotController._ui_snapshot = _patched_ui_snapshot
    BotController._user_button_labels = _patched_user_button_labels
    BotController._send_inline_screen = _patched_send_inline_screen
    BotController._admin_role_title = _patched_admin_role_title
    BotController._payment_method_title = _patched_payment_method_title
    BotController._tariff_upsell_lines = _patched_tariff_upsell_lines
    BotController._render_tariff_lines = _patched_render_tariff_lines
    BotController._render_home_text = _patched_render_home_text
    BotController._render_buy_text = _patched_render_buy_text
    BotController._render_help_text = _patched_render_help_text
    BotController._render_device_guides_menu = _patched_render_device_guides_menu
    BotController._render_device_guide = _patched_render_device_guide
    BotController._render_referral_text = _patched_render_referral_text
    BotController._render_trial_text = _patched_render_trial_text


