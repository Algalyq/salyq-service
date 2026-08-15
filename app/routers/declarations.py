from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from jose import jwt, JWTError

from app.config import settings
from app.eligibility import get_declaration_history_async, determine_required_form
from datetime import datetime

router = APIRouter(prefix="/api/v1/declarations", tags=["declarations"])


class EligibilityResponse(BaseModel):
    target_form: str
    title_ru: str
    title_kk: str
    reason_ru: str
    reason_kk: str
    is_first_time: bool
    target_year: int
    user_iin: str
    user_name: str


def get_current_user(authorization: str = Header(...)) -> dict:
    """Extract user info from JWT Bearer token."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")

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


@router.get("/eligibility", response_model=EligibilityResponse)
async def get_eligibility(user: dict = Depends(get_current_user)):
    """Determine which declaration form the user needs to file.

    Uses declaration history (from KGD API or mock) to determine:
    - If user has never filed 250.00 → recommend 250.00 (first-time)
    - If user has filed 250.00 → recommend 270.00 (annual)
    """
    iin = user["iin"]
    current_year = datetime.now().year

    history = await get_declaration_history_async(iin)
    result = determine_required_form(history, current_year)

    return EligibilityResponse(
        target_form=result.target_form,
        title_ru=result.title_ru,
        title_kk=result.title_kk,
        reason_ru=result.reason_ru,
        reason_kk=result.reason_kk,
        is_first_time=result.is_first_time,
        target_year=result.target_year,
        user_iin=iin,
        user_name=user["name"],
    )
