"""KGD Smart Bridge client for fetching taxpayer declaration history.

Integration via Smart Bridge (sb.egov.kz) SOAP API.
Service: FNO_INTEGRATION_STATUS (replaces deprecated SONO_FNO_GET_STATUS)

Requires Smart Bridge account + agreement with KGD.
If credentials are not configured, falls back to mock data.
"""

import httpx
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from app.config import settings
from app.eligibility import DeclarationRecord


# Smart Bridge SOAP endpoint
SMARTBRIDGE_URL = "https://sb.egov.kz/SmartBridgeService/"

# SOAP envelope template for FNO_INTEGRATION_STATUS
SOAP_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:ns="http://www.kgd.gov.kz/fno/integration">
  <soapenv:Header>
    <ns:AuthInfo>
      <ns:senderId>{sender_id}</ns:senderId>
      <ns:password>{password}</ns:password>
    </ns:AuthInfo>
  </soapenv:Header>
  <soapenv:Body>
    <ns:GetTaxpayerDeclarations>
      <ns:iin>{iin}</ns:iin>
      <ns:formCodes>250.00,270.00</ns:formCodes>
    </ns:GetTaxpayerDeclarations>
  </soapenv:Body>
</soapenv:Envelope>"""


@dataclass
class SmartBridgeConfig:
    enabled: bool
    sender_id: str
    password: str
    service_key: str


def _get_sb_config() -> SmartBridgeConfig:
    return SmartBridgeConfig(
        enabled=settings.kgd_smartbridge_enabled,
        sender_id=settings.kgd_sender_id,
        password=settings.kgd_sender_password,
        service_key=settings.kgd_service_key,
    )


async def fetch_declaration_history_from_kgd(iin: str) -> list[DeclarationRecord]:
    """Fetch declaration history from KGD via Smart Bridge SOAP API.

    Falls back to mock data if Smart Bridge is not configured.
    """
    cfg = _get_sb_config()

    if not cfg.enabled:
        return _mock_history(iin)

    try:
        soap_body = SOAP_TEMPLATE.format(
            sender_id=cfg.sender_id,
            password=cfg.password,
            iin=iin,
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                SMARTBRIDGE_URL,
                content=soap_body,
                headers={
                    "Content-Type": "text/xml; charset=utf-8",
                    "SOAPAction": "http://www.kgd.gov.kz/fno/integration/GetTaxpayerDeclarations",
                },
            )

            if resp.status_code != 200:
                raise ValueError(f"Smart Bridge returned {resp.status_code}")

            return _parse_soap_response(resp.text)

    except Exception as e:
        raise ValueError(f"KGD Smart Bridge request failed: {e}")


def _parse_soap_response(xml_text: str) -> list[DeclarationRecord]:
    """Parse SOAP response from KGD Smart Bridge."""
    root = ET.fromstring(xml_text)

    # Find all Declaration elements in the response
    ns = {"ns": "http://www.kgd.gov.kz/fno/integration"}

    records: list[DeclarationRecord] = []
    for decl in root.findall(".//ns:Declaration", ns):
        form_code = decl.findtext("ns:formCode", default="", namespaces=ns)
        year_str = decl.findtext("ns:year", default="0", namespaces=ns)
        status = decl.findtext("ns:status", default="UNKNOWN", namespaces=ns)

        try:
            year = int(year_str)
        except ValueError:
            year = 0

        records.append(
            DeclarationRecord(
                form_code=form_code,
                year=year,
                status=status.upper(),
            )
        )

    return records


# --- Mock fallback ---

_MOCK_HISTORY: dict[str, list[DeclarationRecord]] = {
    # Example: user who already filed 250.00 in 2024
    # "030720550970": [
    #     DeclarationRecord(form_code="250.00", year=2024, status="ACCEPTED"),
    # ],
}


def _mock_history(iin: str) -> list[DeclarationRecord]:
    """Return mock declaration history for development."""
    return _MOCK_HISTORY.get(iin, [])
