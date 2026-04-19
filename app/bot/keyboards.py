from __future__ import annotations

from math import ceil
from urllib.parse import urlsplit

from aiogram.types import CopyTextButton, InlineKeyboardButton, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def _repair_text(value: str) -> str:
    repaired = value
    for _ in range(3):
        try:
            candidate = repaired.encode('cp1251').decode('utf-8')
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if candidate == repaired:
            break
        repaired = candidate
    return repaired


PROFILE_LABEL = '?? ??? ???????'
BUY_LABEL = '?????????? Air'
HELP_LABEL = '? ???????'
REFERRAL_LABEL = '?? ????????'
TRIAL_LABEL = '?? ??????? ??????'
ADMIN_LABEL = '?? ?????-??????'
HOME_LABEL = '?? ??????? ????'
BACK_LABEL = '? ?????'

DEFAULT_USER_BUTTON_LABELS = {
    'nav_profile': PROFILE_LABEL,
    'nav_buy': BUY_LABEL,
    'nav_help': HELP_LABEL,
    'nav_referral': REFERRAL_LABEL,
    'nav_trial': TRIAL_LABEL,
    'nav_home': HOME_LABEL,
    'nav_back': BACK_LABEL,
    'help_channel': '?? ?????',
    'help_support': '?? ?????????',
    'referral_copy': '?? ??????????? ??????',
    'trial_activate': '?? ???????????? ??????? ??????',
    'help_devices': '?? ??? ??????????',
    'guide_ios': '?? iPhone / iPad',
    'guide_android': '?? Android',
    'guide_windows': '?? Windows',
    'guide_macos': '?? macOS',
    'pay_stars': '? Telegram Stars',
    'pay_yookassa': '?? YooKassa',
    'pay_crypto': '?? Crypto',
    'pay_balance': '?? ? ???????',
    'buy_promo': '??? ????????',
    'buy_promo_clear': '? ?????? ????????',
    'buy_gift': '?? ???????? ??????',
    'buy_gift_clear': '? ?????? ???????',
    'pay_open_invoice': '?? ??????? ? ??????',
    'subscription_qr': '?? QR ????????',
    'subscription_extend': '?? ???????? ????????',
    'reserve_open': '?? ????????? ???????',
    'reserve_qr': '?? QR ???????',
    'key_copy': '?? ??????????? ????',
    'key_qr': '?? QR ?????',
    'key_replace': '?? ???????? ????',
    'key_delete': '??? ??????? ????',
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
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, input_field_placeholder='???????? ??????')


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
    return display[: limit - 1] + '?'

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
            pagination.append(InlineKeyboardButton(text='?', callback_data=f'nav:profile:{page - 1}'))
        pagination.append(InlineKeyboardButton(text=f'{page}/{total_pages}', callback_data='noop'))
        if page < total_pages:
            pagination.append(InlineKeyboardButton(text='?', callback_data=f'nav:profile:{page + 1}'))
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


def tariffs_keyboard(
    tariffs,
    extend_subscription_id: int | None = None,
    back_callback: str = 'nav:home',
    labels: dict[str, str] | None = None,
    promo_applied: str | None = None,
):
    lb = _user_labels(labels)
    builder = InlineKeyboardBuilder()
    for tariff in tariffs:
        callback = f'buy:tariff:{tariff.id}'
        if extend_subscription_id:
            callback = f'buy:tariff:{tariff.id}:extend:{extend_subscription_id}'
        builder.row(InlineKeyboardButton(text=f'📦 {tariff.name} • {tariff.days} дн.', callback_data=callback))
    promo_label = lb['buy_promo_clear'] if promo_applied else lb['buy_promo']
    promo_callback = 'buy:promo:clear' if promo_applied else 'buy:promo'
    builder.row(InlineKeyboardButton(text=(f'✅ {promo_applied}' if promo_applied else promo_label), callback_data=promo_callback))
    builder.row(InlineKeyboardButton(text=lb['nav_back'], callback_data=back_callback))
    if back_callback != 'nav:home':
        builder.row(InlineKeyboardButton(text=lb['nav_home'], callback_data='nav:home'))
    return builder.as_markup()


def payment_methods_keyboard(
    tariff_id: int,
    methods: list[str],
    extend_subscription_id: int | None = None,
    back_callback: str = 'buy:back',
    labels: dict[str, str] | None = None,
    gift_active: bool = False,
):
    lb = _user_labels(labels)
    labels_map = {'stars': lb['pay_stars'], 'yookassa': lb['pay_yookassa'], 'crypto': lb['pay_crypto'], 'balance': lb['pay_balance']}
    builder = InlineKeyboardBuilder()
    if not extend_subscription_id:
        gift_label = lb['buy_gift_clear'] if gift_active else lb['buy_gift']
        gift_callback = 'buy:gift:clear' if gift_active else f'buy:gift:{tariff_id}'
        builder.row(InlineKeyboardButton(text=gift_label, callback_data=gift_callback))
    for method in methods:
        callback = f'buy:method:{method}:{tariff_id}'
        if extend_subscription_id:
            callback = f'buy:method:{method}:{tariff_id}:extend:{extend_subscription_id}'
        builder.row(InlineKeyboardButton(text=labels_map[method], callback_data=callback))
    builder.row(InlineKeyboardButton(text=lb['nav_back'], callback_data=back_callback))
    builder.row(InlineKeyboardButton(text=lb['nav_home'], callback_data='nav:home'))
    return builder.as_markup()


def admin_panel_keyboard(role: str = 'owner'):
    builder = InlineKeyboardBuilder()
    if role in {'owner', 'admin', 'support', 'finance'}:
        builder.row(InlineKeyboardButton(text='👥 Пользователи', callback_data='adm:users:filters'))
    if role in {'owner', 'admin', 'ops'}:
        builder.row(InlineKeyboardButton(text='🖥️ Серверы', callback_data='adm:servers'))
    if role in {'owner', 'admin', 'finance'}:
        builder.row(
            InlineKeyboardButton(text='📦 Тарифы', callback_data='adm:tariffs'),
            InlineKeyboardButton(text='💳 Оплаты', callback_data='adm:payments'),
        )
        builder.row(
            InlineKeyboardButton(text='💰 Финансы', callback_data='adm:finance'),
            InlineKeyboardButton(text='📈 Аналитика', callback_data='adm:analytics'),
        )
    if role in {'owner', 'admin', 'support'}:
        builder.row(InlineKeyboardButton(text='📝 Тексты', callback_data='adm:texts'))
        builder.row(InlineKeyboardButton(text='🎟️ Промокоды', callback_data='adm:promos'))
        builder.row(
            InlineKeyboardButton(text='📚 Инструкции', callback_data='adm:guide'),
            InlineKeyboardButton(text='🌍 Резерв', callback_data='adm:reserve'),
        )
    if role in {'owner', 'admin'}:
        builder.row(
            InlineKeyboardButton(text='🎁 Рефералы', callback_data='adm:referral'),
            InlineKeyboardButton(text='🧪 Пробный доступ', callback_data='adm:trial'),
        )
        builder.row(
            InlineKeyboardButton(text='📣 Рассылка', callback_data='adm:broadcast'),
            InlineKeyboardButton(text='🗄️ Бэкапы', callback_data='adm:backup'),
        )
    if role == 'owner':
        builder.row(
            InlineKeyboardButton(text='🛡️ Роли', callback_data='adm:roles'),
            InlineKeyboardButton(text='🧾 Журнал', callback_data='adm:audit'),
        )
    builder.row(InlineKeyboardButton(text='🚀 Обновления', callback_data='adm:updates'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def promos_admin_keyboard(promos, renewal_discount_percent: int = 0):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='➕ Создать промокод', callback_data='adm:promos:create'),
        InlineKeyboardButton(text=f'🕒 Продление {renewal_discount_percent}%', callback_data='adm:promos:renewal'),
    )
    for promo in promos:
        badge = '🟢' if getattr(promo, 'is_active', False) else '⚫'
        builder.row(InlineKeyboardButton(text=f'{badge} {promo.code}', callback_data=f'adm:promos:view:{promo.id}'))
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def promo_detail_keyboard(promo_id: int, is_active: bool):
    builder = InlineKeyboardBuilder()
    toggle_text = '⏸ Выключить' if is_active else '▶️ Включить'
    builder.row(
        InlineKeyboardButton(text=toggle_text, callback_data=f'adm:promos:toggle:{promo_id}'),
        InlineKeyboardButton(text='🗑️ Удалить', callback_data=f'adm:promos:delete:{promo_id}'),
    )
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:promos'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()
def analytics_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='РЎР‚РЎСџРІР‚СњРІР‚С› Р В РЎвЂєР В Р’В±Р В Р вЂ¦Р В РЎвЂўР В Р вЂ Р В РЎвЂР РЋРІР‚С™Р РЋР Р‰ Р В Р’В°Р В Р вЂ¦Р В Р’В°Р В Р’В»Р В РЎвЂР РЋРІР‚С™Р В РЎвЂР В РЎвЂќР РЋРЎвЂњ', callback_data='adm:analytics'))
    builder.row(
        InlineKeyboardButton(text='РЎР‚РЎСџРІР‚СљРІР‚С› Р В Р’В­Р В РЎвЂќР РЋР С“Р В РЎвЂ”Р В РЎвЂўР РЋР вЂљР РЋРІР‚С™ CSV', callback_data='adm:analytics:csv'),
        InlineKeyboardButton(text='РЎР‚РЎСџРІР‚СљР вЂ° Р В Р’В­Р В РЎвЂќР РЋР С“Р В РЎвЂ”Р В РЎвЂўР РЋР вЂљР РЋРІР‚С™ Excel', callback_data='adm:analytics:xls'),
    )
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def finance_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='РЎР‚РЎСџРІР‚СњРІР‚С› Р В РЎвЂєР В Р’В±Р В Р вЂ¦Р В РЎвЂўР В Р вЂ Р В РЎвЂР РЋРІР‚С™Р РЋР Р‰ Р РЋРІР‚С›Р В РЎвЂР В Р вЂ¦Р В Р’В°Р В Р вЂ¦Р РЋР С“Р РЋРІР‚в„–', callback_data='adm:finance'))
    builder.row(
        InlineKeyboardButton(text='РЎР‚РЎСџРІР‚СљРІР‚С› Р В Р’В­Р В РЎвЂќР РЋР С“Р В РЎвЂ”Р В РЎвЂўР РЋР вЂљР РЋРІР‚С™ CSV', callback_data='adm:finance:csv'),
        InlineKeyboardButton(text='РЎР‚РЎСџРІР‚СљР вЂ° Р В Р’В­Р В РЎвЂќР РЋР С“Р В РЎвЂ”Р В РЎвЂўР РЋР вЂљР РЋРІР‚С™ Excel', callback_data='adm:finance:xls'),
    )
    builder.row(
        InlineKeyboardButton(text='РЎР‚РЎСџРІР‚вЂњРўС’Р С—РЎвЂР РЏ Р В РЎв„ў Р РЋР С“Р В Р’ВµР РЋР вЂљР В Р вЂ Р В Р’ВµР РЋР вЂљР В Р’В°Р В РЎВ', callback_data='adm:servers'),
        InlineKeyboardButton(text='РЎР‚РЎСџРІР‚СљРІвЂљВ¬ Р В РЎв„ў Р В Р’В°Р В Р вЂ¦Р В Р’В°Р В Р’В»Р В РЎвЂР РЋРІР‚С™Р В РЎвЂР В РЎвЂќР В Р’Вµ', callback_data='adm:analytics'),
    )
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def admin_guide_keyboard(current_section: str = 'start'):
    builder = InlineKeyboardBuilder()

    def tab(key: str, title: str) -> InlineKeyboardButton:
        prefix = 'Р Р†Р вЂљРЎС› ' if key == current_section else ''
        return InlineKeyboardButton(text=f'{prefix}{title}', callback_data=f'adm:guide:{key}')

    builder.row(tab('start', 'РЎР‚РЎСџРЎв„ўР вЂљ Р В РІР‚ВР РЋРІР‚в„–Р РЋР С“Р РЋРІР‚С™Р РЋР вЂљР РЋРІР‚в„–Р В РІвЂћвЂ“ Р РЋР С“Р РЋРІР‚С™Р В Р’В°Р РЋР вЂљР РЋРІР‚С™'), tab('users', 'РЎР‚РЎСџРІР‚ВРўС’ Р В РЎСџР В РЎвЂўР В Р’В»Р РЋР Р‰Р В Р’В·Р В РЎвЂўР В Р вЂ Р В Р’В°Р РЋРІР‚С™Р В Р’ВµР В Р’В»Р В РЎвЂ'))
    builder.row(tab('servers', 'РЎР‚РЎСџРІР‚вЂњРўС’Р С—РЎвЂР РЏ Р В Р Р‹Р В Р’ВµР РЋР вЂљР В Р вЂ Р В Р’ВµР РЋР вЂљР РЋРІР‚в„–'), tab('tariffs', 'РЎР‚РЎСџРІР‚СљР’В¦ Р В РЎС›Р В Р’В°Р РЋР вЂљР В РЎвЂР РЋРІР‚С›Р РЋРІР‚в„–'))
    builder.row(tab('payments', 'РЎР‚РЎСџРІР‚в„ўРЎвЂ“ Р В РЎвЂєР В РЎвЂ”Р В Р’В»Р В Р’В°Р РЋРІР‚С™Р РЋРІР‚в„–'), tab('finance', 'РЎР‚РЎСџРІР‚в„ўР’В° Р В Р’В¤Р В РЎвЂР В Р вЂ¦Р В Р’В°Р В Р вЂ¦Р РЋР С“Р РЋРІР‚в„–'))
    builder.row(tab('analytics', 'РЎР‚РЎСџРІР‚СљРІвЂљВ¬ Р В РЎвЂ™Р В Р вЂ¦Р В Р’В°Р В Р’В»Р В РЎвЂР РЋРІР‚С™Р В РЎвЂР В РЎвЂќР В Р’В°'), tab('texts', 'РЎР‚РЎСџРІР‚СљРЎСљ Р В РЎС›Р В Р’ВµР В РЎвЂќР РЋР С“Р РЋРІР‚С™Р РЋРІР‚в„–'))
    builder.row(tab('programs', 'РЎР‚РЎСџР вЂ№Р С“ Р В Р’В Р В Р’ВµР РЋРІР‚С›/Р В РЎСџР РЋР вЂљР В РЎвЂўР В Р’В±Р В Р вЂ¦Р РЋРІР‚в„–Р В РІвЂћвЂ“'), tab('service', 'РЎР‚РЎСџРІР‚вЂќРІР‚С›Р С—РЎвЂР РЏ Р В Р Р‹Р В Р’ВµР РЋР вЂљР В Р вЂ Р В РЎвЂР РЋР С“'))
    builder.row(tab('reserve', 'РЎР‚РЎСџР Р‰Р РЉ Р В Р’В Р В Р’ВµР В Р’В·Р В Р’ВµР РЋР вЂљР В Р вЂ '))
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def tariffs_admin_keyboard(tariffs):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='Р Р†РЎвЂєРІР‚Сћ Р В Р Р‹Р В РЎвЂўР В Р’В·Р В РўвЂР В Р’В°Р РЋРІР‚С™Р РЋР Р‰ Р РЋРІР‚С™Р В Р’В°Р РЋР вЂљР В РЎвЂР РЋРІР‚С›', callback_data='adm:tariff:add'))
    for tariff in tariffs:
        status = 'РЎР‚РЎСџРЎСџРЎС›' if tariff.is_active else 'Р Р†РЎв„ўР’В«'
        builder.row(InlineKeyboardButton(text=f'{status} {tariff.name} Р Р†Р вЂљРЎС› {tariff.days} Р В РўвЂР В Р вЂ¦.', callback_data=f'adm:tariff:view:{tariff.id}'))
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def tariff_detail_keyboard(tariff_id: int, is_active: bool):
    builder = InlineKeyboardBuilder()
    toggle_text = 'РЎР‚РЎСџРІвЂћСћРІвЂљВ¬ Р В Р Р‹Р В РЎвЂќР РЋР вЂљР РЋРІР‚в„–Р РЋРІР‚С™Р РЋР Р‰ Р РЋРІР‚С™Р В Р’В°Р РЋР вЂљР В РЎвЂР РЋРІР‚С›' if is_active else 'РЎР‚РЎСџРІР‚ВР С“Р С—РЎвЂР РЏ Р В РЎСџР В РЎвЂўР В РЎвЂќР В Р’В°Р В Р’В·Р В Р’В°Р РЋРІР‚С™Р РЋР Р‰ Р РЋРІР‚С™Р В Р’В°Р РЋР вЂљР В РЎвЂР РЋРІР‚С›'
    builder.row(
        InlineKeyboardButton(text='Р Р†РЎС™Р РЏР С—РЎвЂР РЏ Р В Р’В Р В Р’ВµР В РўвЂР В Р’В°Р В РЎвЂќР РЋРІР‚С™Р В РЎвЂР РЋР вЂљР В РЎвЂўР В Р вЂ Р В Р’В°Р РЋРІР‚С™Р РЋР Р‰', callback_data=f'adm:tariff:edit:{tariff_id}'),
        InlineKeyboardButton(text=toggle_text, callback_data=f'adm:tariff:toggle:{tariff_id}'),
    )
    builder.row(InlineKeyboardButton(text='РЎР‚РЎСџРІР‚вЂќРІР‚ВР С—РЎвЂР РЏ Р В Р в‚¬Р В РўвЂР В Р’В°Р В Р’В»Р В РЎвЂР РЋРІР‚С™Р РЋР Р‰ Р РЋРІР‚С™Р В Р’В°Р РЋР вЂљР В РЎвЂР РЋРІР‚С›', callback_data=f'adm:tariff:delete:{tariff_id}'))
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:tariffs'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def toggles_keyboard(toggles, payment_config: dict | None = None):
    builder = InlineKeyboardBuilder()
    payment_config = payment_config or {}
    state_map = {toggle.key: toggle.is_enabled for toggle in toggles}

    def label(key: str, title: str) -> str:
        return f"{title} Р Р†Р вЂљРЎС› {'Р В Р вЂ Р В РЎвЂќР В Р’В»Р РЋР вЂ№Р РЋРІР‚РЋР В Р’ВµР В Р вЂ¦Р В РЎвЂў' if state_map.get(key, False) else 'Р РЋР С“Р В РЎвЂќР РЋР вЂљР РЋРІР‚в„–Р РЋРІР‚С™Р В РЎвЂў'}"

    yookassa_ready = bool(payment_config.get('yookassa_shop_id') and payment_config.get('yookassa_secret_key'))
    crypto_ready = bool(payment_config.get('crypto_pay_token'))

    builder.row(
        InlineKeyboardButton(text=label('payment_balance', 'РЎР‚РЎСџРІР‚в„ўР’В° Р В РІР‚ВР В Р’В°Р В Р’В»Р В Р’В°Р В Р вЂ¦Р РЋР С“'), callback_data='adm:toggle:payment_balance'),
        InlineKeyboardButton(text=label('payment_stars', 'Р Р†Р’В­РЎвЂ™ Stars'), callback_data='adm:toggle:payment_stars'),
    )
    builder.row(
        InlineKeyboardButton(text=label('payment_yookassa', 'РЎР‚РЎСџРІР‚в„ўРЎвЂ“ YooKassa'), callback_data='adm:toggle:payment_yookassa'),
        InlineKeyboardButton(text=label('payment_crypto', 'РЎР‚РЎСџР вЂћРІвЂћСћ Crypto'), callback_data='adm:toggle:payment_crypto'),
    )
    builder.row(
        InlineKeyboardButton(text=f"Р Р†РЎв„ўРІвЂћСћР С—РЎвЂР РЏ YooKassa {'Р В РЎвЂ“Р В РЎвЂўР РЋРІР‚С™Р В РЎвЂўР В Р вЂ Р В Р’В°' if yookassa_ready else 'Р В Р вЂ¦Р В Р’Вµ Р В Р вЂ¦Р В Р’В°Р РЋР С“Р РЋРІР‚С™Р РЋР вЂљР В РЎвЂўР В Р’ВµР В Р вЂ¦Р В Р’В°'}", callback_data='adm:paymentcfg:yookassa'),
        InlineKeyboardButton(text=f"Р Р†РЎв„ўРІвЂћСћР С—РЎвЂР РЏ Crypto {'Р В РЎвЂ“Р В РЎвЂўР РЋРІР‚С™Р В РЎвЂўР В Р вЂ ' if crypto_ready else 'Р В Р вЂ¦Р В Р’Вµ Р В Р вЂ¦Р В Р’В°Р РЋР С“Р РЋРІР‚С™Р РЋР вЂљР В РЎвЂўР В Р’ВµР В Р вЂ¦'}", callback_data='adm:paymentcfg:crypto'),
    )
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def _user_filter_button_text(filter_key: str, active_filter: str, counts: dict[str, int]) -> str:
    labels = {
        'all': 'РЎР‚РЎСџРІР‚ВРўС’ Р В РІР‚в„ўР РЋР С“Р В Р’Вµ',
        'active': 'РЎР‚РЎСџРЎСџРЎС› Р В РЎвЂ™Р В РЎвЂќР РЋРІР‚С™Р В РЎвЂР В Р вЂ Р В Р вЂ¦Р РЋРІР‚в„–Р В Р’Вµ',
        'inactive': 'РЎР‚РЎСџРІР‚СњРўвЂ Р В РІР‚ВР В Р’ВµР В Р’В· Р В РўвЂР В РЎвЂўР РЋР С“Р РЋРІР‚С™Р РЋРЎвЂњР В РЎвЂ”Р В Р’В°',
        'new': 'РЎР‚РЎСџРІР‚В РІР‚Сћ Р В РЎСљР В РЎвЂўР В Р вЂ Р РЋРІР‚в„–Р В Р’Вµ',
        'never': 'РЎР‚РЎСџРІР‚СљР’В­ Р В РІР‚ВР В Р’ВµР В Р’В· Р В РЎвЂ”Р В РЎвЂўР В РЎвЂќР РЋРЎвЂњР В РЎвЂ”Р В РЎвЂўР В РЎвЂќ',
    }
    prefix = 'Р Р†Р вЂљРЎС› ' if filter_key == active_filter else ''
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
            prefix = 'Р Р†РІР‚С”РІР‚Сњ' if getattr(user, 'is_blocked', False) else 'РЎР‚РЎСџРІР‚ВР’В¤'
            builder.row(InlineKeyboardButton(text=f'{prefix} {label[:35]}', callback_data=f'adm:user:{user.id}:{filter_key}:{page}'))
    else:
        builder.row(InlineKeyboardButton(text='Р Р†Р вЂљРІР‚Сњ Р В Р Р‹Р В РЎвЂ”Р В РЎвЂР РЋР С“Р В РЎвЂўР В РЎвЂќ Р В РЎвЂ”Р РЋРЎвЂњР РЋР С“Р РЋРІР‚С™ Р Р†Р вЂљРІР‚Сњ', callback_data='noop'))
    total_pages = max(1, ceil(total / page_size))
    pagination_row: list[InlineKeyboardButton] = []
    if page > 1:
        pagination_row.append(InlineKeyboardButton(text='Р Р†Р’В¬РІР‚В¦Р С—РЎвЂР РЏ', callback_data=f'adm:users:{filter_key}:{page - 1}'))
    pagination_row.append(InlineKeyboardButton(text=f'{page}/{total_pages}', callback_data='noop'))
    if page < total_pages:
        pagination_row.append(InlineKeyboardButton(text='Р Р†РЎвЂєР Р‹Р С—РЎвЂР РЏ', callback_data=f'adm:users:{filter_key}:{page + 1}'))
    builder.row(*pagination_row)
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def user_actions_keyboard(
    user_id: int,
    is_blocked: bool,
    filter_key: str,
    page: int,
    can_manage_block: bool = True,
    can_grant_balance: bool = True,
    can_grant_access: bool = True,
    can_view_diagnostics: bool = True,
    can_manage_role: bool = False,
):
    builder = InlineKeyboardBuilder()
    top_row = []
    if can_grant_balance:
        top_row.append(InlineKeyboardButton(text='РЎР‚РЎСџРІР‚в„ўРЎвЂ“ Р В РІР‚в„ўР РЋРІР‚в„–Р В РўвЂР В Р’В°Р РЋРІР‚С™Р РЋР Р‰ Р В Р’В±Р В Р’В°Р В Р’В»Р В Р’В°Р В Р вЂ¦Р РЋР С“', callback_data=f'adm:user:balance:{user_id}:{filter_key}:{page}'))
    if can_grant_access:
        top_row.append(InlineKeyboardButton(text='РЎР‚РЎСџРІР‚СњРІР‚В Р В РІР‚в„ўР РЋРІР‚в„–Р В РўвЂР В Р’В°Р РЋРІР‚С™Р РЋР Р‰ Р В РўвЂР В РЎвЂўР РЋР С“Р РЋРІР‚С™Р РЋРЎвЂњР В РЎвЂ”', callback_data=f'adm:user:key:{user_id}:{filter_key}:{page}'))
    if top_row:
        builder.row(*top_row)
    builder.row(
        InlineKeyboardButton(text='РЎР‚РЎСџРІР‚СљРЎС™ Р В РЎвЂєР В РЎвЂ”Р В Р’ВµР РЋР вЂљР В Р’В°Р РЋРІР‚В Р В РЎвЂР В РЎвЂ', callback_data=f'adm:user:ops:{user_id}:{filter_key}:{page}'),
        InlineKeyboardButton(text='РЎР‚РЎСџРІР‚ВРўС’ Р В Р’В Р В Р’ВµР РЋРІР‚С›Р В Р’ВµР РЋР вЂљР В Р’В°Р В Р’В»Р РЋРІР‚в„–', callback_data=f'adm:user:refs:{user_id}:{filter_key}:{page}'),
    )
    extra_row = []
    if can_view_diagnostics:
        extra_row.append(InlineKeyboardButton(text='РЎР‚РЎСџР’В§Р вЂЎ Р В РІР‚СњР В РЎвЂР В Р’В°Р В РЎвЂ“Р В Р вЂ¦Р В РЎвЂўР РЋР С“Р РЋРІР‚С™Р В РЎвЂР В РЎвЂќР В Р’В°', callback_data=f'adm:user:diag:{user_id}:{filter_key}:{page}'))
    if can_manage_role:
        extra_row.append(InlineKeyboardButton(text='РЎР‚РЎСџРІР‚С”Р Р‹Р С—РЎвЂР РЏ Р В Р’В Р В РЎвЂўР В Р’В»Р РЋР Р‰', callback_data=f'adm:user:role:{user_id}:{filter_key}:{page}'))
    if extra_row:
        builder.row(*extra_row)
    if can_manage_block:
        block_text = 'Р Р†РЎС™РІР‚В¦ Р В Р’В Р В Р’В°Р В Р’В·Р В Р’В±Р В Р’В»Р В РЎвЂўР В РЎвЂќР В РЎвЂР РЋР вЂљР В РЎвЂўР В Р вЂ Р В Р’В°Р РЋРІР‚С™Р РЋР Р‰' if is_blocked else 'РЎР‚РЎСџРЎв„ўР’В« Р В РІР‚вЂќР В Р’В°Р В Р’В±Р В Р’В»Р В РЎвЂўР В РЎвЂќР В РЎвЂР РЋР вЂљР В РЎвЂўР В Р вЂ Р В Р’В°Р РЋРІР‚С™Р РЋР Р‰'
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
        prefix = 'Р Р†Р вЂљРЎС› ' if key == group else ''
        return InlineKeyboardButton(text=f'{prefix}{title}', callback_data=f'adm:texts:{key}')

    builder.row(tab('texts', 'РЎР‚РЎСџРІР‚СљРЎСљ Р В РЎС›Р В Р’ВµР В РЎвЂќР РЋР С“Р РЋРІР‚С™Р РЋРІР‚в„–'), tab('buttons', 'РЎР‚РЎСџРІР‚СњР’В Р В РЎв„ўР В Р вЂ¦Р В РЎвЂўР В РЎвЂ”Р В РЎвЂќР В РЎвЂ'))
    if contents:
        icon = 'РЎР‚РЎСџРІР‚СљРЎСљ' if group == 'texts' else 'РЎР‚РЎСџРІР‚СњР’В'
        for page in contents:
            builder.row(InlineKeyboardButton(text=f'{icon} {page.title}', callback_data=f'adm:text:{group}:{page.key}'))
    else:
        builder.row(InlineKeyboardButton(text='Р Р†Р вЂљРІР‚Сњ Р В РЎСљР В РЎвЂР РЋРІР‚РЋР В Р’ВµР В РЎвЂ“Р В РЎвЂў Р В Р вЂ¦Р В Р’Вµ Р В Р вЂ¦Р В Р’В°Р В РІвЂћвЂ“Р В РўвЂР В Р’ВµР В Р вЂ¦Р В РЎвЂў Р Р†Р вЂљРІР‚Сњ', callback_data='noop'))
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def servers_keyboard(servers):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='Р Р†РЎвЂєРІР‚Сћ Р В РІР‚СњР В РЎвЂўР В Р’В±Р В Р’В°Р В Р вЂ Р В РЎвЂР РЋРІР‚С™Р РЋР Р‰ Р РЋР С“Р В Р’ВµР РЋР вЂљР В Р вЂ Р В Р’ВµР РЋР вЂљ', callback_data='adm:server:add'),
        InlineKeyboardButton(text='РЎР‚РЎСџРІР‚СњРІР‚С› Р В РЎСџР РЋР вЂљР В РЎвЂўР В Р вЂ Р В Р’ВµР РЋР вЂљР В РЎвЂР РЋРІР‚С™Р РЋР Р‰ Р В Р вЂ Р РЋР С“Р В Р’Вµ', callback_data='adm:server:refresh'),
    )
    builder.row(InlineKeyboardButton(text='РЎР‚РЎСџРІР‚СљР вЂ° Р В РЎвЂєР В Р’В±Р В Р вЂ¦Р В РЎвЂўР В Р вЂ Р В РЎвЂР РЋРІР‚С™Р РЋР Р‰ Р РЋРІР‚С™Р РЋР вЂљР В Р’В°Р РЋРІР‚С›Р В РЎвЂР В РЎвЂќ Р В РЎвЂќР В Р’В»Р РЋР вЂ№Р РЋРІР‚РЋР В Р’ВµР В РІвЂћвЂ“', callback_data='adm:server:usage'))
    for server in servers:
        health_icon = getattr(server, 'health_badge_icon', 'Р Р†РЎв„ўР вЂћ')
        health_score = getattr(server, 'health_score', 0)
        active_keys = getattr(server, 'active_keys_count', 0)
        expired_keys = getattr(server, 'expired_keys_count', 0)
        subscriptions = getattr(server, 'active_subscriptions_count', 0)
        builder.row(
            InlineKeyboardButton(
                text=f'{health_icon} {server.name} Р Р†Р вЂљРЎС› {health_score}/100 Р Р†Р вЂљРЎС› Р В РЎвЂќР В Р’В»Р РЋР вЂ№Р РЋРІР‚РЋР В РЎвЂ {active_keys}/{expired_keys} Р Р†Р вЂљРЎС› Р В РЎвЂ”Р В РЎвЂўР В Р’В»Р РЋР Р‰Р В Р’В·Р В РЎвЂўР В Р вЂ Р В Р’В°Р РЋРІР‚С™Р В Р’ВµР В Р’В»Р В РЎвЂ {subscriptions}',
                callback_data=f'adm:server:view:{server.id}',
            )
        )
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def server_actions_keyboard(server_id: int, panel_url: str | None = None, agent_configured: bool = False, agent_online: bool = False, billing_configured: bool = False):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='РЎР‚РЎСџРІР‚СњРІР‚С› Р В РЎСџР РЋР вЂљР В РЎвЂўР В Р вЂ Р В Р’ВµР РЋР вЂљР В РЎвЂР РЋРІР‚С™Р РЋР Р‰ Р РЋР С“Р В Р’ВµР РЋР вЂљР В Р вЂ Р В Р’ВµР РЋР вЂљ', callback_data=f'adm:server:refreshone:{server_id}'),
        InlineKeyboardButton(text='РЎР‚РЎСџРІР‚ВР С“Р С—РЎвЂР РЏ Р В РІР‚в„ў Р В Р вЂ Р РЋРІР‚в„–Р В РўвЂР В Р’В°Р РЋРІР‚РЋР РЋРЎвЂњ / Р РЋР С“Р В РЎвЂќР РЋР вЂљР РЋРІР‚в„–Р РЋРІР‚С™Р РЋР Р‰', callback_data=f'adm:server:toggle:{server_id}'),
    )
    builder.row(
        InlineKeyboardButton(text='РЎР‚РЎСџРІР‚СљР вЂ° Р В РЎС›Р РЋР вЂљР В Р’В°Р РЋРІР‚С›Р В РЎвЂР В РЎвЂќ Р В РЎвЂќР В Р’В»Р РЋР вЂ№Р РЋРІР‚РЋР В Р’ВµР В РІвЂћвЂ“', callback_data='adm:server:usage'),
        InlineKeyboardButton(text='РЎР‚РЎСџР’В§Р вЂћ Trial on/off', callback_data=f'adm:server:trial:{server_id}'),
    )
    builder.row(
        InlineKeyboardButton(text=('РЎР‚РЎСџРІР‚в„ўРЎвЂ“ Р В РЎСљР В Р’В°Р РЋР С“Р РЋРІР‚С™Р РЋР вЂљР В РЎвЂўР В РЎвЂР РЋРІР‚С™Р РЋР Р‰ Р В РЎвЂўР В РЎвЂ”Р В Р’В»Р В Р’В°Р РЋРІР‚С™Р РЋРЎвЂњ' if billing_configured else 'РЎР‚РЎСџРІР‚в„ўРЎвЂ“ Р В РІР‚СњР В РЎвЂўР В Р’В±Р В Р’В°Р В Р вЂ Р В РЎвЂР РЋРІР‚С™Р РЋР Р‰ Р В РЎвЂўР В РЎвЂ”Р В Р’В»Р В Р’В°Р РЋРІР‚С™Р РЋРЎвЂњ'), callback_data=f'adm:server:billingcfg:{server_id}'),
        InlineKeyboardButton(text='Р Р†РЎС™РІР‚В¦ Р В РЎвЂєР РЋРІР‚С™Р В РЎВР В Р’ВµР РЋРІР‚С™Р В РЎвЂР РЋРІР‚С™Р РЋР Р‰ Р В РЎвЂўР В РЎвЂ”Р В Р’В»Р В Р’В°Р РЋРІР‚С™Р РЋРЎвЂњ', callback_data=f'adm:server:billingpaid:{server_id}'),
    )
    builder.row(InlineKeyboardButton(text='РЎР‚РЎСџР’В§Р вЂЎ Р В Р’ВР РЋР С“Р РЋРІР‚С™Р В РЎвЂўР РЋР вЂљР В РЎвЂР РЋР РЏ Р РЋР С“Р В Р’В±Р В РЎвЂўР В Р’ВµР В Р вЂ ', callback_data=f'adm:server:failures:{server_id}'))
    if agent_configured:
        builder.row(
            InlineKeyboardButton(text=f'РЎР‚РЎСџР’В¤РІР‚вЂњ Р В РЎвЂ™Р В РЎвЂ“Р В Р’ВµР В Р вЂ¦Р РЋРІР‚С™ {"online" if agent_online else "offline"}', callback_data=f'adm:server:agentstatus:{server_id}'),
            InlineKeyboardButton(text='Р Р†РЎв„ўРІвЂћСћР С—РЎвЂР РЏ Р В РЎСљР В Р’В°Р РЋР С“Р РЋРІР‚С™Р РЋР вЂљР В РЎвЂўР В РЎвЂР РЋРІР‚С™Р РЋР Р‰ Р В Р’В°Р В РЎвЂ“Р В Р’ВµР В Р вЂ¦Р РЋРІР‚С™', callback_data=f'adm:server:agentcfg:{server_id}'),
        )
        builder.row(
            InlineKeyboardButton(text='Р Р†РІвЂћСћР’В»Р С—РЎвЂР РЏ Р В Р’В Р В Р’ВµР РЋР С“Р РЋРІР‚С™Р В Р’В°Р РЋР вЂљР РЋРІР‚С™ 3x-ui', callback_data=f'adm:server:agentcmd:{server_id}:restart_3x_ui'),
            InlineKeyboardButton(text='РЎР‚РЎСџРІР‚С”Р’В°Р С—РЎвЂР РЏ Р В Р’В Р В Р’ВµР РЋР С“Р РЋРІР‚С™Р В Р’В°Р РЋР вЂљР РЋРІР‚С™ Xray', callback_data=f'adm:server:agentcmd:{server_id}:restart_xray'),
        )
        builder.row(
            InlineKeyboardButton(text='Р Р†Р Р‰Р РѓР С—РЎвЂР РЏ Р В Р Р‹Р В Р вЂ Р В РЎвЂўР РЋР РЏ Р В РЎвЂќР В РЎвЂўР В РЎВР В Р’В°Р В Р вЂ¦Р В РўвЂР В Р’В°', callback_data=f'adm:server:agentcustom:{server_id}'),
            InlineKeyboardButton(text='РЎР‚РЎСџР’В§РІвЂћвЂ“ Р В РЎвЂєР РЋРІР‚С™Р В РЎвЂќР В Р’В»Р РЋР вЂ№Р РЋРІР‚РЋР В РЎвЂР РЋРІР‚С™Р РЋР Р‰ Р В Р’В°Р В РЎвЂ“Р В Р’ВµР В Р вЂ¦Р РЋРІР‚С™', callback_data=f'adm:server:agentclear:{server_id}'),
        )
    else:
        builder.row(InlineKeyboardButton(text='РЎР‚РЎСџР’В¤РІР‚вЂњ Р В РЎСџР В РЎвЂўР В РўвЂР В РЎвЂќР В Р’В»Р РЋР вЂ№Р РЋРІР‚РЋР В РЎвЂР РЋРІР‚С™Р РЋР Р‰ Р В Р’В°Р В РЎвЂ“Р В Р’ВµР В Р вЂ¦Р РЋРІР‚С™ Ubuntu', callback_data=f'adm:server:agentcfg:{server_id}'))
    if panel_url:
        builder.row(InlineKeyboardButton(text='РЎР‚РЎСџРІР‚СњРІР‚вЂќ Р В РЎвЂєР РЋРІР‚С™Р В РЎвЂќР РЋР вЂљР РЋРІР‚в„–Р РЋРІР‚С™Р РЋР Р‰ Р В РЎвЂ”Р В Р’В°Р В Р вЂ¦Р В Р’ВµР В Р’В»Р РЋР Р‰', url=panel_url))
    builder.row(InlineKeyboardButton(text='РЎР‚РЎСџРІР‚вЂќРІР‚ВР С—РЎвЂР РЏ Р В Р в‚¬Р В РўвЂР В Р’В°Р В Р’В»Р В РЎвЂР РЋРІР‚С™Р РЋР Р‰ Р РЋР С“Р В Р’ВµР РЋР вЂљР В Р вЂ Р В Р’ВµР РЋР вЂљ', callback_data=f'adm:server:delete:{server_id}'))
    builder.row(
        InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:servers'),
        InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'),
    )
    return builder.as_markup()
def referral_admin_keyboard(is_visible: bool):
    visibility = 'Р РЋР С“Р В РЎвЂќР РЋР вЂљР РЋРІР‚в„–Р РЋРІР‚С™Р РЋР Р‰ Р РЋР вЂљР В Р’В°Р В Р’В·Р В РўвЂР В Р’ВµР В Р’В»' if is_visible else 'Р В РЎвЂ”Р В РЎвЂўР В РЎвЂќР В Р’В°Р В Р’В·Р В Р’В°Р РЋРІР‚С™Р РЋР Р‰ Р РЋР вЂљР В Р’В°Р В Р’В·Р В РўвЂР В Р’ВµР В Р’В»'
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='Р Р†РЎС™Р РЏР С—РЎвЂР РЏ Р В Р’ВР В Р’В·Р В РЎВР В Р’ВµР В Р вЂ¦Р В РЎвЂР РЋРІР‚С™Р РЋР Р‰ Р В РЎвЂ”Р РЋР вЂљР В РЎвЂўР РЋРІР‚В Р В Р’ВµР В Р вЂ¦Р РЋРІР‚С™', callback_data='adm:referral:edit'))
    builder.row(InlineKeyboardButton(text=f'РЎР‚РЎСџРІР‚ВР С“Р С—РЎвЂР РЏ {visibility}', callback_data='adm:toggle:section_referral'))
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def trial_admin_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='Р Р†РЎС™Р РЏР С—РЎвЂР РЏ Р В РЎСљР В Р’В°Р РЋР С“Р РЋРІР‚С™Р РЋР вЂљР В РЎвЂўР В РЎвЂР РЋРІР‚С™Р РЋР Р‰ Р В РЎвЂ”Р РЋР вЂљР В РЎвЂўР В Р’В±Р В Р вЂ¦Р РЋРІР‚в„–Р В РІвЂћвЂ“ Р В РўвЂР В РЎвЂўР РЋР С“Р РЋРІР‚С™Р РЋРЎвЂњР В РЎвЂ”', callback_data='adm:trial:edit'))
    builder.row(InlineKeyboardButton(text='РЎР‚РЎСџРІР‚ВР С“Р С—РЎвЂР РЏ Р В РЎСџР В РЎвЂўР В РЎвЂќР В Р’В°Р В Р’В·Р В Р’В°Р РЋРІР‚С™Р РЋР Р‰ / Р РЋР С“Р В РЎвЂќР РЋР вЂљР РЋРІР‚в„–Р РЋРІР‚С™Р РЋР Р‰ Р РЋР вЂљР В Р’В°Р В Р’В·Р В РўвЂР В Р’ВµР В Р’В»', callback_data='adm:toggle:section_trial'))
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def reserve_admin_keyboard(is_visible: bool):
    visibility = 'Р РЋР С“Р В РЎвЂќР РЋР вЂљР РЋРІР‚в„–Р РЋРІР‚С™Р РЋР Р‰ Р РЋР вЂљР В Р’ВµР В Р’В·Р В Р’ВµР РЋР вЂљР В Р вЂ Р В Р вЂ¦Р РЋРІР‚в„–Р В РІвЂћвЂ“ Р В РЎвЂќР В Р’В°Р В Р’В±Р В РЎвЂР В Р вЂ¦Р В Р’ВµР РЋРІР‚С™' if is_visible else 'Р В РЎвЂ”Р В РЎвЂўР В РЎвЂќР В Р’В°Р В Р’В·Р В Р’В°Р РЋРІР‚С™Р РЋР Р‰ Р РЋР вЂљР В Р’ВµР В Р’В·Р В Р’ВµР РЋР вЂљР В Р вЂ Р В Р вЂ¦Р РЋРІР‚в„–Р В РІвЂћвЂ“ Р В РЎвЂќР В Р’В°Р В Р’В±Р В РЎвЂР В Р вЂ¦Р В Р’ВµР РЋРІР‚С™'
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=f'РЎР‚РЎСџРІР‚ВР С“Р С—РЎвЂР РЏ {visibility}', callback_data='adm:toggle:section_reserve_access'))
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def backup_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='РЎР‚РЎСџРІР‚вЂќРІР‚С›Р С—РЎвЂР РЏ Р В Р Р‹Р В РЎвЂўР В Р’В·Р В РўвЂР В Р’В°Р РЋРІР‚С™Р РЋР Р‰ Р В Р’В±Р РЋР РЉР В РЎвЂќР В Р’В°Р В РЎвЂ” Р РЋР С“Р В Р’ВµР В РІвЂћвЂ“Р РЋРІР‚РЋР В Р’В°Р РЋР С“', callback_data='adm:backup:run'))
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def broadcast_filters_keyboard():
    builder = InlineKeyboardBuilder()
    for key, title in [
        ('all', 'РЎР‚РЎСџРІР‚ВРўС’ Р В РІР‚в„ўР РЋР С“Р В Р’Вµ'),
        ('active', 'РЎР‚РЎСџРЎСџРЎС› Р В Р Р‹ Р В РЎвЂ”Р В РЎвЂўР В РўвЂР В РЎвЂ”Р В РЎвЂР РЋР С“Р В РЎвЂќР В РЎвЂўР В РІвЂћвЂ“'),
        ('inactive', 'РЎР‚РЎСџРІР‚СњРўвЂ Р В РІР‚ВР В Р’ВµР В Р’В· Р В Р’В°Р В РЎвЂќР РЋРІР‚С™Р В РЎвЂР В Р вЂ Р В Р вЂ¦Р В РЎвЂўР В РІвЂћвЂ“'),
        ('never', 'РЎР‚РЎСџРІР‚СљР’В­ Р В РЎСљР В РЎвЂР В РЎвЂќР В РЎвЂўР В РЎвЂ“Р В РўвЂР В Р’В° Р В Р вЂ¦Р В Р’Вµ Р В РЎвЂ”Р В РЎвЂўР В РЎвЂќР РЋРЎвЂњР В РЎвЂ”Р В Р’В°Р В Р’В»Р В РЎвЂ'),
        ('new', 'РЎР‚РЎСџРІР‚В РІР‚Сћ Р В РЎСљР В РЎвЂўР В Р вЂ Р РЋРІР‚в„–Р В Р’Вµ'),
    ]:
        builder.row(InlineKeyboardButton(text=title, callback_data=f'adm:broadcast:{key}'))
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def updates_admin_keyboard(can_trigger: bool, update_available: bool = False):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='РЎР‚РЎСџРІР‚СњРІР‚С› Р В РЎСџР РЋР вЂљР В РЎвЂўР В Р вЂ Р В Р’ВµР РЋР вЂљР В РЎвЂР РЋРІР‚С™Р РЋР Р‰ Р В РЎвЂўР В Р’В±Р В Р вЂ¦Р В РЎвЂўР В Р вЂ Р В Р’В»Р В Р’ВµР В Р вЂ¦Р В РЎвЂР РЋР РЏ', callback_data='adm:updates'))
    if can_trigger:
        builder.row(InlineKeyboardButton(text='РЎР‚РЎСџРЎв„ўР вЂљ Р В РЎвЂєР В Р’В±Р В Р вЂ¦Р В РЎвЂўР В Р вЂ Р В РЎвЂР РЋРІР‚С™Р РЋР Р‰ Р В Р’В±Р В РЎвЂўР РЋРІР‚С™Р В Р’В°' if update_available else 'РЎР‚РЎСџРЎв„ўР вЂљ Р В РЎСџР В Р’ВµР РЋР вЂљР В Р’ВµР В Р’В·Р В Р’В°Р В РЎвЂ”Р РЋРЎвЂњР РЋР С“Р РЋРІР‚С™Р В РЎвЂР РЋРІР‚С™Р РЋР Р‰ Р В РЎвЂўР В Р’В±Р В Р вЂ¦Р В РЎвЂўР В Р вЂ Р В Р’В»Р В Р’ВµР В Р вЂ¦Р В РЎвЂР В Р’Вµ', callback_data='adm:updates:run'))
    builder.row(InlineKeyboardButton(text=BACK_LABEL, callback_data='adm:panel'))
    builder.row(InlineKeyboardButton(text=HOME_LABEL, callback_data='nav:home'))
    return builder.as_markup()


def update_notice_keyboard(can_trigger: bool):
    builder = InlineKeyboardBuilder()
    if can_trigger:
        builder.row(InlineKeyboardButton(text='РЎР‚РЎСџРЎв„ўР вЂљ Р В РЎвЂєР В Р’В±Р В Р вЂ¦Р В РЎвЂўР В Р вЂ Р В РЎвЂР РЋРІР‚С™Р РЋР Р‰ Р В Р’В±Р В РЎвЂўР РЋРІР‚С™Р В Р’В°', callback_data='adm:updates:run'))
    builder.row(InlineKeyboardButton(text='РЎР‚РЎСџРІР‚СљР’В¦ Р В РЎвЂєР РЋРІР‚С™Р В РЎвЂќР РЋР вЂљР РЋРІР‚в„–Р РЋРІР‚С™Р РЋР Р‰ Р В РЎвЂўР В Р’В±Р В Р вЂ¦Р В РЎвЂўР В Р вЂ Р В Р’В»Р В Р’ВµР В Р вЂ¦Р В РЎвЂР РЋР РЏ', callback_data='adm:updates'))
    return builder.as_markup()
