from __future__ import annotations

from datetime import datetime
from html import escape
from ipaddress import ip_address
from time import monotonic
from urllib.parse import quote_plus

import aiohttp
from aiohttp import web

from app.config import settings
from app.utils import is_future_datetime

from app.services.provisioning import ProvisioningService
from app.services.store import Store
from app.services.subscription_links import (
    build_reserve_access_url,
    build_reserve_action_token,
    build_subscription_url,
    build_xui_server_subscription_url,
    decode_subscription_payload,
    encode_subscription_payload,
    iter_subscription_urls,
    parse_access_token,
    parse_subscription_token,
    subscription_server_names,
    verify_reserve_action_token,
)


_REPLACE_COOLDOWN_SECONDS = 45.0
_LAST_REPLACE_ACTIONS: dict[tuple[int, int], float] = {}


def _security_headers(html: bool = False, extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        'Cache-Control': 'no-store, no-cache, must-revalidate, private',
        'Pragma': 'no-cache',
        'Expires': '0',
        'X-Frame-Options': 'DENY',
        'X-Content-Type-Options': 'nosniff',
        'Referrer-Policy': 'no-referrer',
        'X-Robots-Tag': 'noindex, nofollow, noarchive',
    }
    if html:
        headers['Content-Security-Policy'] = "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline';"
    if extra:
        headers.update(extra)
    return headers


def _hidden_not_found() -> web.HTTPNotFound:
    return web.HTTPNotFound(text='Not Found', headers=_security_headers())


def _is_internal_request(request: web.Request) -> bool:
    remote = (request.remote or '').strip()
    if not remote:
        return False
    try:
        addr = ip_address(remote)
    except ValueError:
        return False
    return bool(addr.is_loopback or addr.is_private)


def _is_active_subscription(subscription) -> bool:
    return bool(subscription and getattr(subscription, 'status', '') == 'active' and is_future_datetime(getattr(subscription, 'ends_at', None)))


def _is_active_key(key, subscription) -> bool:
    return bool(getattr(key, 'is_active', False) and _is_active_subscription(subscription))


def _subscription_title(subscription) -> str:
    if getattr(subscription, 'is_trial', False):
        return 'Пробный доступ'
    tariff = getattr(subscription, 'tariff', None)
    if tariff:
        return getattr(tariff, 'name', 'Подписка')
    return 'Ручной доступ'


def _format_datetime(value) -> str:
    if not value:
        return '—'
    return value.strftime('%d.%m.%Y %H:%M')


def _render_key_card(key, subscription, access_token: str, user_id: int) -> str:
    key_id = getattr(key, 'id', 0)
    access_url = (getattr(key, 'access_url', '') or '').strip()
    server_name = escape(getattr(getattr(key, 'server', None), 'name', '') or 'Сервер')
    is_active = _is_active_key(key, subscription)
    status_badge = '<span class="badge badge-ok">🟢 Рабочий</span>' if is_active else '<span class="badge badge-bad">🔴 Неактивен</span>'
    replace_button = ''
    if getattr(key, 'is_active', False) and _is_active_subscription(subscription):
        action_token = build_reserve_action_token(user_id=user_id, key_id=key_id, action='replace')
        replace_button = (
            f'<form method="post" action="/access/{access_token}/key/{key_id}/replace" class="inline-form">'
            f'<input type="hidden" name="action_token" value="{escape(action_token)}">'
            '<button class="btn btn-secondary" type="submit">♻️ Перевыпустить ключ</button>'
            '</form>'
        )
    copy_button = f'<button class="btn btn-primary" type="button" onclick="copyText(\'key-{key_id}\')">📋 Скопировать ключ</button>' if access_url else ''
    safe_url = escape(access_url)
    return (
        f'<div class="key-card" id="key-{key_id}">'
        f'<div class="key-header"><div><strong>{server_name}</strong></div>{status_badge}</div>'
        f'<textarea readonly class="mono" id="key-value-{key_id}">{safe_url}</textarea>'
        '<div class="actions-row">'
        f'{copy_button}'
        f'{replace_button}'
        '</div>'
        '</div>'
    )


def _render_subscription_card(subscription, access_token: str, user_id: int) -> str:
    sub_id = getattr(subscription, 'id', 0)
    sub_url = build_subscription_url(subscription)
    reserve_link = ''
    if sub_url:
        reserve_link = (
            f'<div class="actions-row">'
            f'<a class="btn btn-primary" href="{escape(sub_url)}" target="_blank" rel="noopener">🌐 Открыть ссылку подписки</a>'
            f'<button class="btn btn-secondary" type="button" onclick="copyText(\'sub-{sub_id}\')">📋 Скопировать ссылку</button>'
            '</div>'
        )
    names = subscription_server_names(subscription)
    keys = list(getattr(subscription, 'keys', []) or [])
    keys.sort(key=lambda item: (0 if _is_active_key(item, subscription) else 1, getattr(getattr(item, 'server', None), 'name', '')))
    key_cards = ''.join(_render_key_card(key, subscription, access_token, user_id) for key in keys)
    servers_preview = escape(', '.join(names) if names else 'Серверы пока не подтянулись')
    status_badge = '<span class="badge badge-ok">🟢 Активна</span>' if _is_active_subscription(subscription) else '<span class="badge badge-bad">🔴 Истекла</span>'
    return (
        '<section class="subscription-card">'
        f'<div class="card-top"><div><h3>{escape(_subscription_title(subscription))}</h3><p>До {_format_datetime(getattr(subscription, "ends_at", None))}</p></div>{status_badge}</div>'
        f'<p class="muted">🌐 Серверы: {servers_preview}</p>'
        f'<textarea readonly class="mono" id="sub-{sub_id}">{escape(sub_url)}</textarea>'
        f'{reserve_link}'
        '<div class="subkeys">'
        f'{key_cards or "<p class=\"muted\">Ключи пока не готовы.</p>"}'
        '</div>'
        '</section>'
    )


async def _fetch_native_subscription_urls(subscription) -> list[str]:
    active_keys = [key for key in getattr(subscription, 'keys', []) or [] if getattr(key, 'is_active', True)]
    keys = active_keys or list(getattr(subscription, 'keys', []) or [])
    unique_servers = []
    seen = set()
    for key in keys:
        server = getattr(key, 'server', None)
        server_id = getattr(server, 'id', None)
        if not server or server_id in seen:
            continue
        seen.add(server_id)
        unique_servers.append(server)

    timeout = aiohttp.ClientTimeout(total=settings.xui_request_timeout)
    connector = aiohttp.TCPConnector(ssl=settings.xui_verify_ssl)
    merged: list[str] = []
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        for server in unique_servers:
            native_url = build_xui_server_subscription_url(server, getattr(subscription, 'id', 0))
            if not native_url:
                continue
            try:
                async with session.get(native_url) as response:
                    response.raise_for_status()
                    payload = await response.text()
                lines = decode_subscription_payload(payload)
            except Exception:
                lines = []
            if not lines:
                for key in keys:
                    if getattr(key, 'server_id', None) == getattr(server, 'id', None):
                        access_url = (getattr(key, 'access_url', '') or '').strip()
                        if access_url:
                            lines.append(access_url)
            for line in lines:
                if line not in merged:
                    merged.append(line)
    return merged


def _build_subscription_headers(request: web.Request, subscription, url_count: int) -> dict[str, str]:
    headers = _security_headers(extra={'X-Subscription-Servers': str(url_count)})
    headers['Profile-Update-Interval'] = '12'
    headers['Profile-Web-Page-Url'] = str(request.url.with_query(None))
    expire_at = getattr(subscription, 'ends_at', None)
    expire_ts = int(expire_at.timestamp()) if expire_at else 0
    headers['Subscription-Userinfo'] = f'upload=0; download=0; total=0; expire={expire_ts}'
    return headers


def _choose_subscription_format(request: web.Request) -> str:
    explicit = (request.query.get('format') or '').strip().lower()
    if explicit in {'raw', 'base64'}:
        return explicit
    user_agent = (request.headers.get('User-Agent') or '').strip().lower()
    raw_markers = (
        'happ',
        'hiddify',
        'sing-box',
        'sfa',
        'v2box',
        'shadowrocket',
        'stash',
        'loon',
        'quantumult',
        'surge',
        'clash',
    )
    if any(marker in user_agent for marker in raw_markers):
        return 'raw'
    return 'base64'


def _render_access_page(user, notice: str = '') -> str:
    reserve_url = build_reserve_access_url(user)
    active_subscriptions = [sub for sub in getattr(user, 'subscriptions', []) or [] if _is_active_subscription(sub)]
    active_subscriptions.sort(key=lambda item: getattr(item, 'ends_at', datetime.min), reverse=True)
    token = reserve_url.rsplit('/', 1)[-1] if reserve_url else ''
    notice = (notice or '').strip()[:240]
    notice_block = f'<div class="notice">{escape(notice)}</div>' if notice else ''
    user_id = int(getattr(user, 'id', 0) or 0)
    active_cards = ''.join(_render_subscription_card(subscription, token, user_id) for subscription in active_subscriptions)
    user_name = escape(getattr(user, 'full_name', '') or getattr(user, 'username', '') or str(getattr(user, 'telegram_id', 'Пользователь')))
    safe_reserve_url = escape(reserve_url)
    return f'''<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Резервный доступ</title>
  <style>
    :root {{
      --bg: #0f1720;
      --panel: rgba(18, 28, 38, 0.92);
      --panel-soft: rgba(27, 40, 53, 0.92);
      --line: rgba(255,255,255,0.08);
      --text: #eef4f8;
      --muted: #9fb0bf;
      --accent: #e5b34e;
      --accent-2: #76d1c8;
      --danger: #f07373;
      --ok: #56cf87;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Segoe UI, Arial, sans-serif; background: radial-gradient(circle at top, #183247 0%, var(--bg) 52%); color: var(--text); }}
    .shell {{ max-width: 1040px; margin: 0 auto; padding: 24px; }}
    .hero {{ background: linear-gradient(140deg, rgba(229,179,78,0.16), rgba(118,209,200,0.12)); border: 1px solid var(--line); border-radius: 24px; padding: 24px; box-shadow: 0 24px 50px rgba(0,0,0,0.28); }}
    h1, h2, h3, p {{ margin-top: 0; }}
    .muted {{ color: var(--muted); }}
    .notice {{ margin: 18px 0; padding: 14px 16px; border-radius: 16px; background: rgba(118, 209, 200, 0.12); border: 1px solid rgba(118, 209, 200, 0.24); }}
    .grid {{ display: grid; gap: 18px; margin-top: 22px; }}
    .subscription-card, .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 22px; padding: 20px; box-shadow: 0 18px 40px rgba(0,0,0,0.22); }}
    .card-top, .top-row, .key-header {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; }}
    .badge {{ display: inline-flex; align-items: center; gap: 6px; border-radius: 999px; padding: 6px 10px; font-size: 13px; border: 1px solid var(--line); }}
    .badge-ok {{ background: rgba(86,207,135,0.14); color: #baf0ce; }}
    .badge-bad {{ background: rgba(240,115,115,0.14); color: #ffc2c2; }}
    .mono {{ width: 100%; min-height: 86px; padding: 12px; border-radius: 14px; border: 1px solid var(--line); background: var(--panel-soft); color: var(--text); resize: vertical; font-family: Consolas, monospace; font-size: 13px; }}
    .actions-row {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 12px; }}
    .btn {{ display: inline-flex; align-items: center; justify-content: center; gap: 8px; padding: 10px 14px; border-radius: 14px; text-decoration: none; cursor: pointer; border: 1px solid transparent; font-weight: 600; }}
    .btn-primary {{ background: linear-gradient(135deg, var(--accent), #f0ca78); color: #1d2128; }}
    .btn-secondary {{ background: transparent; border-color: var(--line); color: var(--text); }}
    .inline-form {{ margin: 0; }}
    .subkeys {{ display: grid; gap: 12px; margin-top: 16px; }}
    .key-card {{ background: var(--panel-soft); border: 1px solid var(--line); border-radius: 18px; padding: 14px; }}
    .archived-item {{ padding: 12px 14px; border-radius: 16px; background: rgba(255,255,255,0.03); border: 1px solid var(--line); color: var(--muted); }}
    .split {{ display: grid; gap: 18px; grid-template-columns: 1.3fr 0.7fr; margin-top: 22px; }}
    @media (max-width: 860px) {{ .split {{ grid-template-columns: 1fr; }} .shell {{ padding: 14px; }} }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="top-row">
        <div>
          <div class="badge badge-ok">🌍 Резервный доступ</div>
          <h1>Личный аварийный кабинет</h1>
          <p class="muted">Страница работает вне Telegram, не даёт доступ к админке и не умеет выполнять произвольные команды. Здесь доступны только ваши собственные ссылки доступа и контролируемый перевыпуск ключа.</p>
        </div>
      </div>
      {notice_block}
      <div class="split">
        <div class="panel">
          <h2>{user_name}</h2>
          <p class="muted">Сохраните эту ссылку заранее. Если Telegram недоступен, по ней можно открыть общую ссылку подписки и безопасно заменить свой ключ.</p>
          <textarea readonly class="mono" id="reserve-link">{safe_reserve_url}</textarea>
          <div class="actions-row">
            <button class="btn btn-primary" type="button" onclick="copyText('reserve-link')">📋 Скопировать резервную ссылку</button>
            <a class="btn btn-secondary" href="{safe_reserve_url}">🔄 Обновить страницу</a>
          </div>
        </div>
        <div class="panel">
          <h3>Ограничения безопасности</h3>
          <p class="muted">• ссылка открывает доступ только к вашему профилю;</p>
          <p class="muted">• действия подписаны токеном и быстро протухают;</p>
          <p class="muted">• частые перевыпуски режутся лимитом;</p>
          <p class="muted">• админка, серверные команды и настройки отсюда недоступны.</p>
        </div>
      </div>
    </section>

    <div class="grid">
      {active_cards or '<div class="panel"><h3>Активных подписок пока нет</h3><p class="muted">Когда появится активный доступ, он будет показан здесь.</p></div>'}
    </div>
  </div>
  <script>
    async function copyText(id) {{
      const el = document.getElementById(id);
      if (!el) return;
      const value = el.value || el.textContent || '';
      try {{
        await navigator.clipboard.writeText(value);
      }} catch (e) {{
        el.focus();
        el.select();
      }}
    }}
  </script>
</body>
</html>'''


async def subscription_handler(request: web.Request) -> web.Response:
    store: Store = request.app['store']
    token = request.match_info.get('token', '')
    subscription_id = parse_subscription_token(token)
    if not subscription_id:
        raise _hidden_not_found()

    subscription = await store.get_subscription_details(subscription_id)
    if not subscription or subscription.status != 'active' or not is_future_datetime(getattr(subscription, 'ends_at', None)):
        raise _hidden_not_found()

    urls = await _fetch_native_subscription_urls(subscription)
    if not urls:
        raise _hidden_not_found()

    format_name = _choose_subscription_format(request)
    raw_payload = '\n'.join(urls)
    body = raw_payload
    if format_name != 'raw':
        import base64
        body = base64.b64encode(raw_payload.encode('utf-8')).decode('utf-8')
    headers = _build_subscription_headers(request, subscription, len(urls))
    return web.Response(text=body, content_type='text/plain', charset='utf-8', headers=headers)


async def reserve_access_handler(request: web.Request) -> web.Response:
    store: Store = request.app['store']
    if not await store.get_toggle('section_reserve_access', default=True):
        raise _hidden_not_found()
    token = request.match_info.get('token', '')
    user_id = parse_access_token(token)
    if not user_id:
        raise _hidden_not_found()
    user = await store.get_user_admin_summary(user_id)
    if not user:
        raise _hidden_not_found()
    notice = (request.query.get('notice') or '').strip()
    html = _render_access_page(user, notice=notice)
    return web.Response(text=html, content_type='text/html', charset='utf-8', headers=_security_headers(html=True))


async def replace_key_handler(request: web.Request) -> web.Response:
    store: Store = request.app['store']
    provisioning: ProvisioningService = request.app['provisioning']
    if not await store.get_toggle('section_reserve_access', default=True):
        raise _hidden_not_found()
    token = request.match_info.get('token', '')
    key_id_raw = request.match_info.get('key_id', '')
    user_id = parse_access_token(token)
    if not user_id:
        raise _hidden_not_found()
    try:
        key_id = int(key_id_raw)
    except ValueError:
        raise _hidden_not_found()
    form = await request.post()
    action_token = (form.get('action_token') or '').strip()
    if not verify_reserve_action_token(action_token, user_id=user_id, key_id=key_id, action='replace'):
        raise web.HTTPForbidden(text='Action token expired. Reload the page and try again.', headers=_security_headers())

    key = await store.get_key_details(key_id)
    if not key or not key.subscription or key.subscription.user_id != user_id:
        raise _hidden_not_found()

    rate_key = (user_id, key_id)
    now = monotonic()
    last_action = _LAST_REPLACE_ACTIONS.get(rate_key, 0.0)
    if now - last_action < _REPLACE_COOLDOWN_SECONDS:
        notice = quote_plus('Слишком частый перевыпуск. Подождите немного и обновите страницу.')
        raise web.HTTPFound(location=f'/access/{token}?notice={notice}')
    _LAST_REPLACE_ACTIONS[rate_key] = now

    updated_key, _, error = await provisioning.replace_key(key_id)
    if error:
        notice = quote_plus(f'Не удалось перевыпустить ключ: {error}')
    else:
        server_name = getattr(getattr(updated_key, 'server', None), 'name', '') or 'сервер'
        notice = quote_plus(f'Ключ для {server_name} перевыпущен. Импортируйте новый ключ в клиент.')
    raise web.HTTPFound(location=f'/access/{token}?notice={notice}')


async def health_handler(request: web.Request) -> web.Response:
    if not _is_internal_request(request):
        raise _hidden_not_found()
    return web.Response(text='ok', content_type='text/plain', charset='utf-8', headers=_security_headers())


def create_subscription_web_app(store: Store, provisioning: ProvisioningService) -> web.Application:
    app = web.Application()
    app['store'] = store
    app['provisioning'] = provisioning
    app.router.add_get('/healthz', health_handler)
    app.router.add_get('/sub/{token}', subscription_handler)
    app.router.add_get('/access/{token}', reserve_access_handler)
    app.router.add_post('/access/{token}/key/{key_id}/replace', replace_key_handler)
    return app



