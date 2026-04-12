from __future__ import annotations

from decimal import Decimal, InvalidOperation


def format_money(amount: Decimal | int | float | str, currency: str = "RUB") -> str:
    try:
        value = Decimal(str(amount))
    except InvalidOperation:
        value = Decimal("0")

    suffix = {
        "RUB": "₽",
        "XTR": "⭐",
        "USDT": "USDT",
    }.get(currency.upper(), currency.upper())

    if currency.upper() == "XTR":
        return f"{int(value)} {suffix}"
    return f"{value.quantize(Decimal('0.01'))} {suffix}"


def format_gb(used_bytes: int) -> str:
    if used_bytes <= 0:
        return "0.00 GB"
    return f"{used_bytes / (1024 ** 3):.2f} GB"
