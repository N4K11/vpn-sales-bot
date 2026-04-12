from __future__ import annotations

import io


class QRCodeUnavailableError(RuntimeError):
    """Raised when QR generation dependencies are not installed."""


def build_qr_png_bytes(value: str) -> bytes:
    payload = (value or '').strip()
    if not payload:
        raise ValueError('Пустое значение для QR-кода.')
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M
    except ImportError as exc:  # pragma: no cover - depends on local env
        raise QRCodeUnavailableError(
            'QR-коды пока недоступны: установите зависимости проекта заново, чтобы подтянуть qrcode и Pillow.'
        ) from exc

    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=10,
        border=3,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color='black', back_color='white')
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()
