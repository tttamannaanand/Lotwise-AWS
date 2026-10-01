"""
Indian capital gains rules, versioned by financial year (April 1 to March 31).

Rates are kept as data, not code, so a Budget change means adding a new entry
here rather than rewriting the engine.

Sources checked September 2026:
  - Listed equity / equity-oriented MF: STCG 20% (s.111A), LTCG 12.5% above
    Rs 1.25 lakh aggregate per FY (s.112A), long-term after >12 months.
    Unchanged by Budget 2026 for FY 2026-27.
  - Virtual digital assets: flat 30% (s.115BBH), no holding-period distinction,
    only cost of acquisition deductible, losses cannot be set off against
    anything (including other VDA gains), 1% TDS (s.194S).
  - Income-tax Act, 2025 applies from 1 April 2026 (FY 2026-27 onwards) with
    renumbered sections; FY 2025-26 income is still under the 1961 Act.

FY 2024-25 is intentionally NOT configured: rates changed mid-year on
23 July 2024, which needs date-split logic that is out of MVP scope.
"""

from decimal import Decimal

RULES = {
    "2025-26": {
        "act": "Income-tax Act, 1961",
        "equity_stcg_rate": Decimal("0.20"),
        "equity_ltcg_rate": Decimal("0.125"),
        "equity_ltcg_exemption": Decimal("125000"),
        "equity_long_term_months": 12,
        "vda_rate": Decimal("0.30"),
        "cess_rate": Decimal("0.04"),
        "sections": {
            "equity_st": "Section 111A",
            "equity_lt": "Section 112A",
            "vda": "Section 115BBH (Schedule VDA)",
        },
    },
    "2026-27": {
        "act": "Income-tax Act, 2025",
        "equity_stcg_rate": Decimal("0.20"),
        "equity_ltcg_rate": Decimal("0.125"),
        "equity_ltcg_exemption": Decimal("125000"),
        "equity_long_term_months": 12,
        "vda_rate": Decimal("0.30"),
        "cess_rate": Decimal("0.04"),
        "sections": {
            "equity_st": "Equivalent of former s.111A (renumbered in the 2025 Act)",
            "equity_lt": "Equivalent of former s.112A (renumbered in the 2025 Act)",
            "vda": "Equivalent of former s.115BBH (renumbered in the 2025 Act)",
        },
    },
}

# Listed equity bought before this date may qualify for the 31 Jan 2018 fair
# market value "grandfathering" rule, which the MVP does not apply.
GRANDFATHERING_CUTOFF = "2018-02-01"


def fy_for_date(iso_date: str) -> str:
    """'2025-06-14' -> '2025-26'; '2026-02-01' -> '2025-26'."""
    year, month = int(iso_date[0:4]), int(iso_date[5:7])
    start = year if month >= 4 else year - 1
    return f"{start}-{str(start + 1)[2:]}"


def rules_for(fy: str):
    return RULES.get(fy)


def supported_years():
    return sorted(RULES.keys())
