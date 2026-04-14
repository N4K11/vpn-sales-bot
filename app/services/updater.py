from __future__ import annotations

import importlib.metadata
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import aiohttp

from app.config import settings


@dataclass(slots=True)
class UpdateStatus:
    current_version: str
    current_revision: str
    latest_version: str
    latest_revision: str
    update_available: bool
    trigger_configured: bool
    trigger_url: str
    image_name: str
    repository: str
    check_error: str = ''


class UpdateService:
    async def get_status(self) -> UpdateStatus:
        try:
            current_version = importlib.metadata.version('vpn-sales-bot')
        except importlib.metadata.PackageNotFoundError:
            current_version = 'dev'

        current_revision = (settings.app_build_sha or '').strip()
        repository = self._resolve_repository()
        latest_version = current_version
        latest_revision = current_revision
        check_error = ''
        update_available = False

        if repository:
            try:
                latest_version, latest_revision = await self._fetch_remote_release_state(repository, settings.github_default_branch)
            except Exception as exc:
                check_error = str(exc).strip() or repr(exc)
            else:
                if current_revision and latest_revision and current_revision != latest_revision:
                    update_available = True
                elif latest_version and current_version and latest_version != current_version:
                    update_available = True

        return UpdateStatus(
            current_version=current_version,
            current_revision=current_revision,
            latest_version=latest_version,
            latest_revision=latest_revision,
            update_available=update_available,
            trigger_configured=bool(settings.update_trigger_url and settings.update_trigger_token),
            trigger_url=settings.update_trigger_url,
            image_name=settings.bot_image,
            repository=repository,
            check_error=check_error,
        )

    async def trigger_update(self) -> str:
        if not settings.update_trigger_url or not settings.update_trigger_token:
            raise RuntimeError('Механизм обновления не настроен на сервере.')

        headers = {
            'Authorization': f'Bearer {settings.update_trigger_token}',
        }
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(settings.update_trigger_url) as response:
                body = (await response.text()).strip()
                if response.status >= 400:
                    raise RuntimeError(body or f'HTTP {response.status}')
        return body or 'Запрос на обновление отправлен.'

    def _resolve_repository(self) -> str:
        explicit = (settings.github_repository or '').strip().strip('/')
        if explicit:
            return explicit

        image = (settings.bot_image or '').strip()
        if not image:
            return ''
        image_without_tag = image.split('@', maxsplit=1)[0].split(':', maxsplit=1)[0]
        parsed = urlsplit(f'dummy://{image_without_tag}')
        path = parsed.path.lstrip('/')
        if path.startswith('ghcr.io/'):
            path = path.split('/', maxsplit=1)[1]
        parts = [chunk for chunk in path.split('/') if chunk]
        if len(parts) >= 2:
            return f'{parts[0]}/{parts[1]}'
        return ''

    async def _fetch_remote_release_state(self, repository: str, branch: str) -> tuple[str, str]:
        headers = {
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'vpn-sales-bot-updater',
        }
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            sha = await self._fetch_remote_sha(session, repository, branch)
            version = await self._fetch_remote_version(session, repository, branch)
        return version, sha

    async def _fetch_remote_sha(self, session: aiohttp.ClientSession, repository: str, branch: str) -> str:
        url = f'https://api.github.com/repos/{repository}/commits/{branch}'
        async with session.get(url) as response:
            if response.status >= 400:
                body = (await response.text()).strip()
                raise RuntimeError(body or f'GitHub API HTTP {response.status}')
            payload = await response.json()
        return str(payload.get('sha') or '').strip()

    async def _fetch_remote_version(self, session: aiohttp.ClientSession, repository: str, branch: str) -> str:
        url = f'https://raw.githubusercontent.com/{repository}/{branch}/pyproject.toml'
        async with session.get(url) as response:
            if response.status >= 400:
                body = (await response.text()).strip()
                raise RuntimeError(body or f'raw.githubusercontent.com HTTP {response.status}')
            payload = await response.text()
        match = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"\s*$', payload)
        if not match:
            raise RuntimeError('Не удалось определить версию из pyproject.toml репозитория.')
        return match.group(1).strip()
