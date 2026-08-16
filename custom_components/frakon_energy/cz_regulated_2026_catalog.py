"""Frozen official Czech D25d regulated prices for calendar year 2026.

The monetary values in this module were transcribed from the official ERÚ
low-voltage workbook attached to price measure 14/2025 and cross-checked against
the 2026 regulated measures.  The exact workbook SHA-256 is kept in evidence so
a future source change cannot silently mutate an already-confirmed tariff.

Price measure 1/2026, effective 2026-06-01, changes the treatment of reserved
capacity in local distribution systems.  It does not change the household D25d
low-voltage table or the universal household components used here, but is added
to post-June evidence explicitly rather than being ignored.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import re

from .cz_regulated_sources import (
    CzechRegulatedTariffInputs,
    RegulatedAuthority,
    RegulatedPriceSource,
)

YEAR_START = date(2026, 1, 1)
AMENDMENT_START = date(2026, 6, 1)
YEAR_END = date(2026, 12, 31)

ERU_LOW_VOLTAGE_2026_XLSX_URL = (
    "https://eru.gov.cz/sites/default/files/obsah/prilohy/ceny-nn26-1.xlsx"
)
ERU_LOW_VOLTAGE_2026_XLSX_SHA256 = (
    "ca2948ae156708fa5a340000577b912e10cc92de973de10deb8a215bd46af480"
)
ERU_OTHER_2026_PDF_URL = "https://eru.gov.cz/sites/default/files/obsah/prilohy/erv172025.pdf"
ERU_OTHER_2026_PDF_SHA256 = (
    "a69125ea10b727d1fae87efd154e4f52ea6a8d8b47b57bf74bc95b0370603160"
)
ERU_POZE_2026_PDF_URL = "https://eru.gov.cz/sites/default/files/obsah/prilohy/erv192025.pdf"
ERU_POZE_2026_PDF_SHA256 = (
    "ffb293aeead64ec96a22b5b473274a3c2e011673a8f96a84975c26289d0030c0"
)
ERU_AMENDMENT_1_2026_PDF_URL = (
    "https://eru.gov.cz/sites/default/files/obsah/prilohy/erv022026.pdf"
)
ERU_AMENDMENT_1_2026_PDF_SHA256 = (
    "851778224742eab2545f12079a2a8c9ef543c6e2839c4254233b637a0882cadd"
)
OTE_2026_URL = (
    "https://www.ote-cr.cz/cs/registrace-a-smlouvy/smluvni-vztahy-elektrina/"
    "ceny-za-sluzby-ote"
)
CUSTOMS_ELECTRICITY_TAX_URL = (
    "https://celnisprava.gov.cz/cz/dane/WebENG/WebElek/Stranky/default.aspx"
)

SYSTEM_SERVICES_2026_CZK_PER_KWH = Decimal("0.16424")
ELECTRICITY_TAX_2026_CZK_PER_KWH = Decimal("0.02830")

# ERÚ workbook, sheet "Distribuce", section "Sazba D 25d".
_D25D_DISTRIBUTION = {
    "cez_distribuce": (Decimal("2.25245"), Decimal("0.11650")),
    "eg_d": (Decimal("2.24388"), Decimal("0.22430")),
    "pre_distribuce": (Decimal("1.65649"), Decimal("0.17520")),
}

# Monthly breaker payment in CZK excluding VAT for the standard 3-phase breaker
# choices exposed by the FRAKON wizard.  Values are exact ERÚ table entries.
_D25D_BREAKER_3P = {
    "cez_distribuce": {
        10: Decimal("107"), 16: Decimal("172"), 20: Decimal("215"),
        25: Decimal("269"), 32: Decimal("344"), 40: Decimal("430"),
        50: Decimal("537"), 63: Decimal("677"),
    },
    "eg_d": {
        10: Decimal("98"), 16: Decimal("157"), 20: Decimal("196"),
        25: Decimal("245"), 32: Decimal("314"), 40: Decimal("392"),
        50: Decimal("491"), 63: Decimal("618"),
    },
    "pre_distribuce": {
        10: Decimal("80"), 16: Decimal("128"), 20: Decimal("160"),
        25: Decimal("200"), 32: Decimal("256"), 40: Decimal("320"),
        50: Decimal("401"), 63: Decimal("505"),
    },
}

# The D25d table prices every 1-phase breaker up to and including 1x25 A in the
# same first breaker band as <=3x10 A.  Larger 1-phase breakers deliberately fail
# closed until an exact table-aware rule is implemented.
_D25D_BREAKER_1P_TO_25 = {
    "cez_distribuce": Decimal("107"),
    "eg_d": Decimal("98"),
    "pre_distribuce": Decimal("80"),
}

_BREAKER_RE = re.compile(r"^(1|3)x([1-9]\d*)A$")


def _sources_for_day(day: date) -> tuple[RegulatedPriceSource, ...]:
    common = (
        RegulatedPriceSource(
            authority=RegulatedAuthority.ERU,
            document_id="ERÚ CV 14/2025 – ceny distribuce NN 2026 (XLSX)",
            source_url=ERU_LOW_VOLTAGE_2026_XLSX_URL,
            valid_from=YEAR_START,
            valid_to=YEAR_END,
            document_date=date(2026, 1, 23),
            checksum=ERU_LOW_VOLTAGE_2026_XLSX_SHA256,
        ),
        RegulatedPriceSource(
            authority=RegulatedAuthority.ERU,
            document_id="ERÚ CV 13/2025 – ostatní regulované ceny 2026",
            source_url=ERU_OTHER_2026_PDF_URL,
            valid_from=YEAR_START,
            valid_to=YEAR_END,
            checksum=ERU_OTHER_2026_PDF_SHA256,
        ),
        RegulatedPriceSource(
            authority=RegulatedAuthority.ERU,
            document_id="ERÚ CV 15/2025 – POZE 2026",
            source_url=ERU_POZE_2026_PDF_URL,
            valid_from=YEAR_START,
            valid_to=YEAR_END,
            checksum=ERU_POZE_2026_PDF_SHA256,
        ),
        RegulatedPriceSource(
            authority=RegulatedAuthority.OTE,
            document_id="OTE – ceny za služby v elektroenergetice 2026",
            source_url=OTE_2026_URL,
            valid_from=YEAR_START,
            valid_to=YEAR_END,
        ),
        RegulatedPriceSource(
            authority=RegulatedAuthority.CUSTOMS,
            document_id="Daň z elektřiny – zákon č. 261/2007 Sb., část 47",
            source_url=CUSTOMS_ELECTRICITY_TAX_URL,
            valid_from=YEAR_START,
            valid_to=YEAR_END,
        ),
    )
    if day < AMENDMENT_START:
        return common
    return common + (
        RegulatedPriceSource(
            authority=RegulatedAuthority.ERU,
            document_id="ERÚ CV 1/2026 – změna ostatních regulovaných cen",
            source_url=ERU_AMENDMENT_1_2026_PDF_URL,
            valid_from=AMENDMENT_START,
            valid_to=YEAR_END,
            checksum=ERU_AMENDMENT_1_2026_PDF_SHA256,
        ),
    )


def _breaker_price(distributor: str, breaker_code: str) -> Decimal:
    match = _BREAKER_RE.fullmatch(breaker_code)
    if match is None:
        raise LookupError("unsupported regulated breaker code")
    phases = int(match.group(1))
    amperes = int(match.group(2))
    if phases == 3:
        price = _D25D_BREAKER_3P.get(distributor, {}).get(amperes)
        if price is None:
            raise LookupError("no exact official D25d breaker price for requested 3-phase breaker")
        return price
    if amperes <= 25 and amperes in {10, 16, 20, 25}:
        try:
            return _D25D_BREAKER_1P_TO_25[distributor]
        except KeyError as err:
            raise LookupError("unsupported regulated distributor") from err
    raise LookupError("no exact official D25d breaker price for requested 1-phase breaker")


def official_2026_regulated_inputs(
    *,
    distributor: str,
    distribution_tariff: str,
    breaker_code: str,
    day: date,
) -> CzechRegulatedTariffInputs:
    """Return an exact unconfirmed official 2026 D25d input snapshot.

    This function is intentionally narrow. Unsupported years, tariffs, distributor
    identifiers or breaker bands raise ``LookupError`` instead of falling back to
    an approximate tariff.
    """
    if not isinstance(day, date) or not YEAR_START <= day <= YEAR_END:
        raise LookupError("official frozen regulated catalog currently covers calendar year 2026")
    if distribution_tariff != "D25d":
        raise LookupError("official frozen regulated catalog does not yet cover this distribution tariff")
    if distributor not in _D25D_DISTRIBUTION:
        raise LookupError("official frozen regulated catalog does not cover this distributor")

    distribution_vt, distribution_nt = _D25D_DISTRIBUTION[distributor]
    breaker_monthly = _breaker_price(distributor, breaker_code)
    snapshot_start = YEAR_START if day < AMENDMENT_START else AMENDMENT_START
    snapshot_end = date(2026, 5, 31) if day < AMENDMENT_START else YEAR_END

    return CzechRegulatedTariffInputs(
        distributor=distributor,
        distribution_tariff=distribution_tariff,
        breaker_code=breaker_code,
        valid_from=snapshot_start,
        valid_to=snapshot_end,
        distribution_vt_czk_per_kwh=distribution_vt,
        distribution_nt_czk_per_kwh=distribution_nt,
        breaker_monthly_czk=breaker_monthly,
        system_services_czk_per_kwh=SYSTEM_SERVICES_2026_CZK_PER_KWH,
        electricity_tax_czk_per_kwh=ELECTRICITY_TAX_2026_CZK_PER_KWH,
        sources=_sources_for_day(day),
    )
