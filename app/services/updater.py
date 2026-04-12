from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass

import aiohttp

from app.config import settings


@dataclass(slots=True)
class UpdateStatus:
    current_version: str
    trigger_configured: bool
    trigger_url: str
    image_name: str


class UpdateService:
    def get_status(self) -> UpdateStatus:
        try:
            current_version = importlib.metadata.version('vpn-sales-bot')
        except importlib.metadata.PackageNotFoundError:
            current_version = 'dev'
        return UpdateStatus(
            current_version=current_version,
            trigger_configured=bool(settings.update_trigger_url and settings.update_trigger_token),
            trigger_url=settings.update_trigger_url,
            image_name=settings.bot_image,
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
