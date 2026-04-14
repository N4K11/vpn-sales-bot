from __future__ import annotations

from math import ceil
from urllib.parse import urlsplit

from aiogram.types import CopyTextButton, InlineKeyboardButton, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

PROFILE_LABEL = '👤 Мой профиль'
BUY_LABEL = '🛒 Купить VPN'
HELP_LABEL = '❓ Справка'
REFERRAL_LABEL = '🎁 Рефералы'
TRIAL_LABEL = '🧪 Пробный доступ'
ADMIN_LABEL = '⚙️ Админ-панель'
HOME_LABEL = '🏠 Главное меню'
BACK_LABEL = '◀ Назад'

DEFAULT_USER_BUTTON_LABELS = {
    'nav_profile': PROFILE_LABEL,
    'nav_buy': BUY_LABEL,
    'nav_help': HELP_LABEL,
    'nav_referral': REFERRAL_LABEL,
    'nav_trial': TRIAL_LABEL,
    'nav_home': HOME_LABEL,
    'nav_back': BACK_LABEL,
    'help_channel': '📣 Канал',
    'help_support': '🆘 Поддержка',
    'referral_copy': '📋 Скопировать ссылку',
    'trial_activate': '🚀 Активировать пробный период',
    'help_devices': '📱 Как подключить',
    'guide_ios': '📱 iPhone / iPad',
    'guide_android': '🤖 Android',
    'guide_windows': '🪟 Windows',
    'guide_macos': '🍎 macOS',
    'pay_stars': '⭐ Telegram Stars',
    'pay_yookassa': '💳 YooKassa',
    'pay_crypto': '🪙 Crypto',
    'pay_balance': '💰 С баланса',
    'pay_open_invoice': '💳 Перейти к оплате',
    'subscription_copy': '📋 Скопировать ссылку',
    'subscription_qr': '📷 QR подписки',
    'subscription_extend': '🕒 Продлить подписку',
    'reserve_open': '🌍 Резервный кабинет',
    'reserve_copy': '📋 Скопировать резервную ссылку',
    'reserve_qr': '📷 QR резерва',
    'key_copy': '📋 Скопировать ключ',
    'key_qr': '📷 QR ключа',
    'key_replace': '♻️ Заменить ключ',
    'key_delete': '🗑️ Удалить ключ',
}


def _user_labels(labels: dict[str, str] | None = None) -> dict[str, str]:
    merged = DEFAULT_USER_BUTTON_LABELS.copy()
    if labels:
        for key, value in labels.items():
            if value:
                merged[key] = value
    return merged


def build_main_menu(is_admin: bool, show_referral: bool, show_trial: bool, labels: dict[str, str] | None = None) -> ReplyKeyboardMarkup:
    lb = _user_labels(labels)
    rows: list[list[KeyboardButton]] = [
        [KeyboardButton(text=lb['nav_profile']), KeyboardButton(text=lb['nav_buy'])],
        [KeyboardButton(text=lb['nav_help'])],
    ]
    if show_referral:
        rows.append([KeyboardButton(text=lb['nav_referral'])])
    if show_trial:
        rows.append([KeyboardButton(text=lb['nav_trial'])])
    if is_admin:
        rows.append([KeyboardButton(text=ADMIN_LABEL)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, input_field_placeholder='Выберите раздел')


def _append_copy_rows(builder: InlineKeyboardBuilder, items: list[tuple[str, str]]) -> None:
    for label, text in items:
        value = (text or '').strip()
        if not value or len(value) > 256:
            continue
        builder.row(InlineKeyboardButton(text=label, copy_text=CopyTextButton(text=value)))



def _copy_link_label(value: str, limit: int = 44) -> str:
    text = (value or '').strip()
    if not text:
        return ''
    display = text
    try:
        parsed = urlsplit(text)
    except ValueError:
        parsed = None
    if parsed and parsed.scheme and parsed.netloc:
        display = f'{parsed.scheme}://{parsed.netloc}{parsed.path}'
        if parsed.query:
            display = f'{display}?...'
    if len(display) <= limit:
        return display
    return display[: limit - 1] + '…'

def _append_compact_action_rows(builder: InlineKeyboardBuilder, items: list[tuple[str, str]], width: int = 2) -> None:
    row: list[InlineKeyboardButton] = []
    for label, callback_data in items:
        row.append(InlineKeyboardButton(text=label, callback_data=callback_data))
        if len(row) == width:
            builder.row(*row)
            row = []
    if row:
        builder.row(*row)


def copy_items_keyboard(items: list[tuple[str, str]]):
    builder = InlineKeyboardBuilder()
    _append_copy_rows(builder, items)
    return builder.as_markup() if builder.export() else None


def home_inline_keyboard(is_admin: bool, show_referral: bool, show_trial: bool, labels: dict[str, str] | None = None):
    lb = _user_labels(labels)
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=lb['nav_profile'], callback_data='nav:profile'), InlineKeyboardButton(text=lb['nav_buy'], callback_data='nav:buy'))
    main_row: list[InlineKeyboardButton] = [InlineKeyboardButton(text=lb['nav_help'], callback_data='nav:help')]
    if show_referral:
        main_row.append(InlineKeyboardButton(text=lb['nav_referral'], callback_data='nav:referral'))
    builder.row(*main_row)
    footer: list[InlineKeyboardButton] = []
    if show_trial:
        footer.append(InlineKeyboardButton(text=lb['nav_trial'], callback_data='nav:trial'))
    if is_admin:
        footer.append(InlineKeyboardButton(text=ADMIN_LABEL, callback_data='nav:admin'))
    if footer:
        builder.row(*footer)
    return builder.as_markup()


def profile_inline_keyboard(subscription_actions: list[tuple[str, str]], is_admin: bool, show_referral: bool, show_trial: bool, labels: dict[str, str] | None = None, page: int = 1, total_pages: int = 1):
    lb = _user_labels(labels)
    builder = InlineKeyboardBuilder()
    if subscription_actions:
        _append_compact_action_rows(builder, subscription_actions, width=1)
    if total_pages > 1:
        pagination: list[InlineKeyboardButton] = []
        if page > 1:
            pagination.append(InlineKeyboardButton(text='◀️', callback_data=f'nav:profile:{page - 1}'))
        pagination.append(InlineKeyboardButton(text=f'{page}/{total_pages}', callback_data='noop'))
        if page < total_pages:
            pagination.append(InlineKeyboardButton(text='▶️', callback_data=f'nav:profile:{page + 1}'))
        builder.row(*pagination)
    builder.row(InlineKeyboardButton(text=lb['nav_back'], callback_data='nav:home'))
    return builder.as_markup()


def help_inline_keyboard(channel_url: str, support_url: str, terms_url: str, is_admin: bool, show_referral: bool, show_trial: bool, labels: dict[str, str] | None = None):
    lb = _user_labels(labels)
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=lb['help_channel'], url=channel_url), InlineKeyboardButton(text=lb['help_support'], url=support_url))
    builder.row(InlineKeyboardButton(text=lb['nav_back'], callback_data='nav:home'))
    return builder.as_markup()


def device_guides_menu_keyboard(labels: dict[str, str] | None = None):
    lb = _user_labels(labels)
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=lb['guide_ios'], callback_data='help:ios'), InlineKeyboardButton(text=lb['guide_android'], callback_data='help:android'))
    builder.row(InlineKeyboardButton(text=lb['guide_windows'], callback_data='help:windows'), InlineKeyboardButton(text=lb['guide_macos'], callback_data='help:macos'))
    builder.row(InlineKeyboardButton(text=lb['nav_back'], callback_data='nav:home'))
    return builder.as_markup()


def device_guide_keyboard(labels: dict[str, str] | None = None):
    lb = _user_labels(labels)
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=lb['nav_back'], callback_data='help:devices'))
    return builder.as_markup()


def referral_inline_keyboard(invite_link: str, is_admin: bool, show_referral: bool, show_trial: bool, labels: dict[str, str] | None = None):
    lb = _user_labels(labels)
    builder = InlineKeyboardBuilder()
    _append_copy_rows(builder, [(lb['referral_copy'], invite_link)])
    builder.row(InlineKeyboardButton(text=lb['nav_back'], callback_data='nav:home'))
    return builder.as_markup()


def trial_inline_keyboard(can_activate: bool, is_admin: bool, show_referral: bool, show_trial: bool, labels: dict[str, str] | None = None):
    lb = _user_labels(labels)
    builder = InlineKeyboardBuilder()
    if can_activate:
        builder.row(InlineKeyboardButton(text=lb['trial_activate'], callback_data='trial:activate'))
    builder.row(InlineKeyboardButton(text=lb['nav_back'], callback_data='nav:home'))
    return builder.as_markup()


def access_result_keyboard(
    actions: list[tuple[str, str]],
    subscription_url: str | None = None,
    reserve_url: str | None = None,
    reserve_qr_callback: str | None = None,
    labels: dict[str, str] | None = None,
):
    lb = _user_labels(labels)
    builder = InlineKeyboardBuilder()
    if actions:
        _append_compact_action_rows(builder, actions, width=1)
    builder.row(InlineKeyboardButton(text=lb['nav_profile'], callback_data='nav:profile'), InlineKeyboardButton(text=lb['nav_buy'], callback_data='nav:buy'))
    builder.row(InlineKeyboardButton(text=lb['help_devices'], callback_data='help:devices'))
    builder.row(InlineKeyboardButton(text=lb['nav_home'], callback_data='nav:home'))
    return builder.as_markup()


def admin_result_keyboard(actions: list[tuple[str, str]], back_callback: str):
    builder = InlineKeyboardBuilder()
    if actions:
        _append_compact_action_rows(builder, actions, width=1)
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data=back_callback))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def subscription_detail_keyboard(back_callback: str, key_actions: list[tuple[str, str]] | None = None, copy_value: str | None = None, extend_callback: str | None = None, reserve_url: str | None = None, qr_callback: str | None = None, reserve_qr_callback: str | None = None, labels: dict[str, str] | None = None):
    lb = _user_labels(labels)
    builder = InlineKeyboardBuilder()
    if qr_callback:
        builder.row(InlineKeyboardButton(text=lb['subscription_qr'], callback_data=qr_callback))
    if extend_callback:
        builder.row(InlineKeyboardButton(text=lb['subscription_extend'], callback_data=extend_callback))
    if key_actions:
        _append_compact_action_rows(builder, key_actions, width=2)
    builder.row(InlineKeyboardButton(text=lb['help_devices'], callback_data='help:devices'))
    builder.row(InlineKeyboardButton(text=lb['nav_back'], callback_data=back_callback), InlineKeyboardButton(text=lb['nav_home'], callback_data='nav:home'))
    return builder.as_markup()


def key_detail_keyboard(back_callback: str, copy_value: str | None = None, replace_callback: str | None = None, delete_callback: str | None = None, extend_callback: str | None = None, qr_callback: str | None = None, labels: dict[str, str] | None = None):
    lb = _user_labels(labels)
    builder = InlineKeyboardBuilder()
    if copy_value and len(copy_value) <= 256:
        builder.row(InlineKeyboardButton(text=_copy_link_label(copy_value, limit=40), copy_text=CopyTextButton(text=copy_value)))
    if qr_callback:
        builder.row(InlineKeyboardButton(text=lb['key_qr'], callback_data=qr_callback))
    actions: list[InlineKeyboardButton] = []
    if replace_callback:
        actions.append(InlineKeyboardButton(text=lb['key_replace'], callback_data=replace_callback))
    if delete_callback:
        actions.append(InlineKeyboardButton(text=lb['key_delete'], callback_data=delete_callback))
    if actions:
        builder.row(*actions)
    if extend_callback:
        builder.row(InlineKeyboardButton(text=lb['subscription_extend'], callback_data=extend_callback))
    builder.row(InlineKeyboardButton(text=lb['nav_back'], callback_data=back_callback), InlineKeyboardButton(text=lb['nav_home'], callback_data='nav:home'))
    return builder.as_markup()


def back_keyboard(callback_data: str, label: str | None = None, include_home: bool | None = None, labels: dict[str, str] | None = None):
    lb = _user_labels(labels)
    builder = InlineKeyboardBuilder()
    show_home = callback_data.startswith('adm:') if include_home is None else include_home
    row = [InlineKeyboardButton(text=label or lb['nav_back'], callback_data=callback_data)]
    if show_home and callback_data != 'nav:home':
        row.append(InlineKeyboardButton(text=lb['nav_home'], callback_data='nav:home'))
    builder.row(*row)
    return builder.as_markup()


def tariffs_keyboard(tariffs, extend_subscription_id: int | None = None, back_callback: str = 'nav:home', labels: dict[str, str] | None = None):
    lb = _user_labels(labels)
    builder = InlineKeyboardBuilder()
    for tariff in tariffs:
        callback = f'buy:tariff:{tariff.id}'
        if extend_subscription_id:
            callback = f'buy:tariff:{tariff.id}:extend:{extend_subscription_id}'
        builder.row(InlineKeyboardButton(text=f'📦 {tariff.name} • {tariff.days} дн.', callback_data=callback))
    builder.row(InlineKeyboardButton(text=lb['nav_back'], callback_data=back_callback))
    if back_callback != 'nav:home':
        builder.row(InlineKeyboardButton(text=lb['nav_home'], callback_data='nav:home'))
    return builder.as_markup()


def payment_methods_keyboard(tariff_id: int, methods: list[str], extend_subscription_id: int | None = None, back_callback: str = 'buy:back', labels: dict[str, str] | None = None):
    lb = _user_labels(labels)
    labels_map = {'stars': lb['pay_stars'], 'yookassa': lb['pay_yookassa'], 'crypto': lb['pay_crypto'], 'balance': lb['pay_balance']}
    builder = InlineKeyboardBuilder()
    for method in methods:
        callback = f'buy:method:{method}:{tariff_id}'
        if extend_subscription_id:
            callback = f'buy:method:{method}:{tariff_id}:extend:{extend_subscription_id}'
        builder.row(InlineKeyboardButton(text=labels_map[method], callback_data=callback))
    builder.row(InlineKeyboardButton(text=lb['nav_back'], callback_data=back_callback))
    builder.row(InlineKeyboardButton(text=lb['nav_home'], callback_data='nav:home'))
    return builder.as_markup()


def admin_panel_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='👥 Пользователи', callback_data='adm:users:filters'),
        InlineKeyboardButton(text='🖥️ Серверы', callback_data='adm:servers'),
    )
    builder.row(
        InlineKeyboardButton(text='📦 Тарифы', callback_data='adm:tariffs'),
        InlineKeyboardButton(text='💳 Оплаты', callback_data='adm:payments'),
    )
    builder.row(
        InlineKeyboardButton(text='💰 Финансы', callback_data='adm:finance'),
        InlineKeyboardButton(text='📈 Аналитика', callback_data='adm:analytics'),
    )
    builder.row(InlineKeyboardButton(text='📝 Тексты', callback_data='adm:texts'))
    builder.row(
        InlineKeyboardButton(text='📚 Инструкции', callback_data='adm:guide'),
        InlineKeyboardButton(text='🌍 Резерв', callback_data='adm:reserve'),
    )
    builder.row(
        InlineKeyboardButton(text='🎁 Рефералы', callback_data='adm:referral'),
        InlineKeyboardButton(text='🧪 Пробный доступ', callback_data='adm:trial'),
    )
    builder.row(
        InlineKeyboardButton(text='📣 Рассылка', callback_data='adm:broadcast'),
        InlineKeyboardButton(text='🗄️ Бэкап', callback_data='adm:backup'),
    )
    builder.row(InlineKeyboardButton(text='🚀 Обновления', callback_data='adm:updates'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def analytics_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='🔄 Обновить аналитику', callback_data='adm:analytics'))
    builder.row(
        InlineKeyboardButton(text='📄 Экспорт CSV', callback_data='adm:analytics:csv'),
        InlineKeyboardButton(text='📊 Экспорт Excel', callback_data='adm:analytics:xls'),
    )
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def finance_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='🔄 Обновить финансы', callback_data='adm:finance'))
    builder.row(
        InlineKeyboardButton(text='📄 Экспорт CSV', callback_data='adm:finance:csv'),
        InlineKeyboardButton(text='📊 Экспорт Excel', callback_data='adm:finance:xls'),
    )
    builder.row(
        InlineKeyboardButton(text='🖥️ К серверам', callback_data='adm:servers'),
        InlineKeyboardButton(text='📈 К аналитике', callback_data='adm:analytics'),
    )
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def admin_guide_keyboard(current_section: str = 'start'):
    builder = InlineKeyboardBuilder()

    def tab(key: str, title: str) -> InlineKeyboardButton:
        prefix = '• ' if key == current_section else ''
        return InlineKeyboardButton(text=f'{prefix}{title}', callback_data=f'adm:guide:{key}')

    builder.row(tab('start', '🚀 Быстрый старт'), tab('users', '👥 Пользователи'))
    builder.row(tab('servers', '🖥️ Серверы'), tab('tariffs', '📦 Тарифы'))
    builder.row(tab('payments', '💳 Оплаты'), tab('finance', '💰 Финансы'))
    builder.row(tab('analytics', '📈 Аналитика'), tab('texts', '📝 Тексты'))
    builder.row(tab('programs', '🎁 Реф/Пробный'), tab('service', '🗄️ Сервис'))
    builder.row(tab('reserve', '🌍 Резерв'))
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def tariffs_admin_keyboard(tariffs):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='➕ Создать тариф', callback_data='adm:tariff:add'))
    for tariff in tariffs:
        status = '🟢' if tariff.is_active else '⚫'
        builder.row(InlineKeyboardButton(text=f'{status} {tariff.name} • {tariff.days} дн.', callback_data=f'adm:tariff:view:{tariff.id}'))
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def tariff_detail_keyboard(tariff_id: int, is_active: bool):
    builder = InlineKeyboardBuilder()
    toggle_text = '🙈 Скрыть тариф' if is_active else '👁️ Показать тариф'
    builder.row(
        InlineKeyboardButton(text='✏️ Редактировать', callback_data=f'adm:tariff:edit:{tariff_id}'),
        InlineKeyboardButton(text=toggle_text, callback_data=f'adm:tariff:toggle:{tariff_id}'),
    )
    builder.row(InlineKeyboardButton(text='🗑️ Удалить тариф', callback_data=f'adm:tariff:delete:{tariff_id}'))
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:tariffs'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def toggles_keyboard(toggles, payment_config: dict | None = None):
    builder = InlineKeyboardBuilder()
    payment_config = payment_config or {}
    state_map = {toggle.key: toggle.is_enabled for toggle in toggles}

    def label(key: str, title: str) -> str:
        return f"{title} • {'включено' if state_map.get(key, False) else 'скрыто'}"

    yookassa_ready = bool(payment_config.get('yookassa_shop_id') and payment_config.get('yookassa_secret_key'))
    crypto_ready = bool(payment_config.get('crypto_pay_token'))

    builder.row(
        InlineKeyboardButton(text=label('payment_balance', '💰 Баланс'), callback_data='adm:toggle:payment_balance'),
        InlineKeyboardButton(text=label('payment_stars', '⭐ Stars'), callback_data='adm:toggle:payment_stars'),
    )
    builder.row(
        InlineKeyboardButton(text=label('payment_yookassa', '💳 YooKassa'), callback_data='adm:toggle:payment_yookassa'),
        InlineKeyboardButton(text=label('payment_crypto', '🪙 Crypto'), callback_data='adm:toggle:payment_crypto'),
    )
    builder.row(
        InlineKeyboardButton(text=f"⚙️ YooKassa {'готова' if yookassa_ready else 'не настроена'}", callback_data='adm:paymentcfg:yookassa'),
        InlineKeyboardButton(text=f"⚙️ Crypto {'готов' if crypto_ready else 'не настроен'}", callback_data='adm:paymentcfg:crypto'),
    )
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def _user_filter_button_text(filter_key: str, active_filter: str, counts: dict[str, int]) -> str:
    labels = {
        'all': '👥 Все',
        'active': '🟢 Активные',
        'inactive': '🔴 Без доступа',
        'new': '🆕 Новые',
        'never': '📭 Без покупок',
    }
    prefix = '• ' if filter_key == active_filter else ''
    return f"{prefix}{labels.get(filter_key, filter_key)} {counts.get(filter_key, 0)}"


def _append_users_filter_rows(builder: InlineKeyboardBuilder, active_filter: str, counts: dict[str, int]) -> None:
    builder.row(
        InlineKeyboardButton(text=_user_filter_button_text('all', active_filter, counts), callback_data='adm:users:all:1'),
        InlineKeyboardButton(text=_user_filter_button_text('active', active_filter, counts), callback_data='adm:users:active:1'),
    )
    builder.row(
        InlineKeyboardButton(text=_user_filter_button_text('inactive', active_filter, counts), callback_data='adm:users:inactive:1'),
        InlineKeyboardButton(text=_user_filter_button_text('new', active_filter, counts), callback_data='adm:users:new:1'),
    )
    builder.row(InlineKeyboardButton(text=_user_filter_button_text('never', active_filter, counts), callback_data='adm:users:never:1'))


def users_filters_keyboard(active_filter: str = 'all', filter_counts: dict[str, int] | None = None):
    builder = InlineKeyboardBuilder()
    _append_users_filter_rows(builder, active_filter, filter_counts or {})
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def users_list_keyboard(users, filter_key: str, page: int, total: int, page_size: int, filter_counts: dict[str, int] | None = None):
    builder = InlineKeyboardBuilder()
    counts = filter_counts or {}
    _append_users_filter_rows(builder, filter_key, counts)
    if users:
        for user in users:
            label = user.full_name or user.username or str(user.telegram_id)
            prefix = '⛔' if getattr(user, 'is_blocked', False) else '👤'
            builder.row(InlineKeyboardButton(text=f'{prefix} {label[:35]}', callback_data=f'adm:user:{user.id}:{filter_key}:{page}'))
    else:
        builder.row(InlineKeyboardButton(text='— Список пуст —', callback_data='noop'))
    total_pages = max(1, ceil(total / page_size))
    pagination_row: list[InlineKeyboardButton] = []
    if page > 1:
        pagination_row.append(InlineKeyboardButton(text='⬅️', callback_data=f'adm:users:{filter_key}:{page - 1}'))
    pagination_row.append(InlineKeyboardButton(text=f'{page}/{total_pages}', callback_data='noop'))
    if page < total_pages:
        pagination_row.append(InlineKeyboardButton(text='➡️', callback_data=f'adm:users:{filter_key}:{page + 1}'))
    builder.row(*pagination_row)
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def user_actions_keyboard(user_id: int, is_blocked: bool, filter_key: str, page: int, can_manage_block: bool = True):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='💰 Выдать баланс', callback_data=f'adm:user:balance:{user_id}:{filter_key}:{page}'),
        InlineKeyboardButton(text='🔑 Выдать доступ', callback_data=f'adm:user:key:{user_id}:{filter_key}:{page}'),
    )
    builder.row(
        InlineKeyboardButton(text='🧾 Операции', callback_data=f'adm:user:ops:{user_id}:{filter_key}:{page}'),
        InlineKeyboardButton(text='👥 Рефералы', callback_data=f'adm:user:refs:{user_id}:{filter_key}:{page}'),
    )
    if can_manage_block:
        block_text = '✅ Разблокировать' if is_blocked else '🚫 Заблокировать'
        builder.row(InlineKeyboardButton(text=block_text, callback_data=f'adm:user:block:{user_id}:{filter_key}:{page}'))
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data=f'adm:users:{filter_key}:{page}'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def user_operations_keyboard(user_id: int, filter_key: str, page: int):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data=f'adm:user:{user_id}:{filter_key}:{page}'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def user_referrals_keyboard(user_id: int, filter_key: str, page: int):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data=f'adm:user:{user_id}:{filter_key}:{page}'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def contents_keyboard(contents, group: str = 'texts'):
    builder = InlineKeyboardBuilder()

    def tab(key: str, title: str) -> InlineKeyboardButton:
        prefix = '• ' if key == group else ''
        return InlineKeyboardButton(text=f'{prefix}{title}', callback_data=f'adm:texts:{key}')

    builder.row(tab('texts', '📝 Тексты'), tab('buttons', '🔘 Кнопки'))
    if contents:
        icon = '📝' if group == 'texts' else '🔘'
        for page in contents:
            builder.row(InlineKeyboardButton(text=f'{icon} {page.title}', callback_data=f'adm:text:{group}:{page.key}'))
    else:
        builder.row(InlineKeyboardButton(text='— Ничего не найдено —', callback_data='noop'))
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def servers_keyboard(servers):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='➕ Добавить сервер', callback_data='adm:server:add'),
        InlineKeyboardButton(text='🔄 Проверить все', callback_data='adm:server:refresh'),
    )
    builder.row(InlineKeyboardButton(text='📊 Обновить трафик ключей', callback_data='adm:server:usage'))
    for server in servers:
        health_icon = getattr(server, 'health_badge_icon', '⚪')
        health_score = getattr(server, 'health_score', 0)
        active_keys = getattr(server, 'active_keys_count', 0)
        expired_keys = getattr(server, 'expired_keys_count', 0)
        subscriptions = getattr(server, 'active_subscriptions_count', 0)
        builder.row(
            InlineKeyboardButton(
                text=f'{health_icon} {server.name} • {health_score}/100 • ключи {active_keys}/{expired_keys} • пользователи {subscriptions}',
                callback_data=f'adm:server:view:{server.id}',
            )
        )
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def server_actions_keyboard(server_id: int, panel_url: str | None = None, agent_configured: bool = False, agent_online: bool = False, billing_configured: bool = False):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='🔄 Проверить сервер', callback_data=f'adm:server:refreshone:{server_id}'),
        InlineKeyboardButton(text='👁️ В выдачу / скрыть', callback_data=f'adm:server:toggle:{server_id}'),
    )
    builder.row(
        InlineKeyboardButton(text='📊 Трафик ключей', callback_data='adm:server:usage'),
        InlineKeyboardButton(text='🧪 Пробный режим', callback_data=f'adm:server:trial:{server_id}'),
    )
    builder.row(
        InlineKeyboardButton(text=('💳 Изменить оплату' if billing_configured else '💳 Настроить оплату'), callback_data=f'adm:server:billingcfg:{server_id}'),
        InlineKeyboardButton(text='✅ Отметить оплату', callback_data=f'adm:server:billingpaid:{server_id}'),
    )
    if agent_configured:
        builder.row(
            InlineKeyboardButton(text=f'🤖 Агент {"онлайн" if agent_online else "оффлайн"}', callback_data=f'adm:server:agentstatus:{server_id}'),
            InlineKeyboardButton(text='🧩 Перенастроить агент', callback_data=f'adm:server:agentcfg:{server_id}'),
        )
        builder.row(
            InlineKeyboardButton(text='♻️ Рестарт 3x-ui', callback_data=f'adm:server:agentcmd:{server_id}:restart_3x_ui'),
            InlineKeyboardButton(text='🛰️ Рестарт Xray', callback_data=f'adm:server:agentcmd:{server_id}:restart_xray'),
        )
        builder.row(
            InlineKeyboardButton(text='⌨️ Своя команда', callback_data=f'adm:server:agentcustom:{server_id}'),
            InlineKeyboardButton(text='🧹 Отключить агент', callback_data=f'adm:server:agentclear:{server_id}'),
        )
    else:
        builder.row(InlineKeyboardButton(text='🤖 Подключить агент Ubuntu', callback_data=f'adm:server:agentcfg:{server_id}'))
    if panel_url:
        builder.row(InlineKeyboardButton(text='🔗 Открыть панель', url=panel_url))
    builder.row(InlineKeyboardButton(text='🗑️ Удалить сервер', callback_data=f'adm:server:delete:{server_id}'))
    builder.row(
        InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:servers'),
        InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'),
    )
    return builder.as_markup()


def referral_admin_keyboard(is_visible: bool):
    visibility = 'скрыть раздел' if is_visible else 'показать раздел'
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='✏️ Изменить процент', callback_data='adm:referral:edit'))
    builder.row(InlineKeyboardButton(text=f'👁️ {visibility}', callback_data='adm:toggle:section_referral'))
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def trial_admin_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='✏️ Настроить пробный доступ', callback_data='adm:trial:edit'))
    builder.row(InlineKeyboardButton(text='👁️ Показать / скрыть раздел', callback_data='adm:toggle:section_trial'))
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def reserve_admin_keyboard(is_visible: bool):
    visibility = 'скрыть резервный кабинет' if is_visible else 'показать резервный кабинет'
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=f'👁️ {visibility}', callback_data='adm:toggle:section_reserve_access'))
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def backup_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='🗄️ Создать бэкап сейчас', callback_data='adm:backup:run'))
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def broadcast_filters_keyboard():
    builder = InlineKeyboardBuilder()
    for key, title in [
        ('all', '👥 Все'),
        ('active', '🟢 С подпиской'),
        ('inactive', '🔴 Без активной'),
        ('never', '📭 Никогда не покупали'),
        ('new', '🆕 Новые'),
    ]:
        builder.row(InlineKeyboardButton(text=title, callback_data=f'adm:broadcast:{key}'))
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def updates_admin_keyboard(can_trigger: bool, update_available: bool = False):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='🔄 Проверить обновления', callback_data='adm:updates'))
    if can_trigger:
        button_title = '🆕 Обновить сейчас' if update_available else '🚀 Обновить сейчас'
        builder.row(InlineKeyboardButton(text=button_title, callback_data='adm:updates:run'))
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()
