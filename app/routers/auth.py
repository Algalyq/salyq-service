import asyncio
import base64
import logging
import uuid

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.security import generate_challenge, create_access_token, create_refresh_token
from app.kalkan_client import validate_cms

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class ChallengeResponse(BaseModel):
    challenge: str


class LoginRequest(BaseModel):
    cms: str


class UserInfo(BaseModel):
    iin: str
    fullName: str


class LoginResponse(BaseModel):
    accessToken: str
    refreshToken: str
    user: UserInfo


@router.post("/challenge", response_model=ChallengeResponse)
async def get_challenge():
    challenge = generate_challenge()
    return ChallengeResponse(challenge=challenge)


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    if not req.cms or len(req.cms) < 10:
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        result = await validate_cms(req.cms)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Validation service unavailable: {e}")

    if not result.valid:
        raise HTTPException(status_code=401, detail=result.error or "Signature validation failed")

    user = UserInfo(iin=result.subject.iin, fullName=result.subject.full_name)
    token_data = {"sub": user.iin, "name": user.fullName}

    return LoginResponse(
        accessToken=create_access_token(token_data),
        refreshToken=create_refresh_token(token_data),
        user=user,
    )


# --- SIGEX eGov QR auth ---

_qr_sessions: dict[str, dict] = {}


class QrCreateResponse(BaseModel):
    session_id: str
    qr_code: str
    expires_at: int


class QrStatusResponse(BaseModel):
    status: str
    accessToken: str | None = None
    refreshToken: str | None = None
    user: UserInfo | None = None


async def _send_data_to_sigex(data_url: str, challenge_b64: str):
    """Background task: send challenge data to SIGEX for eGov Mobile to pick up."""
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            await client.post(
                data_url,
                json={
                    "signMethod": "CMS_SIGN_ONLY",
                    "documentsToSign": [
                        {
                            "id": 1,
                            "document": {
                                "file": {
                                    "mime": "text/plain",
                                    "data": challenge_b64,
                                }
                            },
                        }
                    ],
                },
            )
    except httpx.TimeoutException:
        pass  # Expected — SIGEX holds connection until eGov Mobile reads data
    except Exception as e:
        logger.warning(f"SIGEX data send error: {e}")


@router.post("/qr/create", response_model=QrCreateResponse)
async def create_qr_session(background_tasks: BackgroundTasks):
    """Create a SIGEX eGov QR auth session.

    1. Generate a challenge.
    2. Register QR procedure with SIGEX (returns QR code + URLs).
    3. Return QR code immediately.
    4. Send challenge data to SIGEX in background (non-blocking).
    """
    challenge = generate_challenge()
    challenge_b64 = base64.b64encode(challenge.encode()).decode()

    # Step 1: Register QR procedure (fast, ~1-2s)
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(
                f"{settings.sigex_url}/api/egovQr",
                json={
                    "description": "Salyq Service authentication",
                    "whenDone": {"backUrl": ""},
                },
            )
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"SIGEX QR create error: {e}")
            raise HTTPException(status_code=502, detail=f"SIGEX error: {e}")

    qr_data = resp.json()
    qr_code = qr_data.get("qrCode", "")
    data_url = qr_data.get("dataURL", "")
    sign_url = qr_data.get("signURL", "")
    expires_at = qr_data.get("expireAt", 0)
    mobile_link = qr_data.get("eGovMobileLaunchLink", "")

    if not qr_code or not data_url or not sign_url:
        raise HTTPException(status_code=502, detail="Invalid SIGEX response")

    # Step 2: Send challenge data in background (non-blocking)
    background_tasks.add_task(_send_data_to_sigex, data_url, challenge_b64)

    session_id = str(uuid.uuid4())
    _qr_sessions[session_id] = {
        "challenge": challenge,
        "sign_url": sign_url,
        "mobile_link": mobile_link,
        "status": "pending",
    }

    return QrCreateResponse(
        session_id=session_id,
        qr_code=qr_code,
        expires_at=expires_at,
    )


@router.get("/qr/{session_id}/status", response_model=QrStatusResponse)
async def get_qr_status(session_id: str):
    """Poll SIGEX for QR auth signature, validate with kalkan, return JWT."""
    session = _qr_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session["status"] == "success":
        return QrStatusResponse(
            status="success",
            accessToken=session.get("access_token"),
            refreshToken=session.get("refresh_token"),
            user=UserInfo(
                iin=session.get("iin", ""),
                fullName=session.get("name", ""),
            ),
        )

    if session["status"] == "expired":
        return QrStatusResponse(status="expired")

    # Poll SIGEX for signature (long-poll, short timeout for our endpoint)
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(session["sign_url"])
            if resp.status_code == 200:
                data = resp.json()
            else:
                return QrStatusResponse(status="pending")
        except httpx.TimeoutException:
            return QrStatusResponse(status="pending")
        except Exception as e:
            logger.warning(f"SIGEX poll error: {e}")
            return QrStatusResponse(status="pending")

    # Check if user canceled
    if data.get("status") == "CANCELED":
        session["status"] = "expired"
        return QrStatusResponse(status="canceled")

    # Extract CMS signature from signed documents
    docs = data.get("documentsToSign", [])
    if not docs:
        return QrStatusResponse(status="pending")

    sig_doc = docs[0]
    sig_data = sig_doc.get("document", {}).get("file", {}).get("data", "")
    if not sig_data:
        return QrStatusResponse(status="pending")

    # Validate CMS with kalkan
    try:
        result = await validate_cms(sig_data)
    except Exception as e:
        logger.error(f"Kalkan validation error for QR: {e}")
        return QrStatusResponse(status="error")

    if not result.valid:
        session["status"] = "expired"
        return QrStatusResponse(status="failed")

    # Issue JWT
    user = UserInfo(iin=result.subject.iin, fullName=result.subject.full_name)
    token_data = {"sub": user.iin, "name": user.fullName}
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    session["status"] = "success"
    session["access_token"] = access_token
    session["refresh_token"] = refresh_token
    session["iin"] = user.iin
    session["name"] = user.fullName

    return QrStatusResponse(
        status="success",
        accessToken=access_token,
        refreshToken=refresh_token,
        user=user,
    )
