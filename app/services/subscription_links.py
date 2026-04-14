from __future__ import annotations

import base64
import hashlib
import hmac
import time
from ipaddress import ip_address
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

from app.config import settings


TOKEN_SECRET = settings.bot_token.encode('utf-8')


def _sign_payload(raw_value: bytes) -> str:
    return hmac.new(TOKEN_SECRET, raw_value, hashlib.sha256).hexdigest()[:24]


def _build_signed_token(prefix: str, value: int) -> str:
    raw_value = f'{prefix}:{value}'.encode('utf-8')
    digest = _sign_payload(raw_value)
    return f'{value}.{digest}'


def _parse_signed_token(prefix: str, token: str) -> int | None:
    if not token or '.' not in token:
        return None
    raw_id, provided_digest = token.split('.', 1)
    try:
        value = int(raw_id)
    except ValueError:
        return None
    expected_digest = _build_signed_token(prefix, value).split('.', 1)[1]
    if not hmac.compare_digest(provided_digest, expected_digest):
        return None
    return value


def _is_public_base_usable(base: str) -> bool:
    if not base:
        return False
    parsed = urlsplit(base)
    host = (parsed.hostname or '').strip().lower()
    if not host or host == 'localhost':
        return False
    try:
        address = ip_address(host)
    except ValueError:
        return True
    return not (address.is_loopback or address.is_unspecified)


def build_subscription_token(subscription_id: int) -> str:
    return _build_signed_token('sub', subscription_id)


def parse_subscription_token(token: str) -> int | None:
    return _parse_signed_token('sub', token)


def build_access_token(user_id: int) -> str:
    return _build_signed_token('access', user_id)


def parse_access_token(token: str) -> int | None:
    return _parse_signed_token('access', token)


def build_reserve_action_token(user_id: int, key_id: int, action: str = 'replace') -> str:
    issued_at = int(time.time())
    raw_value = f'action:{action}:{user_id}:{key_id}:{issued_at}'.encode('utf-8')
    digest = _sign_payload(raw_value)
    return f'{issued_at}.{digest}'


def verify_reserve_action_token(token: str, user_id: int, key_id: int, action: str = 'replace', max_age_seconds: int = 900) -> bool:
    if not token or '.' not in token:
        return False
    raw_ts, provided_digest = token.split('.', 1)
    try:
        issued_at = int(raw_ts)
    except ValueError:
        return False
    now = int(time.time())
    if issued_at <= 0 or now - issued_at > max_age_seconds:
        return False
    raw_value = f'action:{action}:{user_id}:{key_id}:{issued_at}'.encode('utf-8')
    expected_digest = _sign_payload(raw_value)
    return hmac.compare_digest(provided_digest, expected_digest)


def build_public_subscription_base() -> str:
    return (settings.public_base_url or '').strip().rstrip('/')


def build_bot_subscription_url(subscription) -> str:
    base = build_public_subscription_base()
    if not _is_public_base_usable(base):
        return ''
    return f"{base}/sub/{build_subscription_token(subscription.id)}"


def build_reserve_access_url(user) -> str:
    base = build_public_subscription_base()
    user_id = getattr(user, 'id', None)
    if not user_id or not _is_public_base_usable(base):
        return ''
    return f"{base}/access/{build_access_token(int(user_id))}"


def build_xui_subscription_url(subscription) -> str | None:
    active_keys = [key for key in getattr(subscription, 'keys', []) or [] if getattr(key, 'is_active', True)]
    keys = active_keys or list(getattr(subscription, 'keys', []) or [])

    endpoints: list[tuple[str, str]] = []
    for key in keys:
        server = getattr(key, 'server', None)
        base_url = (getattr(server, 'base_url', '') or '').strip()
        if not base_url:
            continue
        parsed = urlsplit(base_url)
        host = parsed.hostname
        if not host:
            continue
        scheme = (settings.xui_subscription_scheme or parsed.scheme or 'https').strip() or 'https'
        endpoints.append((scheme, host))

    unique_endpoints = list(dict.fromkeys(endpoints))
    if len(unique_endpoints) != 1:
        return None

    scheme, host = unique_endpoints[0]
    path = (settings.xui_subscription_path or '/sub/').strip() or '/sub/'
    if not path.startswith('/'):
        path = f'/{path}'
    if not path.endswith('/'):
        path += '/'

    port = settings.xui_subscription_port
    netloc = f'{host}:{port}' if port else host
    base = urlunsplit((scheme, netloc, path, '', '')).rstrip('/')
    return f'{base}/{subscription.id}'


def build_xui_server_subscription_url(server, subscription_id: int) -> str | None:
    base_url = (getattr(server, 'base_url', '') or '').strip()
    if not base_url or not subscription_id:
        return None
    parsed = urlsplit(base_url)
    host = (parsed.hostname or '').strip()
    if not host:
        return None
    scheme = (settings.xui_subscription_scheme or parsed.scheme or 'https').strip() or 'https'
    path = (settings.xui_subscription_path or '/sub/').strip() or '/sub/'
    if not path.startswith('/'):
        path = f'/{path}'
    if not path.endswith('/'):
        path += '/'
    port = settings.xui_subscription_port
    netloc = f'{host}:{port}' if port else host
    base = urlunsplit((scheme, netloc, path, '', '')).rstrip('/')
    return f'{base}/{subscription_id}'


def build_subscription_url(subscription) -> str:
    native_url = build_xui_subscription_url(subscription)
    if native_url:
        return native_url
    bot_url = build_bot_subscription_url(subscription)
    return bot_url or ''


def subscription_server_names(subscription) -> list[str]:
    keys = [key for key in getattr(subscription, 'keys', []) or [] if getattr(key, 'is_active', True)]
    if not keys:
        keys = list(getattr(subscription, 'keys', []) or [])

    names: list[str] = []
    for key in keys:
        server_name = (getattr(getattr(key, 'server', None), 'name', '') or '').strip()
        if not server_name:
            label = (getattr(key, 'label', '') or '').strip()
            server_name = label.split(' / ')[-1].strip() if label else 'Сервер'
        if server_name not in names:
            names.append(server_name)
    return names


def iter_subscription_urls(subscription) -> Iterable[str]:
    for key in getattr(subscription, 'keys', []) or []:
        access_url = (getattr(key, 'access_url', '') or '').strip()
        is_active = getattr(key, 'is_active', True)
        if access_url and is_active:
            yield access_url


def encode_subscription_payload(subscription) -> str:
    raw_payload = '\n'.join(iter_subscription_urls(subscription))
    return base64.b64encode(raw_payload.encode('utf-8')).decode('utf-8')


def decode_subscription_payload(payload: str) -> list[str]:
    text = (payload or '').strip()
    if not text:
        return []
    try:
        decoded = base64.b64decode(text, validate=True).decode('utf-8', 'replace')
    except Exception:
        decoded = text
    lines: list[str] = []
    for line in decoded.replace('\r', '\n').split('\n'):
        item = line.strip()
        if item:
            lines.append(item)
    return lines
