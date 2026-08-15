from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.security import generate_challenge, create_access_token, create_refresh_token
from app.kalkan_client import validate_cms

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
