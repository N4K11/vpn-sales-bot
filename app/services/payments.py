from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

import aiohttp

from app.config import settings
from app.db.models import Payment, Tariff, User
from app.services.store import Store

logger = logging.getLogger(__name__)


class PaymentGatewayError(RuntimeError):
    pass


@dataclass
class ExternalInvoice:
    provider_payment_id: str
    payment_url: str


class PaymentService:
    def __init__(self, store: Store) -> None:
        self.store = store

    async def create_invoice(self, payment_id: int) -> ExternalInvoice:
        payment, user, tariff = await self.store.get_payment_bundle(payment_id)
        if not payment or not user or not tariff:
            raise PaymentGatewayError('Платеж не найден.')

        config = await self.store.get_payment_settings_snapshot()
        if payment.method == 'yookassa':
            invoice = await self._create_yookassa_invoice(payment, user, tariff, config)
        elif payment.method == 'crypto':
            invoice = await self._create_crypto_invoice(payment, user, tariff, config)
        else:
            raise PaymentGatewayError('Для этого метода не требуется внешняя ссылка.')

        await self.store.update_payment_provider(payment.id, invoice.provider_payment_id, invoice.payment_url)
        return invoice

    async def poll_pending(self) -> list[int]:
        payments = await self.store.list_pending_external_payments()
        paid_ids: list[int] = []
        config = await self.store.get_payment_settings_snapshot()

        for payment in payments:
            try:
                if payment.method == 'yookassa':
                    is_paid = await self._check_yookassa_payment(payment, config)
                elif payment.method == 'crypto':
                    is_paid = await self._check_crypto_payment(payment, config)
                else:
                    is_paid = False
            except Exception as exc:
                logger.warning('Payment poll failed for %s: %s', payment.id, exc)
                continue

            if is_paid:
                paid_ids.append(payment.id)

        return paid_ids

    async def _create_yookassa_invoice(self, payment: Payment, user: User, tariff: Tariff, config: dict) -> ExternalInvoice:
        shop_id = (config.get('yookassa_shop_id') or '').strip()
        secret_key = (config.get('yookassa_secret_key') or '').strip()
        return_url = (config.get('yookassa_return_url') or settings.yookassa_return_url).strip() or settings.yookassa_return_url
        if not shop_id or not secret_key:
            raise PaymentGatewayError('YooKassa не настроена.')

        auth = aiohttp.BasicAuth(shop_id, secret_key)
        headers = {'Idempotence-Key': str(uuid4())}
        payload = {
            'amount': {'value': self._money_value(payment.amount), 'currency': 'RUB'},
            'capture': True,
            'confirmation': {'type': 'redirect', 'return_url': return_url},
            'description': f'VPN тариф {tariff.name}',
            'save_payment_method': False,
            'metadata': {
                'payment_id': str(payment.id),
                'telegram_id': str(user.telegram_id),
                'tariff_id': str(tariff.id),
            },
        }

        async with aiohttp.ClientSession(auth=auth, headers=headers) as session:
            async with session.post('https://api.yookassa.ru/v3/payments', json=payload) as resp:
                data = await resp.json(content_type=None)
                if resp.status not in (200, 201):
                    raise PaymentGatewayError(self._yookassa_error_message(data, resp.status))

        confirmation = data.get('confirmation') or {}
        payment_url = confirmation.get('confirmation_url', '')
        if not payment_url:
            raise PaymentGatewayError('YooKassa вернула платеж без confirmation_url.')
        return ExternalInvoice(provider_payment_id=str(data['id']), payment_url=payment_url)

    async def _check_yookassa_payment(self, payment: Payment, config: dict) -> bool:
        shop_id = (config.get('yookassa_shop_id') or '').strip()
        secret_key = (config.get('yookassa_secret_key') or '').strip()
        if not shop_id or not secret_key or not payment.provider_payment_id:
            return False

        auth = aiohttp.BasicAuth(shop_id, secret_key)
        async with aiohttp.ClientSession(auth=auth) as session:
            async with session.get(f'https://api.yookassa.ru/v3/payments/{payment.provider_payment_id}') as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    logger.warning('YooKassa status check failed for %s: %s', payment.id, self._yookassa_error_message(data, resp.status))
                    return False
        return data.get('status') == 'succeeded'

    async def _create_crypto_invoice(self, payment: Payment, user: User, tariff: Tariff, config: dict) -> ExternalInvoice:
        token = (config.get('crypto_pay_token') or '').strip()
        use_testnet = bool(config.get('crypto_pay_use_testnet'))
        assets = config.get('crypto_pay_assets') or settings.crypto_assets
        if not token:
            raise PaymentGatewayError('Crypto Pay не настроен.')

        endpoint = 'https://testnet-pay.crypt.bot/api' if use_testnet else 'https://pay.crypt.bot/api'
        headers = {'Crypto-Pay-API-Token': token}
        payload = {
            'currency_type': 'fiat',
            'fiat': 'RUB',
            'amount': self._money_value(payment.amount),
            'accepted_assets': ','.join(assets),
            'description': f'VPN тариф {tariff.name}',
            'payload': str(payment.id),
            'hidden_message': f'Спасибо за оплату. Пользователь {user.telegram_id}',
        }

        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(f'{endpoint}/createInvoice', json=payload) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200 or not data.get('ok'):
                    error = data.get('error') or {}
                    raise PaymentGatewayError(error.get('name', 'Не удалось создать crypto invoice.'))

        invoice = data['result']
        return ExternalInvoice(provider_payment_id=str(invoice['invoice_id']), payment_url=invoice['pay_url'])

    async def _check_crypto_payment(self, payment: Payment, config: dict) -> bool:
        token = (config.get('crypto_pay_token') or '').strip()
        use_testnet = bool(config.get('crypto_pay_use_testnet'))
        if not token or not payment.provider_payment_id:
            return False

        endpoint = 'https://testnet-pay.crypt.bot/api' if use_testnet else 'https://pay.crypt.bot/api'
        headers = {'Crypto-Pay-API-Token': token}
        params = {'invoice_ids': payment.provider_payment_id}

        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(f'{endpoint}/getInvoices', params=params) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200 or not data.get('ok'):
                    return False

        invoices = data['result'].get('items', [])
        if not invoices:
            return False
        return invoices[0].get('status') == 'paid'

    def _money_value(self, amount: Decimal) -> str:
        return str(Decimal(amount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

    def _yookassa_error_message(self, data: dict, status_code: int) -> str:
        description = data.get('description') or data.get('message') or ''
        error_obj = data.get('error') or {}
        error_id = data.get('id') or error_obj.get('code') or ''
        pieces = [piece for piece in [description, error_id] if piece]
        if pieces:
            return ' / '.join(pieces)
        return f'YooKassa вернула HTTP {status_code}.'
