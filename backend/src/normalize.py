"""
Apply a confirmed column mapping to every row and produce canonical trade
"legs". All arithmetic uses Decimal. No AI involved.

A leg is one acquisition or one disposal of one asset:
  {date, asset_class, key, symbol, isin, side, quantity, value_inr, fees_inr, row, import_id}

  - equity BUY : value_inr = cost incl. brokerage
  - equity SELL: value_inr = proceeds net of brokerage
  - crypto     : value_inr = consideration only; fees are NOT deductible for
                 VDAs, so they are recorded (fees_inr) but never used.

Crypto pair trades not quoted in INR (e.g. BUY 2 ETH on ETH/USDT) produce two
legs: acquire ETH and dispose of the USDT paid, both valued at the trade's INR
value. A crypto-to-crypto swap is a taxable transfer of the asset given up.
"""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from mapper import classify_side

_QUOTES = ["INR", "USDT", "USDC", "BTC", "ETH", "BUSD", "DAI", "FDUSD"]

_ISO_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"]
_NAMED_MONTH = [
    "%d-%b-%Y",
    "%d %b %Y",
    "%d-%b-%y",
    "%d %B %Y",
    "%d-%B-%Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d/%b/%Y",
]
_DAY_FIRST = ["%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y"]
_MONTH_FIRST = ["%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y"]


def parse_date(value, day_first=True):
    v = (value or "").strip()
    if not v:
        return None
    formats = (
        _ISO_FORMATS
        + _NAMED_MONTH
        + (_DAY_FIRST + _MONTH_FIRST if day_first else _MONTH_FIRST + _DAY_FIRST)
    )
    candidates = [v, v.split("T")[0], v.split(" ")[0], " ".join(v.split(" ")[:3])]
    for c in candidates:
        for f in formats:
            try:
                d = datetime.strptime(c, f)
                if 1990 <= d.year <= 2100:
                    return d.date().isoformat()
            except ValueError:
                continue
    return None


def parse_number(value):
    if value is None:
        return None
    v = str(value).strip()
    if not v or v in ("-", "--", "NA", "N/A"):
        return None
    neg = v.startswith("(") and v.endswith(")")
    v = re.sub(r"(?i)(rs\.?|inr|₹|\s|,|\(|\))", "", v)
    v = re.sub(r"[A-Za-z]+$", "", v)  # trailing currency codes like "0.001BTC"
    try:
        n = Decimal(v)
    except InvalidOperation:
        return None
    return -n if neg else n


def split_pair(symbol):
    """'BTC/INR' -> ('BTC','INR'); 'ETHUSDT' -> ('ETH','USDT'); 'SOL' -> ('SOL','INR')."""
    s = symbol.strip().upper()
    parts = [p for p in re.split(r"[/\-_: ]", s) if p]
    if len(parts) == 2:
        return parts[0], parts[1]
    for q in _QUOTES:
        if s.endswith(q) and len(s) - len(q) >= 2:
            return s[: -len(q)], q
    return s, "INR"


_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def normalize_rows(headers, rows, mapping, import_id="preview"):
    """Returns (legs, errors). errors: [{row, message}] with 1-based data row numbers."""
    cols = mapping["columns"]
    idx = {f: (headers.index(h) if h else None) for f, h in cols.items()}
    buy_vals = {v.strip().lower() for v in mapping["side_values"].get("buy", [])}
    sell_vals = {v.strip().lower() for v in mapping["side_values"].get("sell", [])}
    asset_class = mapping["asset_class"]
    day_first = mapping["day_first"]

    def get(row, field):
        i = idx.get(field)
        return row[i] if i is not None and i < len(row) else ""

    legs, errors = [], []
    if idx["date"] is None or idx["symbol"] is None or idx["quantity"] is None:
        return [], [{"row": 0, "message": "Map the date, symbol and quantity columns to continue."}]
    if idx["price"] is None and idx["total_inr"] is None:
        return [], [
            {"row": 0, "message": "Map either a price column or a total rupee value column."}
        ]

    for n, row in enumerate(rows, start=1):
        date = parse_date(get(row, "date"), day_first)
        if not date:
            errors.append({"row": n, "message": f"Could not read the date '{get(row, 'date')}'."})
            continue
        symbol_raw = get(row, "symbol").strip()
        if not symbol_raw:
            errors.append({"row": n, "message": "Symbol is empty."})
            continue
        qty = parse_number(get(row, "quantity"))
        if qty is None or qty == 0:
            errors.append(
                {"row": n, "message": f"Quantity '{get(row, 'quantity')}' is not a number."}
            )
            continue

        # Side: explicit column first, else sign of quantity.
        side = None
        if idx["side"] is not None:
            raw_side = get(row, "side").strip().lower()
            if raw_side in buy_vals:
                side = "BUY"
            elif raw_side in sell_vals:
                side = "SELL"
            else:
                guess = classify_side(raw_side)
                side = guess.upper() if guess else None
            if not side:
                errors.append(
                    {
                        "row": n,
                        "message": f"Could not tell if '{get(row, 'side')}' is a buy or a sell.",
                    }
                )
                continue
        else:
            side = "SELL" if qty < 0 else "BUY"
        qty = abs(qty)

        price = parse_number(get(row, "price")) if idx["price"] is not None else None
        total_inr = parse_number(get(row, "total_inr")) if idx["total_inr"] is not None else None
        fees = parse_number(get(row, "fees")) if idx["fees"] is not None else None
        fees = abs(fees) if fees else Decimal("0")
        total_inr = abs(total_inr) if total_inr is not None else None
        price = abs(price) if price is not None else None

        if asset_class == "crypto":
            base, quote = split_pair(symbol_raw)
            if quote == "INR":
                value = (
                    total_inr
                    if total_inr is not None
                    else (qty * price if price is not None else None)
                )
            else:
                value = total_inr
            if value is None:
                msg = (
                    f"{symbol_raw} is not priced in rupees. Map an INR value column so the trade can be valued."
                    if quote != "INR"
                    else "Needs a price or total value."
                )
                errors.append({"row": n, "message": msg})
                continue
            legs.append(
                _leg(date, "crypto", base, base, None, side, qty, value, n, import_id, fees)
            )
            if quote != "INR":
                if price is None:
                    errors.append(
                        {
                            "row": n,
                            "message": f"Needs the {quote} price to work out how much {quote} changed hands.",
                        }
                    )
                    legs.pop()
                    continue
                quote_qty = qty * price
                quote_side = "SELL" if side == "BUY" else "BUY"
                legs.append(
                    _leg(
                        date,
                        "crypto",
                        quote,
                        quote,
                        None,
                        quote_side,
                        quote_qty,
                        value,
                        n,
                        import_id,
                        Decimal("0"),
                    )
                )
        else:
            gross = (
                total_inr if total_inr is not None else (qty * price if price is not None else None)
            )
            if gross is None:
                errors.append({"row": n, "message": "Needs a price or total value."})
                continue
            isin = get(row, "isin").strip().upper()
            isin = isin if _ISIN.match(isin) else None
            symbol = symbol_raw.upper() if len(symbol_raw) <= 20 else symbol_raw
            key = isin or re.sub(r"\s+", " ", symbol_raw.strip().upper())
            value = gross + fees if side == "BUY" else gross - fees
            legs.append(
                _leg(date, "equity", key, symbol, isin, side, qty, value, n, import_id, fees)
            )
    return legs, errors


def _leg(date, asset_class, key, symbol, isin, side, qty, value, row, import_id, fees_raw):
    return {
        "date": date,
        "asset_class": asset_class,
        "key": key,
        "symbol": symbol,
        "isin": isin,
        "side": side,
        "quantity": str(qty),
        "value_inr": str(value),
        "fees_inr": str(fees_raw),
        "row": row,
        "import_id": import_id,
    }
