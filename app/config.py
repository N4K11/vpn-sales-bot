from __future__ import annotations

from functools import cached_property
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    bot_token: str = Field(alias='BOT_TOKEN')
    admin_ids_raw: str = Field(default='', alias='ADMIN_IDS')
    bot_username: str = Field(default='vpn_bot', alias='BOT_USERNAME')
    bot_image: str = Field(default='', alias='BOT_IMAGE')
    app_build_sha: str = Field(default='', alias='APP_BUILD_SHA')
    github_repository: str = Field(default='', alias='GITHUB_REPOSITORY')
    github_default_branch: str = Field(default='main', alias='GITHUB_DEFAULT_BRANCH')

    database_url: str = Field(default='sqlite+aiosqlite:///./data/bot.db', alias='DATABASE_URL')
    redis_url: str = Field(default='', alias='REDIS_URL')
    proxy_url: str = Field(default='', alias='PROXY_URL')

    log_level: str = Field(default='INFO', alias='LOG_LEVEL')
    log_dir: Path = Field(default=Path('./logs'), alias='LOG_DIR')
    backup_dir: Path = Field(default=Path('./backups'), alias='BACKUP_DIR')
    backup_hour: int = Field(default=4, alias='BACKUP_HOUR')
    backup_minute: int = Field(default=0, alias='BACKUP_MINUTE')
    backup_timezone: str = Field(default='Europe/Saratov', alias='BACKUP_TIMEZONE')
    poll_payments_every_seconds: int = Field(default=60, alias='POLL_PAYMENTS_EVERY_SECONDS')

    referral_default_percent: int = Field(default=10, alias='REFERRAL_DEFAULT_PERCENT')
    trial_default_days: int = Field(default=3, alias='TRIAL_DEFAULT_DAYS')

    support_chat_url: str = Field(default='https://t.me/your_support', alias='SUPPORT_CHAT_URL')
    channel_url: str = Field(default='https://t.me/your_channel', alias='CHANNEL_URL')
    terms_url: str = Field(default='https://t.me/your_channel/1', alias='TERMS_URL')

    public_base_url: str = Field(default='', alias='PUBLIC_BASE_URL')
    subscription_host: str = Field(default='0.0.0.0', alias='SUBSCRIPTION_HOST')
    subscription_port: int = Field(default=8080, alias='SUBSCRIPTION_PORT')
    xui_subscription_port: int = Field(default=2096, alias='XUI_SUBSCRIPTION_PORT')
    xui_subscription_path: str = Field(default='/sub/', alias='XUI_SUBSCRIPTION_PATH')
    xui_subscription_scheme: str = Field(default='', alias='XUI_SUBSCRIPTION_SCHEME')

    update_trigger_url: str = Field(default='', alias='UPDATE_TRIGGER_URL')
    update_trigger_token: str = Field(default='', alias='UPDATE_TRIGGER_TOKEN')

    yookassa_shop_id: str = Field(default='', alias='YOOKASSA_SHOP_ID')
    yookassa_secret_key: str = Field(default='', alias='YOOKASSA_SECRET_KEY')
    yookassa_return_url: str = Field(default='https://example.com/payment-return', alias='YOOKASSA_RETURN_URL')

    crypto_pay_token: str = Field(default='', alias='CRYPTO_PAY_TOKEN')
    crypto_pay_use_testnet: bool = Field(default=False, alias='CRYPTO_PAY_USE_TESTNET')
    crypto_pay_assets_raw: str = Field(default='USDT,TON,BTC,ETH', alias='CRYPTO_PAY_ASSETS')

    xui_request_timeout: int = Field(default=20, alias='XUI_REQUEST_TIMEOUT')
    xui_verify_ssl: bool = Field(default=False, alias='XUI_VERIFY_SSL')

    @cached_property
    def admin_ids(self) -> list[int]:
        return [int(chunk.strip()) for chunk in self.admin_ids_raw.split(',') if chunk.strip()]

    @cached_property
    def crypto_assets(self) -> list[str]:
        return [chunk.strip().upper() for chunk in self.crypto_pay_assets_raw.split(',') if chunk.strip()]

    def ensure_directories(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        if self.database_url.startswith('sqlite'):
            Path('./data').mkdir(parents=True, exist_ok=True)


settings = Settings()

