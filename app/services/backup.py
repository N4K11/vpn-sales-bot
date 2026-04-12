from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zipfile import ZIP_DEFLATED, ZipFile

from aiogram import Bot
from aiogram.types import FSInputFile

from app.config import settings

logger = logging.getLogger(__name__)


class BackupService:
    def __init__(self) -> None:
        settings.ensure_directories()

    async def create_backup(self) -> Path:
        timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        archive_path = settings.backup_dir / f"backup-{timestamp}.zip"
        manifest: dict[str, str | list[str]] = {
            "created_at_utc": datetime.utcnow().isoformat(),
            "database_backend": settings.database_url.split(":", maxsplit=1)[0],
            "included": [],
        }

        with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
            await self._dump_database_if_possible(archive, manifest)
            await self._add_directory(Path(settings.log_dir), archive, "logs", manifest)
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

        return archive_path

    async def send_backup_to_admins(self, bot: Bot) -> Path:
        archive_path = await self.create_backup()
        for admin_id in settings.admin_ids:
            try:
                await bot.send_document(admin_id, FSInputFile(archive_path), caption="Ежедневный backup бота")
            except Exception as exc:
                logger.warning("Failed to send backup to admin %s: %s", admin_id, exc)
        return archive_path

    async def cleanup_old_files(self, backup_retention_days: int = 14, log_retention_days: int = 14) -> dict[str, int]:
        stats = {
            'deleted_backups': 0,
            'deleted_logs': 0,
        }
        backup_before = datetime.utcnow().timestamp() - backup_retention_days * 86400
        log_before = datetime.utcnow().timestamp() - log_retention_days * 86400

        for file_path in Path(settings.backup_dir).glob('**/*'):
            if file_path.is_dir():
                continue
            try:
                if file_path.stat().st_mtime < backup_before:
                    file_path.unlink(missing_ok=True)
                    stats['deleted_backups'] += 1
            except Exception as exc:
                logger.warning('Failed to delete old backup file %s: %s', file_path, exc)

        for file_path in Path(settings.log_dir).glob('**/*'):
            if file_path.is_dir():
                continue
            try:
                if file_path.stat().st_mtime < log_before:
                    file_path.unlink(missing_ok=True)
                    stats['deleted_logs'] += 1
            except Exception as exc:
                logger.warning('Failed to delete old log file %s: %s', file_path, exc)

        return stats
    async def _dump_database_if_possible(self, archive: ZipFile, manifest: dict[str, str | list[str]]) -> None:
        if settings.database_url.startswith("sqlite"):
            db_path = Path(settings.database_url.replace("sqlite+aiosqlite:///", "", 1))
            if db_path.exists():
                archive.write(db_path, arcname=f"database/{db_path.name}")
                manifest["included"].append(f"database/{db_path.name}")
            return

        if settings.database_url.startswith("postgresql"):
            parsed = urlparse(settings.database_url.replace("+asyncpg", "", 1))
            dump_path = settings.backup_dir / "postgres.dump"
            env = os.environ.copy()
            env["PGPASSWORD"] = parsed.password or ""
            process = await asyncio.create_subprocess_exec(
                "pg_dump",
                "-h",
                parsed.hostname or "postgres",
                "-p",
                str(parsed.port or 5432),
                "-U",
                parsed.username or "postgres",
                "-f",
                str(dump_path),
                parsed.path.lstrip("/"),
                env=env,
            )
            return_code = await process.wait()
            if return_code == 0 and dump_path.exists():
                archive.write(dump_path, arcname="database/postgres.dump")
                manifest["included"].append("database/postgres.dump")
                dump_path.unlink(missing_ok=True)
            else:
                manifest["postgres_dump"] = "pg_dump failed or is unavailable"

    async def _add_directory(
        self,
        directory: Path,
        archive: ZipFile,
        target_prefix: str,
        manifest: dict[str, str | list[str]],
    ) -> None:
        if not directory.exists():
            return
        for file_path in directory.glob("**/*"):
            if file_path.is_dir():
                continue
            archive.write(file_path, arcname=f"{target_prefix}/{file_path.name}")
            manifest["included"].append(f"{target_prefix}/{file_path.name}")


