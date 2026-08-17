import hmac
import hashlib
import logging
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from pydantic import BaseModel

from app.config import settings
from app.kaspi_client import create_qr, check_qr_status
from app.database import SessionLocal
from app.models import Payment

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])


def get_current_user(authorization: str = Header(...)) -> dict:
    """Extract user info from JWT Bearer token."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    from jose import jwt, JWTError

    token = authorization.split(" ", 1)[1]

    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    iin = payload.get("sub")
    name = payload.get("name", "")

    if not iin:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    return {"iin": iin, "name": name}


# --- In-memory payment store (replace with DB in production) ---

_payments: dict[str, dict] = {}


class CreateQrRequest(BaseModel):
    amount: int | None = None


class CreateQrResponse(BaseModel):
    operation_id: str
    qr_token: str
    qr_original_token: str
    amount: int
    expire_date: str | None = None
    status: str = "created"


class PaymentStatusResponse(BaseModel):
    operation_id: str
    status: str
    amount: int | None
    receipt_url: str | None


class WebhookPayload(BaseModel):
    event: str
    paymentId: str
    type: str
    status: str
    statusDesc: str | None = None
    amount: int | None = None
    qrToken: str | None = None
    receiptUrl: str | None = None
    orderNumber: str | None = None
    timestamp: str | None = None


@router.post("/create-qr", response_model=CreateQrResponse)
async def create_payment_qr(
    req: CreateQrRequest,
    user: dict = Depends(get_current_user),
):
    """Create a dynamic Kaspi QR code for payment.

    Calls kaspi-pos-automation to generate a fresh QR token.
    The QR is valid for ~5 minutes.
    """
    amount = req.amount or settings.service_fee_amount

    if not settings.kaspi_pos_token_sn:
        raise HTTPException(
            status_code=503,
            detail="Kaspi POS session not configured. Authenticate first at /api/v1/payments/auth-status.",
        )

    try:
        result = await create_qr(amount)
    except Exception as e:
        logger.error(f"Failed to create QR: {e}")
        raise HTTPException(status_code=502, detail=f"Kaspi POS error: {e}")

    # Store payment in memory (for quick lookup)
    _payments[result.operation_id] = {
        "operation_id": result.operation_id,
        "iin": user["iin"],
        "amount": amount,
        "status": "created",
        "receipt_url": None,
    }

    # Store payment in database
    db = SessionLocal()
    try:
        payment = Payment(
            operation_id=result.operation_id,
            amount=amount,
            status="created",
            kaspi_info={
                "qr_token": result.qr_token,
                "qr_original_token": result.qr_original_token,
                "expire_date": result.expire_date,
            },
        )
        db.add(payment)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to save payment to DB: {e}")
    finally:
        db.close()

    return CreateQrResponse(
        operation_id=result.operation_id,
        qr_token=result.qr_token,
        qr_original_token=result.qr_original_token,
        amount=amount,
        expire_date=result.expire_date,
    )


@router.get("/{operation_id}/status", response_model=PaymentStatusResponse)
async def get_payment_status(
    operation_id: str,
    user: dict = Depends(get_current_user),
):
    """Check payment status for a QR operation.

    Polls kaspi-pos-automation for the latest status.
    """
    # Check local store first for webhook-updated status
    local = _payments.get(operation_id)
    if local and local["status"] == "paid":
        return PaymentStatusResponse(
            operation_id=operation_id,
            status="paid",
            amount=local["amount"],
            receipt_url=local.get("receipt_url"),
        )

    try:
        result = await check_qr_status(operation_id)
    except Exception as e:
        logger.error(f"Failed to check QR status: {e}")
        raise HTTPException(status_code=502, detail=f"Kaspi POS error: {e}")

    # Map Kaspi statuses
    status_map = {
        "Processed": "paid",
        "Created": "pending",
        "QrTokenDiscarded": "expired",
        "Expired": "expired",
        "CancelledByUser": "cancelled",
        "Rejected": "failed",
        "Error": "failed",
    }
    mapped = status_map.get(result.status, result.status.lower())

    # Update local store
    if operation_id in _payments:
        _payments[operation_id]["status"] = mapped
        if result.receipt_url:
            _payments[operation_id]["receipt_url"] = result.receipt_url

    return PaymentStatusResponse(
        operation_id=operation_id,
        status=mapped,
        amount=result.amount,
        receipt_url=result.receipt_url,
    )


@router.post("/webhook")
async def kaspi_webhook(
    request: Request,
    x_webhook_signature: str = Header(default=""),
):
    """Receive webhook from kaspi-pos-automation.

    Verifies HMAC signature and updates payment status.
    """
    body = await request.body()

    # Verify HMAC signature
    if settings.kaspi_webhook_secret:
        expected = (
            "sha256="
            + hmac.new(
                settings.kaspi_webhook_secret.encode(),
                body,
                hashlib.sha256,
            ).hexdigest()
        )
        if not hmac.compare_digest(x_webhook_signature, expected):
            logger.warning("Webhook signature mismatch")
            raise HTTPException(status_code=401, detail="Invalid signature")

    import json

    payload = json.loads(body)
    logger.info(f"Kaspi webhook: {payload.get('event')} for {payload.get('paymentId')}")

    payment_id = str(payload.get("paymentId", ""))
    event = payload.get("event", "")

    if payment_id and payment_id in _payments:
        if event == "payment.success":
            _payments[payment_id]["status"] = "paid"
            _payments[payment_id]["receipt_url"] = payload.get("receiptUrl")
        elif event == "payment.failed":
            _payments[payment_id]["status"] = "failed"
        elif event == "payment.expired":
            _payments[payment_id]["status"] = "expired"

    # Update database
    status_map = {
        "payment.success": "paid",
        "payment.failed": "failed",
        "payment.expired": "expired",
        "payment.lost": "lost",
    }
    db_status = status_map.get(event)
    if db_status:
        db = SessionLocal()
        try:
            db_payment = db.query(Payment).filter(Payment.operation_id == payment_id).first()
            if db_payment:
                db_payment.status = db_status
                if payload.get("receiptUrl"):
                    kaspi_info = db_payment.kaspi_info or {}
                    kaspi_info["receipt_url"] = payload.get("receiptUrl")
                    kaspi_info["webhook_event"] = event
                    kaspi_info["webhook_payload"] = payload
                    db_payment.kaspi_info = kaspi_info
                db.commit()
                logger.info(f"Payment {payment_id} updated to {db_status} in DB")
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to update payment in DB: {e}")
        finally:
            db.close()

    return {"received": True}


@router.get("/auth-status")
async def auth_status():
    """Check if kaspi-pos-automation session is configured."""
    return {
        "configured": bool(settings.kaspi_pos_token_sn),
        "kaspi_pos_url": settings.kaspi_pos_url,
    }
