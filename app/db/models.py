from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    admin_role: Mapped[str] = mapped_column(String(30), default="user", nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    invite_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    referrer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    trial_claimed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    referrer: Mapped[Optional["User"]] = relationship(remote_side=[id], back_populates="referrals")
    referrals: Mapped[List["User"]] = relationship(back_populates="referrer")
    subscriptions: Mapped[List["Subscription"]] = relationship(back_populates="user")
    payments: Mapped[List["Payment"]] = relationship(back_populates="user")
    balance_operations: Mapped[List["BalanceOperation"]] = relationship(back_populates="user")


class Tariff(TimestampMixin, Base):
    __tablename__ = "tariffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    price_rub: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    price_stars: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    subscriptions: Mapped[List["Subscription"]] = relationship(back_populates="tariff")
    payments: Mapped[List["Payment"]] = relationship(back_populates="tariff")


class Server(TimestampMixin, Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    inbound_id: Mapped[int] = mapped_column(Integer, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_trial_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    health_status: Mapped[str] = mapped_column(String(50), default="unknown", nullable=False)
    cpu_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ram_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    subscriptions: Mapped[List["Subscription"]] = relationship(back_populates="server")
    keys: Mapped[List["VpnKey"]] = relationship(back_populates="server")


class Subscription(TimestampMixin, Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    tariff_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tariffs.id"))
    server_id: Mapped[Optional[int]] = mapped_column(ForeignKey("servers.id"))
    status: Mapped[str] = mapped_column(String(50), default="active", nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_trial: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_payment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("payments.id"))
    expiry_notice_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="subscriptions")
    tariff: Mapped[Optional["Tariff"]] = relationship(back_populates="subscriptions")
    server: Mapped[Optional["Server"]] = relationship(back_populates="subscriptions")
    keys: Mapped[List["VpnKey"]] = relationship(back_populates="subscription")
    source_payment: Mapped[Optional["Payment"]] = relationship(back_populates="subscription")


class VpnKey(TimestampMixin, Base):
    __tablename__ = "vpn_keys"
    __table_args__ = (UniqueConstraint("external_id", name="uq_vpn_key_external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id"), index=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    access_url: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[Optional[str]] = mapped_column(String(255))
    used_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    subscription: Mapped["Subscription"] = relationship(back_populates="keys")
    server: Mapped["Server"] = relationship(back_populates="keys")


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    tariff_id: Mapped[int] = mapped_column(ForeignKey("tariffs.id"), index=True)
    method: Mapped[str] = mapped_column(String(50), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    provider_payment_id: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    provider_url: Mapped[str] = mapped_column(Text, default="", nullable=False)
    payload: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    activation_notice_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reminder_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="payments")
    tariff: Mapped["Tariff"] = relationship(back_populates="payments")
    subscription: Mapped[Optional["Subscription"]] = relationship(back_populates="source_payment", uselist=False)
    operations: Mapped[List["BalanceOperation"]] = relationship(back_populates="payment")


class BalanceOperation(TimestampMixin, Base):
    __tablename__ = "balance_operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    payment_id: Mapped[Optional[int]] = mapped_column(ForeignKey("payments.id"))
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    user: Mapped["User"] = relationship(back_populates="balance_operations")
    payment: Mapped[Optional["Payment"]] = relationship(back_populates="operations")


class AdminActionLog(TimestampMixin, Base):
    __tablename__ = "admin_action_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    target_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True)
    target_server_id: Mapped[Optional[int]] = mapped_column(ForeignKey("servers.id"), index=True)
    details_json: Mapped[str] = mapped_column(Text, default="", nullable=False)

    actor: Mapped[Optional["User"]] = relationship(foreign_keys=[actor_user_id])
    target_user: Mapped[Optional["User"]] = relationship(foreign_keys=[target_user_id])
    target_server: Mapped[Optional["Server"]] = relationship(foreign_keys=[target_server_id])


class ProvisioningFailureLog(TimestampMixin, Base):
    __tablename__ = "provisioning_failure_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    server_id: Mapped[Optional[int]] = mapped_column(ForeignKey("servers.id"), index=True)
    subscription_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    user_telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True)
    server_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    server: Mapped[Optional["Server"]] = relationship(foreign_keys=[server_id])
    subscription: Mapped[Optional["Subscription"]] = relationship(foreign_keys=[subscription_id])
class PromoCode(TimestampMixin, Base):
    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    discount_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bonus_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    used_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    first_purchase_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    extend_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    target_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True)

    target_user: Mapped[Optional["User"]] = relationship(foreign_keys=[target_user_id])


class FeatureToggle(Base):
    __tablename__ = "feature_toggles"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="", nullable=False)


class ContentPage(Base):
    __tablename__ = "content_pages"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
