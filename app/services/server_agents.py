from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote

import aiohttp


class ServerAgentError(RuntimeError):
    pass


@dataclass(slots=True)
class ServerAgentStatus:
    online: bool
    host: str = ''
    platform: str = ''
    uptime: str = ''
    load: str = ''
    memory_percent: int = 0
    disk_percent: int = 0
    services: dict[str, str] = field(default_factory=dict)
    version: str = ''
    error: str = ''


class ServerAgentClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = (base_url or '').strip().rstrip('/')
        self.token = (token or '').strip()

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    def _headers(self) -> dict[str, str]:
        return {
            'Authorization': f'Bearer {self.token}',
            'X-Agent-Token': self.token,
        }

    async def fetch_status(self) -> ServerAgentStatus:
        if not self.configured:
            raise ServerAgentError('Агент не настроен.')
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout, headers=self._headers()) as session:
            async with session.get(f'{self.base_url}/health') as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    raise ServerAgentError(str(data.get('error') or data.get('message') or f'HTTP {resp.status}'))
        return ServerAgentStatus(
            online=True,
            host=str(data.get('host') or ''),
            platform=str(data.get('platform') or ''),
            uptime=str(data.get('uptime') or ''),
            load=str(data.get('load') or ''),
            memory_percent=int(data.get('memory_percent') or 0),
            disk_percent=int(data.get('disk_percent') or 0),
            services=dict(data.get('services') or {}),
            version=str(data.get('version') or ''),
        )

    async def run_command(self, command: str) -> str:
        if not self.configured:
            raise ServerAgentError('Агент не настроен.')
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout, headers=self._headers()) as session:
            data = await self._post_run_command(session, command)
        stdout = str(data.get('stdout') or '').strip()
        stderr = str(data.get('stderr') or '').strip()
        exit_code = data.get('exit_code', 0)
        summary = str(data.get('message') or 'Команда выполнена').strip()
        parts = [summary, f'code={exit_code}']
        if stdout:
            parts.append(f'stdout: {stdout[:180]}')
        if stderr:
            parts.append(f'stderr: {stderr[:180]}')
        return ' | '.join(parts)

    async def _post_run_command(self, session: aiohttp.ClientSession, command: str) -> dict:
        async with session.post(f'{self.base_url}/run', json={'command': command}) as resp:
            data = await resp.json(content_type=None)
            if resp.status == 404:
                return await self._post_legacy_run_command(session, command)
            if resp.status != 200:
                raise ServerAgentError(str(data.get('error') or data.get('message') or f'HTTP {resp.status}'))
            return data

    async def _post_legacy_run_command(self, session: aiohttp.ClientSession, command: str) -> dict:
        encoded = quote(command, safe='')
        async with session.post(f'{self.base_url}/run/{encoded}') as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                raise ServerAgentError(str(data.get('error') or data.get('message') or f'HTTP {resp.status}'))
            return data
