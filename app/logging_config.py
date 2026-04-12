from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from pythonjsonlogger import jsonlogger

from app.config import settings


def setup_logging() -> None:
    settings.ensure_directories()

    root_logger = logging.getLogger()
    root_logger.setLevel(settings.log_level.upper())

    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(settings.log_level.upper())
    console_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))

    file_handler = RotatingFileHandler(
        Path(settings.log_dir) / "bot.log",
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(settings.log_level.upper())
    file_handler.setFormatter(
        jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s %(pathname)s %(lineno)d")
    )

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

