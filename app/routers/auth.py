import datetime
import logging
import uuid

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.security import generate_challenge, create_access_token, create_refresh_token
from app.kalkan_client import validate_cms
from app.database import SessionLocal
from app.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _save_user(iin: str, full_name: str, auth_method: str = "ncalayer") -> str:
    """Find or create user by IIN. Updates login count and last login time."""
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.iin == iin).first()
        if user:
            user.login_count += 1
            user.auth_method = auth_method
            user.last_login_at = datetime.datetime.utcnow()
            if full_name and full_name != user.full_name:
                user.full_name = full_name
            db.commit()
            logger.info(f"User login: iin={iin}, method={auth_method}, count={user.login_count}")
            return user.id
        else:
            user = User(iin=iin, full_name=full_name, auth_method=auth_method)
            db.add(user)
            db.commit()
            db.refresh(user)
            logger.info(f"New user created: iin={iin}, method={auth_method}, id={user.id}")
            return user.id
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to save user: {e}")
        return ""
    finally:
        db.close()


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

    _save_user(user.iin, user.fullName, auth_method="ncalayer")

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


async def _send_data_to_sigex(data_url: str, nonce_b64: str):
    """Background task: send nonce data to SIGEX for eGov Mobile to pick up."""
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
                                    "data": nonce_b64,
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

    1. Get a nonce from SIGEX auth API.
    2. Register QR procedure with SIGEX egovQr.
    3. Return QR code immediately.
    4. Send nonce as data to sign in background.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        # Step 1: Get nonce from SIGEX auth
        try:
            resp = await client.post(f"{settings.sigex_url}/api/auth", json={})
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"SIGEX auth nonce error: {e}")
            raise HTTPException(status_code=502, detail=f"SIGEX error: {e}")

        nonce_data = resp.json()
        nonce = nonce_data.get("nonce", "")
        if not nonce:
            raise HTTPException(status_code=502, detail="No nonce from SIGEX")

        # Step 2: Register QR procedure
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

    if not qr_code or not data_url or not sign_url:
        raise HTTPException(status_code=502, detail="Invalid SIGEX response")

    # Step 3: Send nonce as data to sign in background
    background_tasks.add_task(_send_data_to_sigex, data_url, nonce)

    session_id = str(uuid.uuid4())
    _qr_sessions[session_id] = {
        "nonce": nonce,
        "sign_url": sign_url,
        "status": "pending",
    }

    return QrCreateResponse(
        session_id=session_id,
        qr_code=qr_code,
        expires_at=expires_at,
    )


@router.get("/qr/{session_id}/status", response_model=QrStatusResponse)
async def get_qr_status(session_id: str):
    """Poll SIGEX for QR auth signature, verify via SIGEX auth API, return JWT."""
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

    # Poll SIGEX for signature (short timeout for our endpoint)
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

    logger.info(f"QR auth: got CMS signature, verifying via SIGEX auth API")

    # Verify via SIGEX auth API (external mode) — returns IIN and name
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(
                f"{settings.sigex_url}/api/auth",
                json={
                    "nonce": session["nonce"],
                    "signature": sig_data,
                    "external": True,
                },
            )
        except Exception as e:
            logger.error(f"SIGEX auth verify error: {e}")
            return QrStatusResponse(status="error")

    if resp.status_code != 200:
        logger.error(f"SIGEX auth verify failed: {resp.status_code} {resp.text}")
        session["status"] = "expired"
        return QrStatusResponse(status="failed")

    auth_data = resp.json()
    iin = auth_data.get("userId", "")
    subject = auth_data.get("subject", "")

    # Strip "IIN" prefix if present (SIGEX returns "IIN030720550970", kalkan returns "030720550970")
    if iin.startswith("IIN"):
        iin = iin[3:]

    # Parse name from subject string: "SERIALINUMBER=IIN...,CN=Name..."
    full_name = ""
    for part in subject.split(","):
        if part.strip().startswith("CN="):
            full_name = part.strip()[3:]
            break

    if not iin:
        logger.error(f"SIGEX auth: no IIN in response: {auth_data}")
        session["status"] = "expired"
        return QrStatusResponse(status="failed")

    logger.info(f"QR auth success: iin={iin}, name={full_name}")

    _save_user(iin, full_name, auth_method="egov_qr")

    # Issue JWT
    user = UserInfo(iin=iin, fullName=full_name)
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
