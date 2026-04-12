from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import uuid4

import aiohttp

from app.config import settings
from app.db.models import Server, Subscription, User

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class XUIProvisionResult:
    external_id: str
    access_url: str
    email: str


@dataclass(slots=True)
class XUIRoute:
    login_url: str
    api_base: str


class XUIClient:
    def __init__(self, server: Server) -> None:
        self.server = server
        self.base_url = server.base_url.rstrip('/')
        self._resolved_route: XUIRoute | None = None

    async def _request(self, method: str, api_path: str, **kwargs) -> dict:
        last_error: Exception | None = None
        routes = self._candidate_routes()
        if self._resolved_route:
            routes = [self._resolved_route] + [route for route in routes if route != self._resolved_route]

        for route in routes:
            try:
                data = await self._request_via_route(route, method, api_path, **kwargs)
                self._resolved_route = route
                return data
            except aiohttp.ClientResponseError as exc:
                last_error = exc
                if exc.status in {404, 405}:
                    continue
                raise
            except RuntimeError as exc:
                last_error = exc
                continue

        raise last_error or RuntimeError(f'Не удалось подключиться к 3x-ui для сервера {self.server.name}')

    async def _request_via_route(self, route: XUIRoute, method: str, api_path: str, **kwargs) -> dict:
        timeout = aiohttp.ClientTimeout(total=settings.xui_request_timeout)
        connector = aiohttp.TCPConnector(ssl=settings.xui_verify_ssl)
        request_url = f'{route.api_base}{api_path}'

        async with aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            cookie_jar=aiohttp.CookieJar(unsafe=True),
        ) as session:
            login_resp = await session.post(
                route.login_url,
                data={'username': self.server.username, 'password': self.server.password},
            )
            login_resp.raise_for_status()

            async with session.request(method, request_url, **kwargs) as resp:
                resp.raise_for_status()
                return await self._decode_response(resp)

    async def _decode_response(self, resp: aiohttp.ClientResponse) -> dict:
        body = (await resp.text()).strip()
        if not body:
            return {}

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return {'raw': body}

        if isinstance(data, dict) and data.get('success') is False:
            message = data.get('msg') or data.get('message') or '3x-ui вернула ошибку'
            raise RuntimeError(str(message))

        if isinstance(data, dict):
            return data
        return {'obj': data}

    def _candidate_routes(self) -> list[XUIRoute]:
        parsed = urlsplit(self.base_url)
        path = parsed.path.rstrip('/')

        def make_url(route_path: str) -> str:
            return urlunsplit((parsed.scheme, parsed.netloc, route_path or '/', '', ''))

        def normalize(route_path: str) -> str:
            route_path = (route_path or '/').strip()
            if not route_path.startswith('/'):
                route_path = f'/{route_path}'
            return route_path.rstrip('/') or '/'

        routes: list[XUIRoute] = []
        seen: set[tuple[str, str]] = set()

        def add_route(login_path: str, api_base_path: str) -> None:
            login_url = make_url(normalize(login_path))
            api_base = make_url(normalize(api_base_path)).rstrip('/')
            key = (login_url, api_base)
            if key in seen:
                return
            seen.add(key)
            routes.append(XUIRoute(login_url=login_url, api_base=api_base))

        root_path = ''
        panel_path = '/panel'
        if path:
            if path.endswith('/panel'):
                root_path = path[: -len('/panel')]
                panel_path = path
            else:
                root_path = path
                panel_path = f'{path}/panel'

        preferred_pairs: list[tuple[str, str]] = [
            ('/login', '/panel/api'),
            ('/panel/login', '/panel/api'),
            ('/login', '/api'),
        ]

        if root_path:
            preferred_pairs.extend(
                [
                    ('/login', f'{root_path}/panel/api'),
                    (f'{root_path}/login', '/panel/api'),
                    (f'{root_path}/login', f'{root_path}/panel/api'),
                    (f'{root_path}/panel/login', f'{root_path}/panel/api'),
                    (f'{root_path}/login', f'{root_path}/api'),
                ]
            )
        if panel_path and panel_path != '/panel':
            preferred_pairs.extend(
                [
                    ('/login', f'{panel_path}/api'),
                    (f'{panel_path}/login', f'{panel_path}/api'),
                ]
            )

        for login_path, api_base_path in preferred_pairs:
            add_route(login_path, api_base_path)

        login_candidates = ['/login', '/panel/login']
        api_candidates = ['/panel/api', '/api']
        if root_path:
            login_candidates.extend([f'{root_path}/login', f'{root_path}/panel/login'])
            api_candidates.extend([f'{root_path}/panel/api', f'{root_path}/api'])
        if panel_path and panel_path != '/panel':
            login_candidates.append(f'{panel_path}/login')
            api_candidates.append(f'{panel_path}/api')

        for login_path in login_candidates:
            for api_base_path in api_candidates:
                add_route(login_path, api_base_path)

        return routes

    async def fetch_server_status(self) -> tuple[str, int, int, str]:
        try:
            for method in ('GET', 'POST'):
                try:
                    data = await self._request(method, '/server/status')
                    obj = data.get('obj') or data
                    cpu_percent = int(float(obj.get('cpu', 0)))
                    ram_percent = int(float(obj.get('mem', obj.get('memory', 0))))
                    return 'online', cpu_percent, ram_percent, ''
                except Exception:
                    continue

            await self._request('GET', '/inbounds/list')
            return 'online', 0, 0, ''
        except Exception as exc:
            error_text = self._describe_exception(exc)
            logger.warning('Unable to fetch server status for %s: %s', self.server.name, error_text)
            return 'offline', 0, 0, error_text

    async def _resolve_inbound(self) -> tuple[int, dict]:
        try:
            inbound_data = await self._request('GET', f'/inbounds/get/{self.server.inbound_id}')
            inbound = inbound_data.get('obj') or {}
            if inbound:
                return int(inbound.get('id') or self.server.inbound_id), inbound
        except Exception:
            pass

        list_data = await self._request('GET', '/inbounds/list')
        inbounds = list_data.get('obj') or []
        if not isinstance(inbounds, list):
            inbounds = []

        for inbound in inbounds:
            inbound_id = inbound.get('id')
            port = inbound.get('port')
            if inbound_id == self.server.inbound_id or port == self.server.inbound_id:
                return int(inbound_id), inbound

        if len(inbounds) == 1:
            inbound = inbounds[0]
            resolved_id = int(inbound.get('id') or self.server.inbound_id)
            logger.warning(
                'Saved inbound_id %s was not found for server %s. Falling back to the only inbound %s.',
                self.server.inbound_id,
                self.server.name,
                resolved_id,
            )
            return resolved_id, inbound

        available_ids = ', '.join(str(item.get('id')) for item in inbounds if item.get('id') is not None)
        raise RuntimeError(
            f'Inbound ID {self.server.inbound_id} не найден для сервера {self.server.name}. '
            f'Доступные ID: {available_ids or "нет"}'
        )

    async def provision_key(self, user: User, subscription: Subscription) -> XUIProvisionResult:
        resolved_inbound_id, inbound = await self._resolve_inbound()
        protocol = (inbound.get('protocol') or 'vless').lower()

        secret = str(uuid4())
        email = self._subscription_email(user, subscription)
        expiry_ms = int(subscription.ends_at.timestamp() * 1000)
        client = self._build_client(protocol, inbound, secret, email, expiry_ms, user, subscription)

        payload = {
            'id': resolved_inbound_id,
            'settings': json.dumps({'clients': [client]}, ensure_ascii=False),
        }

        await self._request('POST', '/inbounds/addClient', json=payload)
        access_url = self._build_connection_string(inbound, client, email)
        external_id = self._client_identifier(protocol, client, email, fallback=secret)
        return XUIProvisionResult(external_id=external_id, access_url=access_url, email=email)

    async def update_key(self, key, subscription: Subscription, user: User) -> XUIProvisionResult:
        return await self._mutate_existing_key(key, subscription, user, rotate_secret=False)

    async def replace_key(self, key, subscription: Subscription, user: User) -> XUIProvisionResult:
        return await self._mutate_existing_key(key, subscription, user, rotate_secret=True)

    async def delete_key(self, key, subscription: Subscription, user: User) -> None:
        resolved_inbound_id, inbound = await self._resolve_inbound()
        protocol = (inbound.get('protocol') or 'vless').lower()
        email = self._subscription_email(user, subscription)
        client = self._find_client(inbound, protocol, key.external_id, email, raise_if_missing=False)
        client_id = self._client_identifier(protocol, client or {}, email, fallback=key.external_id or email)

        attempts: list[str] = []
        if client_id:
            attempts.append(f'/inbounds/{resolved_inbound_id}/delClient/{quote(str(client_id), safe="")}')
        if email:
            attempts.append(f'/inbounds/{resolved_inbound_id}/delClientByEmail/{quote(email, safe="")}')

        last_error: Exception | None = None
        for api_path in list(dict.fromkeys(attempts)):
            try:
                await self._request('POST', api_path)
                return
            except aiohttp.ClientResponseError as exc:
                last_error = exc
                if exc.status == 404:
                    continue
                raise
            except RuntimeError as exc:
                message = str(exc).lower()
                if 'not found' in message or 'record not found' in message or 'inbound not found for email' in message:
                    return
                last_error = exc
                continue

        if last_error:
            raise last_error

    async def get_client_traffic(self, email: str) -> int:
        try:
            data = await self._request('GET', f'/inbounds/getClientTraffics/{email}')
            obj = data.get('obj') or {}
            return int(obj.get('up', 0)) + int(obj.get('down', 0))
        except RuntimeError as exc:
            message = str(exc)
            if 'Inbound Not Found For Email' in message or 'record not found' in message:
                return 0
            logger.warning('Unable to fetch client traffic for %s: %s', email, self._describe_exception(exc))
            return 0
        except Exception as exc:
            logger.warning('Unable to fetch client traffic for %s: %s', email, self._describe_exception(exc))
            return 0

    async def _mutate_existing_key(self, key, subscription: Subscription, user: User, *, rotate_secret: bool) -> XUIProvisionResult:
        resolved_inbound_id, inbound = await self._resolve_inbound()
        protocol = (inbound.get('protocol') or 'vless').lower()
        email = self._subscription_email(user, subscription)
        existing_client = self._find_client(inbound, protocol, key.external_id, email)
        current_secret = self._client_secret(protocol, existing_client, fallback=key.external_id)
        secret = str(uuid4()) if rotate_secret or not current_secret else current_secret
        expiry_ms = int(subscription.ends_at.timestamp() * 1000)
        client = self._build_client(protocol, inbound, secret, email, expiry_ms, user, subscription)
        client_id = self._client_identifier(protocol, existing_client, email, fallback=key.external_id or email)
        if not client_id:
            raise RuntimeError('Не удалось определить идентификатор клиента для обновления в 3x-ui.')

        payload = {
            'id': resolved_inbound_id,
            'settings': json.dumps({'clients': [client]}, ensure_ascii=False),
        }
        await self._request('POST', f'/inbounds/updateClient/{quote(str(client_id), safe="")}', json=payload)
        access_url = self._build_connection_string(inbound, client, email)
        external_id = self._client_identifier(protocol, client, email, fallback=secret)
        return XUIProvisionResult(external_id=external_id, access_url=access_url, email=email)

    def _find_client(self, inbound: dict, protocol: str, external_id: str | None, email: str, *, raise_if_missing: bool = True) -> dict | None:
        inbound_settings = self._json_field(inbound.get('settings'))
        clients = inbound_settings.get('clients') or []
        for client in clients:
            candidate_email = str(client.get('email') or '').strip()
            candidate_ids = {
                str(value).strip()
                for value in (client.get('id'), client.get('password'), candidate_email)
                if value not in (None, '')
            }
            if external_id and str(external_id).strip() in candidate_ids:
                return dict(client)
            if email and candidate_email == email:
                return dict(client)
        if raise_if_missing:
            raise RuntimeError(f'Клиент для ключа {external_id or email} не найден в inbound {self.server.inbound_id}.')
        return None

    def _client_secret(self, protocol: str, client: dict, fallback: str | None = None) -> str:
        if protocol in {'trojan', 'shadowsocks', 'http', 'socks'}:
            value = client.get('password')
        else:
            value = client.get('id')
        return str(value or fallback or '')

    def _client_identifier(self, protocol: str, client: dict, email: str, fallback: str | None = None) -> str:
        if protocol in {'shadowsocks', 'http', 'socks'}:
            value = client.get('email') or email or fallback or ''
        elif protocol == 'trojan':
            value = client.get('password') or fallback or email or ''
        else:
            value = client.get('id') or fallback or email or ''
        return str(value).strip()

    def _subscription_email(self, user: User, subscription: Subscription) -> str:
        return f'tg{user.telegram_id}-sub{subscription.id}'

    def _describe_exception(self, exc: Exception) -> str:
        message = str(exc).strip()
        return message or repr(exc)

    def _build_client(
        self,
        protocol: str,
        inbound: dict,
        secret: str,
        email: str,
        expiry_ms: int,
        user: User,
        subscription: Subscription,
    ) -> dict:
        inbound_settings = self._json_field(inbound.get('settings'))
        template_client = ((inbound_settings.get('clients') or [])[:1] or [{}])[0]

        client = {
            'email': email,
            'enable': True,
            'expiryTime': expiry_ms,
            'totalGB': int(template_client.get('totalGB', 0) or 0),
            'limitIp': int(template_client.get('limitIp', 0) or 0),
            'subId': str(subscription.id),
            'tgId': str(user.telegram_id),
            'reset': int(template_client.get('reset', 0) or 0),
        }

        if protocol in {'trojan', 'shadowsocks', 'http', 'socks'}:
            client['password'] = secret
        else:
            client['id'] = secret

        for field in ('flow', 'method', 'security', 'alterId', 'level', 'encryption'):
            value = template_client.get(field)
            if value not in (None, ''):
                client[field] = value

        if protocol == 'vmess':
            client.setdefault('alterId', 0)
            client.setdefault('security', 'auto')
        if protocol == 'vless' and not client.get('flow'):
            client.pop('flow', None)
        if protocol == 'shadowsocks' and 'method' not in client:
            client['method'] = inbound_settings.get('method', 'aes-256-gcm')
        return client

    def _build_connection_string(self, inbound: dict, client: dict, email: str) -> str:
        protocol = (inbound.get('protocol') or 'vless').lower()
        port = inbound.get('port', 443)
        remark = inbound.get('remark') or self.server.name
        title = quote(f'{remark}-{email}')
        host = self._host_from_base_url()

        stream_settings = self._json_field(inbound.get('streamSettings'))
        network = stream_settings.get('network', 'tcp')
        security = stream_settings.get('security', 'none')
        transport_settings = self._json_field(stream_settings.get('settings'))
        tls_settings = self._json_field(stream_settings.get('tlsSettings'))
        reality_settings = self._json_field(stream_settings.get('realitySettings'))
        reality_payload = self._json_field(reality_settings.get('settings'))

        if protocol == 'vmess':
            vmess_payload = {
                'v': '2',
                'ps': f'{remark}-{email}',
                'add': host,
                'port': str(port),
                'id': client.get('id', ''),
                'aid': str(client.get('alterId', 0)),
                'scy': client.get('security', 'auto'),
                'net': network,
                'type': 'none',
                'host': (transport_settings.get('headers') or {}).get('Host', ''),
                'path': transport_settings.get('path', ''),
                'tls': 'tls' if security in {'tls', 'reality'} else '',
                'sni': reality_payload.get('serverName') or tls_settings.get('serverName', ''),
            }
            encoded = json.dumps(vmess_payload, ensure_ascii=False).encode('utf-8')
            return 'vmess://' + base64.b64encode(encoded).decode('utf-8')

        query_parts = [f'type={network}']
        if security and security != 'none':
            query_parts.append(f'security={security}')
        if client.get('flow'):
            query_parts.append(f"flow={quote(str(client['flow']))}")

        server_name = reality_payload.get('serverName') or tls_settings.get('serverName')
        if not server_name:
            server_names = reality_settings.get('serverNames') or []
            if server_names:
                server_name = server_names[0]
        if server_name:
            query_parts.append(f'sni={quote(str(server_name))}')

        fingerprint = reality_payload.get('fingerprint')
        if fingerprint:
            query_parts.append(f'fp={quote(str(fingerprint))}')
        public_key = reality_payload.get('publicKey')
        if public_key:
            query_parts.append(f'pbk={quote(str(public_key))}')
        short_ids = reality_settings.get('shortIds') or []
        if short_ids and short_ids[0]:
            query_parts.append(f'sid={quote(str(short_ids[0]))}')
        spider_x = reality_payload.get('spiderX')
        if spider_x:
            query_parts.append(f"spx={quote(str(spider_x), safe='/-._~')}")

        if network == 'ws':
            headers = transport_settings.get('headers') or {}
            if headers.get('Host'):
                query_parts.append(f"host={quote(str(headers['Host']))}")
            if transport_settings.get('path'):
                query_parts.append(f"path={quote(str(transport_settings['path']), safe='/-._~')}")
        elif network == 'grpc' and transport_settings.get('serviceName'):
            query_parts.append(f"serviceName={quote(str(transport_settings['serviceName']))}")

        query = '&'.join(query_parts)
        if protocol == 'trojan':
            secret = client.get('password') or client.get('id') or ''
            return f'trojan://{secret}@{host}:{port}?{query}#{title}'

        secret = client.get('id') or client.get('password') or ''
        return f'vless://{secret}@{host}:{port}?{query}#{title}'

    def _json_field(self, value) -> dict:
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                return {}
        return {}

    def _host_from_base_url(self) -> str:
        parsed = urlsplit(self.base_url)
        return parsed.hostname or self.base_url.split('://', maxsplit=1)[-1].split('/', maxsplit=1)[0]
