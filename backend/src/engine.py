"""
FIFO lot matching and per-financial-year capital gains summary.

Equity: each sale is matched to the oldest remaining purchase lots (FIFO is
the standard method for demat holdings). Each matched slice is short-term or
long-term by its own holding period (> 12 months = long-term).

Crypto (VDA): each sale/swap is one transfer. Gain = consideration - cost of
acquisition. Positive transfers are taxed; losses are listed but never set
off against anything.
"""

from collections import defaultdict, deque
from datetime import date as Date
from decimal import ROUND_HALF_UP, Decimal

from tax_rules import GRANDFATHERING_CUTOFF, fy_for_date, rules_for

ZERO = Decimal("0")


def _add_months(iso, months):
    d = Date.fromisoformat(iso)
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    # clamp day (e.g. 29 Feb -> 28 Feb)
    for day in (d.day, 30, 29, 28):
        try:
            return Date(y, m, day)
        except ValueError:
            continue


def _is_long_term(buy_date, sell_date, months):
    return Date.fromisoformat(sell_date) > _add_months(buy_date, months)


def _r(x):
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def match_lots(legs, long_term_months=12):
    """Returns (transfers, holdings, warnings)."""
    groups = defaultdict(list)
    for i, leg in enumerate(legs):
        groups[(leg["asset_class"], leg["key"])].append((i, leg))

    transfers, holdings, warnings = [], [], []
    intraday_flagged = set()
    grandfather_flagged = set()

    for (asset_class, key), items in groups.items():
        # Oldest first; on the same day, buys before sells; then file order.
        items.sort(key=lambda t: (t[1]["date"], 0 if t[1]["side"] == "BUY" else 1, t[0]))
        lots = deque()
        buy_dates = set()
        for _, leg in items:
            qty = Decimal(leg["quantity"])
            value = Decimal(leg["value_inr"])
            if leg["side"] == "BUY":
                lots.append({"date": leg["date"], "qty": qty, "unit_cost": value / qty})
                buy_dates.add(leg["date"])
                if (
                    asset_class == "equity"
                    and leg["date"] < GRANDFATHERING_CUTOFF
                    and key not in grandfather_flagged
                ):
                    grandfather_flagged.add(key)
                    warnings.append(
                        {
                            "code": "grandfathering",
                            "message": f"{leg['symbol']} has purchases before 1 Feb 2018. The 31 Jan 2018 fair-value rule may lower your gain; Lotwise uses actual cost.",
                        }
                    )
                continue

            if (
                asset_class == "equity"
                and leg["date"] in buy_dates
                and (key, leg["date"]) not in intraday_flagged
            ):
                intraday_flagged.add((key, leg["date"]))
                warnings.append(
                    {
                        "code": "intraday",
                        "message": f"{leg['symbol']} was bought and sold on {leg['date']}. Intraday trading is usually business income, not capital gains. It is matched here as a normal sale.",
                    }
                )

            unit_proceeds = value / qty
            remaining = qty
            parts = []
            while remaining > 0 and lots:
                lot = lots[0]
                take = min(remaining, lot["qty"])
                cost = take * lot["unit_cost"]
                proceeds = take * unit_proceeds
                term = "VDA"
                if asset_class == "equity":
                    term = (
                        "LT" if _is_long_term(lot["date"], leg["date"], long_term_months) else "ST"
                    )
                parts.append(
                    {
                        "buy_date": lot["date"],
                        "quantity": str(take),
                        "cost": str(_r(cost)),
                        "proceeds": str(_r(proceeds)),
                        "gain": str(_r(proceeds - cost)),
                        "term": term,
                    }
                )
                lot["qty"] -= take
                remaining -= take
                if lot["qty"] == 0:
                    lots.popleft()

            unmatched = str(remaining) if remaining > 0 else None
            if unmatched:
                warnings.append(
                    {
                        "code": "unmatched",
                        "message": f"Sold {remaining.normalize()} more {leg['symbol']} on {leg['date']} than the uploaded purchases cover. Upload older statements; the unmatched part is left out of totals.",
                    }
                )
            transfers.append(
                {
                    "id": f"{leg['import_id']}:{leg['row']}:{asset_class}:{key}",
                    "asset_class": asset_class,
                    "symbol": leg["symbol"],
                    "isin": leg.get("isin"),
                    "sell_date": leg["date"],
                    "fy": fy_for_date(leg["date"]),
                    "quantity": leg["quantity"],
                    "proceeds": str(_r(sum((Decimal(p["proceeds"]) for p in parts), ZERO))),
                    "cost": str(_r(sum((Decimal(p["cost"]) for p in parts), ZERO))),
                    "gain": str(_r(sum((Decimal(p["gain"]) for p in parts), ZERO))),
                    "parts": parts,
                    "unmatched_quantity": unmatched,
                }
            )

        for lot in lots:
            if lot["qty"] > 0:
                holdings.append(
                    {
                        "asset_class": asset_class,
                        "symbol": next(leg["symbol"] for _, leg in items),
                        "buy_date": lot["date"],
                        "quantity": str(lot["qty"].normalize()),
                        "cost": str(_r(lot["qty"] * lot["unit_cost"])),
                    }
                )

    transfers.sort(key=lambda t: (t["sell_date"], t["symbol"]))
    holdings.sort(key=lambda h: (h["asset_class"], h["symbol"], h["buy_date"]))
    return transfers, holdings, warnings


def summarize(transfers, fy):
    """Aggregate one financial year and estimate tax using that year's rules."""
    rules = rules_for(fy)
    fy_transfers = [t for t in transfers if t["fy"] == fy]

    st_g = st_l = lt_g = lt_l = ZERO
    vda_g = vda_l = ZERO
    for t in fy_transfers:
        if t["asset_class"] == "crypto":
            g = Decimal(t["gain"])
            if g >= 0:
                vda_g += g
            else:
                vda_l += -g
            continue
        for p in t["parts"]:
            g = Decimal(p["gain"])
            if p["term"] == "ST":
                st_g, st_l = (st_g + g, st_l) if g >= 0 else (st_g, st_l - g)
            else:
                lt_g, lt_l = (lt_g + g, lt_l) if g >= 0 else (lt_g, lt_l - g)

    # Set-off: ST losses can reduce ST and LT gains; LT losses only LT gains.
    net_st = st_g - st_l
    net_lt = lt_g - lt_l
    lt_loss_cf = ZERO
    if net_lt < 0:
        lt_loss_cf, net_lt = -net_lt, ZERO
    st_loss_cf = ZERO
    if net_st < 0:
        offset = min(-net_st, net_lt)
        net_lt -= offset
        net_st += offset
        if net_st < 0:
            st_loss_cf, net_st = -net_st, ZERO

    summary = {
        "fy": fy,
        "rules_available": rules is not None,
        "act": rules["act"] if rules else None,
        "sections": rules["sections"] if rules else None,
        "transfer_count": len(fy_transfers),
        "equity": {
            "st_gains": str(_r(st_g)),
            "st_losses": str(_r(st_l)),
            "lt_gains": str(_r(lt_g)),
            "lt_losses": str(_r(lt_l)),
            "net_st": str(_r(net_st)),
            "net_lt_before_exemption": str(_r(net_lt)),
            "st_loss_carry_forward": str(_r(st_loss_cf)),
            "lt_loss_carry_forward": str(_r(lt_loss_cf)),
        },
        "vda": {
            "gains": str(_r(vda_g)),
            "losses_not_set_off": str(_r(vda_l)),
        },
        "tax": None,
    }

    if rules:
        exemption_used = min(net_lt, rules["equity_ltcg_exemption"])
        taxable_lt = net_lt - exemption_used
        st_tax = net_st * rules["equity_stcg_rate"]
        lt_tax = taxable_lt * rules["equity_ltcg_rate"]
        vda_tax = vda_g * rules["vda_rate"]
        base = st_tax + lt_tax + vda_tax
        cess = base * rules["cess_rate"]
        summary["equity"]["ltcg_exemption_used"] = str(_r(exemption_used))
        summary["equity"]["taxable_lt"] = str(_r(taxable_lt))
        summary["tax"] = {
            "equity_st": str(_r(st_tax)),
            "equity_lt": str(_r(lt_tax)),
            "vda": str(_r(vda_tax)),
            "cess": str(_r(cess)),
            "total": str(_r(base + cess)),
            "rates": {
                "equity_st": str(rules["equity_stcg_rate"]),
                "equity_lt": str(rules["equity_ltcg_rate"]),
                "vda": str(rules["vda_rate"]),
                "cess": str(rules["cess_rate"]),
                "ltcg_exemption": str(rules["equity_ltcg_exemption"]),
            },
        }
    return summary


def build_report(legs, fy=None):
    transfers, holdings, warnings = match_lots(legs)
    years = sorted({t["fy"] for t in transfers}, reverse=True)
    if not fy and years:
        # Prefer the latest *completed* year (the one people are filing for).
        current = fy_for_date(Date.today().isoformat())
        completed = [y for y in years if y < current]
        fy = completed[0] if completed else years[0]
    summary = summarize(transfers, fy) if fy else None
    if fy and summary and not summary["rules_available"]:
        warnings.append(
            {
                "code": "rules",
                "message": f"Tax rates for FY {fy} are not set up yet. Gains are calculated, but no tax estimate is shown.",
            }
        )
    return {
        "fy": fy,
        "years": years,
        "summary": summary,
        "transfers": [t for t in transfers if t["fy"] == fy] if fy else [],
        "holdings": holdings,
        "warnings": warnings,
    }
