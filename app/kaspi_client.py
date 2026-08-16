"""Kaspi POS Automation client.

Communicates with kaspi-pos-automation Node.js service
to create dynamic QR codes and check payment status.
"""

import httpx
import logging
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class QrCreateResult:
    operation_id: str
    qr_token: str
    qr_original_token: str
    amount: int
    expire_date: str | None = None


@dataclass
class QrStatusResult:
    operation_id: str
    status: str
    amount: int | None
    receipt_url: str | None


def _session_headers() -> dict[str, str]:
    """Return session headers required by kaspi-pos-automation."""
    return {
        "X-Token-SN": settings.kaspi_pos_token_sn,
        "X-Vtoken-Secret": settings.kaspi_pos_vtoken_secret,
        "X-Profile-Id": settings.kaspi_pos_profile_id,
    }


async def create_qr(amount: int) -> QrCreateResult:
    """Create a dynamic QR code via kaspi-pos-automation.

    Calls POST /api/qr/create with the given amount.
    Returns QrCreateResult with operation_id and QR tokens.
    """
    headers = _session_headers()
    headers["Content-Type"] = "application/json"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{settings.kaspi_pos_url}/api/qr/create",
            json={"amount": amount},
            headers=headers,
        )

    if resp.status_code != 200:
        raise ValueError(
            f"kaspi-pos /api/qr/create failed: {resp.status_code} {resp.text}"
        )

    data = resp.json()

    # kaspi-pos-automation returns Kaspi API response format
    inner = data.get("Data", data)

    return QrCreateResult(
        operation_id=str(inner.get("QrOperationId", inner.get("Id", ""))),
        qr_token=inner.get("QrToken", ""),
        qr_original_token=inner.get("QrOriginalToken", ""),
        amount=amount,
        expire_date=inner.get("ExpireDate"),
    )


async def check_qr_status(operation_id: str) -> QrStatusResult:
    """Check QR payment status via kaspi-pos-automation.

    Calls GET /api/qr/status?qrOperationId={operation_id}.
    """
    headers = _session_headers()

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{settings.kaspi_pos_url}/api/qr/status",
            params={"qrOperationId": operation_id},
            headers=headers,
        )

    if resp.status_code != 200:
        raise ValueError(
            f"kaspi-pos /api/qr/status failed: {resp.status_code} {resp.text}"
        )

    data = resp.json()
    inner = data.get("Data", data)

    return QrStatusResult(
        operation_id=operation_id,
        status=inner.get("Status", "Unknown"),
        amount=inner.get("Amount"),
        receipt_url=inner.get("ReceiptUrl"),
    )
