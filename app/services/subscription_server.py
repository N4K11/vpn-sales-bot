from __future__ import annotations

import logging
from datetime import datetime
from html import escape
from ipaddress import ip_address
from time import monotonic
from urllib.parse import quote_plus

import aiohttp
from aiohttp import web

from app.config import settings
from app.utils import format_gb, is_future_datetime

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


logger = logging.getLogger(__name__)

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


def _external_request_url(request: web.Request, include_query: bool = True) -> str:
    proto = (request.headers.get('X-Forwarded-Proto') or request.scheme or 'https').strip()
    host = (request.headers.get('X-Forwarded-Host') or request.headers.get('Host') or request.host or '').strip()
    path_qs = request.path_qs if include_query else request.path
    return f'{proto}://{host}{path_qs}'


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


def _link_preview(value: str, limit: int = 78) -> str:
    text = (value or '').strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + '…'


def _render_copy_tile(title: str, value: str, caption: str, tone: str = '') -> str:
    link = (value or '').strip()
    if not link:
        return ''
    tone_class = f' {tone}' if tone else ''
    return (
        f'<button class="copy-tile{tone_class}" type="button" data-copy="{escape(link)}" onclick="copyValue(this)">'
        f'<span class="copy-title">{escape(title)}</span>'
        f'<span class="copy-value">{escape(_link_preview(link))}</span>'
        f'<span class="copy-caption">{escape(caption)}</span>'
        '</button>'
    )


def _server_sort_name(key) -> str:
    return str(getattr(getattr(key, 'server', None), 'name', '') or '').strip().lower()

def _render_key_card(key, subscription, access_token: str, user_id: int) -> str:
    key_id = getattr(key, 'id', 0)
    access_url = (getattr(key, 'access_url', '') or '').strip()
    server_name = escape(getattr(getattr(key, 'server', None), 'name', '') or 'Сервер')
    is_active = _is_active_key(key, subscription)
    status_badge = '<span class="badge badge-ok">🟢 Рабочий</span>' if is_active else '<span class="badge badge-bad">🔴 Неактивен</span>'
    replace_block = ''
    if getattr(key, 'is_active', False) and _is_active_subscription(subscription):
        action_token = build_reserve_action_token(user_id=user_id, key_id=key_id, action='replace')
        replace_block = (
            '<div class="actions-row">'
            f'<form method="post" action="/access/{access_token}/key/{key_id}/replace" class="inline-form">'
            f'<input type="hidden" name="action_token" value="{escape(action_token)}">'
            '<button class="btn btn-secondary" type="submit">♻️ Заменить ключ</button>'
            '</form>'
            '</div>'
        )
    copy_tile = _render_copy_tile(f'Ключ • {server_name}', access_url, 'Нажмите, чтобы скопировать адрес', tone='soft')
    if not copy_tile:
        copy_tile = '<p class="muted compact">Ключ ещё не подтянулся.</p>'
    used_text = format_gb(getattr(key, 'used_bytes', 0) or 0)
    return (
        '<div class="key-card">'
        f'<div class="key-header"><div><strong>{server_name}</strong><p class="muted compact">Трафик: {used_text}</p></div>{status_badge}</div>'
        f'{copy_tile}'
        f'{replace_block}'
        '</div>'
    )

def _render_subscription_card(subscription, access_token: str, user_id: int) -> str:
    sub_url = build_subscription_url(subscription)
    names = subscription_server_names(subscription)
    keys = list(getattr(subscription, 'keys', []) or [])
    keys.sort(key=lambda item: (0 if _is_active_key(item, subscription) else 1, _server_sort_name(item)))
    key_cards = ''.join(_render_key_card(key, subscription, access_token, user_id) for key in keys)
    servers_preview = escape(', '.join(names) if names else 'Серверы пока не подтянулись')
    status_badge = '<span class="badge badge-ok">🟢 Активна</span>' if _is_active_subscription(subscription) else '<span class="badge badge-bad">🔴 Истекла</span>'
    subscription_tile = _render_copy_tile('Общая ссылка подписки', sub_url, 'Нажмите, чтобы скопировать адрес для клиента', tone='accent')
    if not subscription_tile:
        subscription_tile = '<p class="muted compact">Общая ссылка пока не сформировалась.</p>'
    return (
        '<section class="subscription-card">'
        f'<div class="card-top"><div><h3>{escape(_subscription_title(subscription))}</h3><p class="muted compact">До {_format_datetime(getattr(subscription, "ends_at", None))}</p></div>{status_badge}</div>'
        f'<p class="muted">🌐 Серверы: {servers_preview}</p>'
        f'{subscription_tile}'
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
    headers['Profile-Web-Page-Url'] = _external_request_url(request, include_query=False)
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
    rendered_cards: list[str] = []
    for subscription in active_subscriptions:
        try:
            rendered_cards.append(_render_subscription_card(subscription, token, user_id))
        except Exception:
            logger.exception('Failed to render reserve subscription card for subscription %s', getattr(subscription, 'id', None))
            rendered_cards.append(
                '<section class="subscription-card">'
                '<div class="card-top"><div><h3>Подписка временно недоступна</h3><p class="muted compact">Не удалось собрать карточку этой подписки.</p></div><span class="badge badge-bad">⚠️ Ошибка</span></div>'
                '<p class="muted">Попробуйте обновить страницу чуть позже. Если проблема повторится, вернитесь в Telegram и перевыпустите ключ после обновления бота.</p>'
                '</section>'
            )
    active_cards = ''.join(rendered_cards)
    user_name = escape(getattr(user, 'full_name', '') or getattr(user, 'username', '') or str(getattr(user, 'telegram_id', 'Пользователь')))
    reserve_tile = _render_copy_tile('Личная ссылка на кабинет', reserve_url, 'Нажмите, чтобы скопировать и сохраните отдельно', tone='accent')
    if not reserve_tile:
        reserve_tile = '<p class="muted compact">Резервная ссылка сейчас временно недоступна.</p>'
    return f'''<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Резервный доступ</title>
  <style>
    :root {{
      --bg: #0b1320;
      --bg-soft: #101c2a;
      --panel: rgba(15, 24, 36, 0.94);
      --panel-soft: rgba(27, 39, 53, 0.92);
      --line: rgba(255,255,255,0.08);
      --line-strong: rgba(255,255,255,0.12);
      --text: #eef5fb;
      --muted: #9fb2c5;
      --accent: #f2c869;
      --accent-2: #73d7cb;
      --ok: #67d591;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Segoe UI, Arial, sans-serif; color: var(--text); background: radial-gradient(circle at top, #183247 0%, var(--bg) 48%, #08111d 100%); }}
    .shell {{ max-width: 1080px; margin: 0 auto; padding: 28px 20px 40px; }}
    .hero {{ background: linear-gradient(145deg, rgba(242,200,105,0.12), rgba(115,215,203,0.10)); border: 1px solid var(--line); border-radius: 28px; padding: 24px; box-shadow: 0 24px 60px rgba(0,0,0,0.30); }}
    .hero-badge {{ display: inline-flex; align-items: center; gap: 8px; padding: 8px 14px; border-radius: 999px; background: rgba(115,215,203,0.14); border: 1px solid rgba(115,215,203,0.20); color: #dff8f3; font-size: 14px; }}
    h1, h2, h3, p {{ margin-top: 0; }}
    h1 {{ margin: 16px 0 12px; font-size: clamp(34px, 5vw, 52px); line-height: 1.05; }}
    h2 {{ font-size: 28px; margin-bottom: 10px; }}
    h3 {{ font-size: 24px; margin-bottom: 8px; }}
    .lead {{ font-size: 18px; line-height: 1.55; max-width: 780px; }}
    .muted {{ color: var(--muted); }}
    .compact {{ margin-bottom: 0; }}
    .notice {{ margin-top: 18px; padding: 14px 16px; border-radius: 18px; background: rgba(115,215,203,0.12); border: 1px solid rgba(115,215,203,0.22); }}
    .hero-grid {{ display: grid; grid-template-columns: 1.2fr 0.8fr; gap: 18px; margin-top: 24px; }}
    .grid {{ display: grid; gap: 18px; margin-top: 22px; }}
    .panel, .subscription-card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 24px; padding: 20px; box-shadow: 0 18px 42px rgba(0,0,0,0.22); }}
    .panel.soft {{ background: rgba(12, 20, 31, 0.76); }}
    .tips {{ display: grid; gap: 10px; margin: 0; padding: 0; list-style: none; }}
    .tips li {{ padding: 12px 14px; border-radius: 16px; background: rgba(255,255,255,0.03); border: 1px solid var(--line); color: var(--muted); line-height: 1.45; }}
    .copy-tile {{ width: 100%; border: 1px solid var(--line-strong); background: linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01)); color: var(--text); border-radius: 18px; padding: 16px; text-align: left; cursor: pointer; transition: transform 0.16s ease, border-color 0.16s ease, background 0.16s ease; }}
    .copy-tile:hover {{ transform: translateY(-1px); border-color: rgba(255,255,255,0.18); }}
    .copy-tile.accent {{ background: linear-gradient(160deg, rgba(242,200,105,0.14), rgba(115,215,203,0.10)); }}
    .copy-tile.soft {{ background: rgba(255,255,255,0.03); }}
    .copy-tile.copied {{ border-color: rgba(103,213,145,0.36); box-shadow: 0 0 0 1px rgba(103,213,145,0.12) inset; }}
    .copy-title {{ display: block; font-size: 13px; color: var(--muted); margin-bottom: 8px; }}
    .copy-value {{ display: block; font-family: Consolas, monospace; font-size: 14px; line-height: 1.55; word-break: break-all; }}
    .copy-caption {{ display: block; margin-top: 10px; font-size: 13px; color: #d8e2eb; }}
    .card-top, .key-header {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }}
    .badge {{ display: inline-flex; align-items: center; gap: 6px; border-radius: 999px; padding: 6px 10px; font-size: 13px; border: 1px solid var(--line); white-space: nowrap; }}
    .badge-ok {{ background: rgba(103,213,145,0.14); color: #cdf4db; }}
    .badge-bad {{ background: rgba(240,115,115,0.14); color: #ffc6c6; }}
    .subkeys {{ display: grid; gap: 12px; margin-top: 16px; }}
    .key-card {{ background: var(--panel-soft); border: 1px solid var(--line); border-radius: 18px; padding: 14px; }}
    .actions-row {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 12px; }}
    .btn {{ display: inline-flex; align-items: center; justify-content: center; gap: 8px; padding: 10px 14px; border-radius: 14px; text-decoration: none; cursor: pointer; border: 1px solid transparent; font-weight: 600; }}
    .btn-secondary {{ background: transparent; border-color: var(--line-strong); color: var(--text); }}
    .inline-form {{ margin: 0; }}
    @media (max-width: 860px) {{
      .shell {{ padding: 16px 14px 28px; }}
      .hero-grid {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 34px; }}
      .lead {{ font-size: 16px; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <span class="hero-badge">🌍 Резервный доступ</span>
      <h1>Личный кабинет на случай, если Telegram недоступен</h1>
      <p class="muted lead">Здесь можно быстро забрать общую ссылку подписки, скопировать ключ по нужному серверу и заменить его, если он перестал подключаться.</p>
      {notice_block}
      <div class="hero-grid">
        <div class="panel">
          <h2>{user_name}</h2>
          <p class="muted">Сохраните эту ссылку заранее в заметках или браузере. Она пригодится, если Telegram временно перестанет открываться.</p>
          {reserve_tile}
        </div>
        <div class="panel soft">
          <h3>Что можно сделать здесь</h3>
          <ul class="tips">
            <li>🌐 Скопировать общую ссылку подписки и заново импортировать её в клиент.</li>
            <li>🔑 Забрать отдельный ключ по серверу, если нужен точечный доступ.</li>
            <li>♻️ Заменить ключ, если он перестал подключаться или работает нестабильно.</li>
          </ul>
        </div>
      </div>
    </section>

    <div class="grid">
      {active_cards or '<div class="panel"><h3>Активных подписок пока нет</h3><p class="muted">Когда появится активный доступ, он будет показан здесь.</p></div>'}
    </div>
  </div>
  <script>
    async function copyValue(button) {{
      const value = button?.dataset?.copy || '';
      if (!value) return;
      const caption = button.querySelector('.copy-caption');
      const original = caption ? caption.textContent : '';
      try {{
        await navigator.clipboard.writeText(value);
      }} catch (e) {{
        const input = document.createElement('textarea');
        input.value = value;
        input.setAttribute('readonly', 'readonly');
        input.style.position = 'absolute';
        input.style.opacity = '0';
        document.body.appendChild(input);
        input.select();
        document.execCommand('copy');
        document.body.removeChild(input);
      }}
      button.classList.add('copied');
      if (caption) caption.textContent = 'Скопировано';
      setTimeout(() => {{
        button.classList.remove('copied');
        if (caption) caption.textContent = original;
      }}, 1800);
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
