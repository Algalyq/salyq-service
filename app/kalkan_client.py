import httpx
from dataclasses import dataclass

from app.config import settings


@dataclass
class KalkanSubject:
    iin: str
    full_name: str
    email: str
    key_type: str


@dataclass
class KalkanValidationResult:
    valid: bool
    ocsp_status: str
    subject: KalkanSubject
    error: str | None = None


async def validate_cms(cms: str) -> KalkanValidationResult:
    """Send CMS signature to kalkan-validator service for validation."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{settings.kalkan_url}/validate",
            json={"cms": cms},
        )

        if resp.status_code != 200:
            raise ValueError(f"Kalkan service error: {resp.status_code} {resp.text}")

        data = resp.json()

        subject = KalkanSubject(
            iin=data.get("subject", {}).get("iin", "000000000000"),
            full_name=data.get("subject", {}).get("full_name", "Unknown"),
            email=data.get("subject", {}).get("email", ""),
            key_type=data.get("subject", {}).get("key_type", "INDIVIDUAL"),
        )

        return KalkanValidationResult(
            valid=data.get("valid", False),
            ocsp_status=data.get("ocsp_status", "UNKNOWN"),
            subject=subject,
            error=data.get("error"),
        )
