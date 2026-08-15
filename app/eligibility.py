from dataclasses import dataclass


@dataclass
class DeclarationRecord:
    form_code: str
    year: int
    status: str  # ACCEPTED, REJECTED, DRAFT


@dataclass
class EligibilityResult:
    target_form: str
    title_ru: str
    title_kk: str
    reason_ru: str
    reason_kk: str
    is_first_time: bool
    target_year: int


def determine_required_form(
    declaration_history: list[DeclarationRecord], current_year: int
) -> EligibilityResult:
    """Determine which form the user needs to file next.

    Logic:
    - If user has never submitted 250.00 → they need 250.00 (first time)
    - If user has submitted 250.00 → they need 270.00 for the previous year
    """
    accepted_250 = [
        d for d in declaration_history
        if d.form_code == "250.00" and d.status == "ACCEPTED"
    ]
    accepted_270 = [
        d for d in declaration_history
        if d.form_code == "270.00" and d.status == "ACCEPTED"
    ]

    has_submitted_250 = len(accepted_250) > 0

    if not has_submitted_250:
        return EligibilityResult(
            target_form="250.00",
            title_ru="Вам необходимо сдать ФНО 250.00 (Входная)",
            title_kk="Сізге ТҒН 250.00 (Кіру) тапсыру қажет",
            reason_ru=(
                "Вы еще не сдавали первичную декларацию об активах и обязательствах. "
                "Это первый шаг во всеобщем декларировании."
            ),
            reason_kk=(
                "Сіз әлі активтер мен міндеттемелер туралы бастапқы декларацияны "
                "тапсырған жоқсыз. Бұл жалпыға бірдей декларациялаудағы бірінші қадам."
            ),
            is_first_time=True,
            target_year=current_year,
        )

    # User has submitted 250.00 — needs 270.00 for previous year
    last_250_year = max(d.year for d in accepted_250)
    last_270_years = {d.year for d in accepted_270}
    target_year = current_year - 1

    # If already submitted 270 for target year, advance to next year
    while target_year in last_270_years:
        target_year += 1

    return EligibilityResult(
        target_form="270.00",
        title_ru=f"Вам необходимо сдать ФНО 270.00 за {target_year} год",
        title_kk=f"Сізге ТҒН 270.00 ({target_year} жыл) тапсыру қажет",
        reason_ru=(
            f"Вы уже сдавали входную форму 250.00 в {last_250_year} году. "
            f"Теперь вам нужно ежегодно отчитываться о доходах и имуществе "
            f"за {target_year} год."
        ),
        reason_kk=(
            f"Сіз {last_250_year} жылы кіру формасын (250.00) тапсырғансыз. "
            f"Енді жыл сайын {target_year} жылғы кірістер мен мүлік туралы "
            f"есеп беруіңіз керек."
        ),
        is_first_time=False,
        target_year=target_year,
    )


# Mock declaration history — simulates KGD API response
# In production, this would be replaced by a real API call to KGD
MOCK_DECLARATION_HISTORY: dict[str, list[DeclarationRecord]] = {
    # IINs that have NOT submitted 250 → need 250
    # IINs that HAVE submitted 250 → need 270
    # Default: empty (first-time user)
}


def get_declaration_history(iin: str) -> list[DeclarationRecord]:
    """Get declaration history for a taxpayer by IIN.

    Synchronous wrapper — used for mock/fallback.
    For real KGD API calls, use fetch_declaration_history_from_kgd() directly.
    """
    from app.kgd_client import _mock_history
    return _mock_history(iin)


async def get_declaration_history_async(iin: str) -> list[DeclarationRecord]:
    """Async: Get declaration history from KGD Smart Bridge (or mock fallback)."""
    from app.kgd_client import fetch_declaration_history_from_kgd
    return await fetch_declaration_history_from_kgd(iin)
