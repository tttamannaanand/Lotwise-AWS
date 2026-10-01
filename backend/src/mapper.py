"""
Column mapping: turn an unknown broker/exchange CSV into a mapping onto
Lotwise's canonical trade schema.

Design rule: the AI only proposes *which column means what*. It never sees the
whole file and never computes a number. Deterministic code (normalize.py)
applies the mapping and does all arithmetic.

Order of preference:
  1. A saved template whose header fingerprint matches (instant, no AI call)
  2. Amazon Bedrock proposal from headers + 5 sample rows
  3. Keyword heuristic (also used to fill required fields the AI left empty,
     and as a full fallback if Bedrock is disabled or fails)
"""

import csv
import hashlib
import io
import json
import os
import re

CANONICAL_FIELDS = ["date", "symbol", "isin", "side", "quantity", "price", "total_inr", "fees"]
REQUIRED_FIELDS = ["date", "symbol", "quantity"]  # plus price OR total_inr
ASSET_CLASSES = ["equity", "crypto"]
SAMPLE_ROWS_FOR_AI = 5
MAX_ROWS = 20000

FIELD_DESCRIPTIONS = {
    "date": "trade / transaction date (time may be included)",
    "symbol": "instrument: stock symbol, mutual fund scheme name, or crypto market/pair like BTC/INR",
    "isin": "12-character ISIN code (equity / mutual funds only)",
    "side": "buy or sell indicator (e.g. BUY/SELL, B/S, Purchase/Redemption)",
    "quantity": "number of shares, units or coins traded",
    "price": "price per unit (for crypto pairs, in the pair's quote currency)",
    "total_inr": "total trade value in Indian rupees (consideration / amount / INR value)",
    "fees": "brokerage, commission or trading fee (NOT STT, NOT GST if separate)",
}

# ---------------------------------------------------------------- CSV parsing


def parse_csv(text: str):
    """Return (headers, rows). Skips preamble lines some brokers add above the header."""
    text = text.lstrip("\ufeff")
    sample = text[:5000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","

    all_rows = [r for r in csv.reader(io.StringIO(text), delimiter=delimiter)]
    all_rows = [[c.strip() for c in r] for r in all_rows]
    all_rows = [r for r in all_rows if any(c for c in r)]
    if not all_rows:
        raise ValueError("The file is empty.")

    header_idx = _find_header_row(all_rows[:25])
    headers = _dedupe_headers(all_rows[header_idx])
    width = len(headers)
    rows = []
    for r in all_rows[header_idx + 1 : header_idx + 1 + MAX_ROWS]:
        if len([c for c in r if c]) < 2:
            continue  # footer / subtotal noise
        r = (r + [""] * width)[:width]
        rows.append(r)
    if not rows:
        raise ValueError("Found a header row but no trade rows under it.")
    return headers, rows


def _find_header_row(rows):
    max_filled = max(len([c for c in r if c]) for r in rows)
    for i, r in enumerate(rows):
        filled = [c for c in r if c]
        if len(filled) < max(3, int(max_filled * 0.6)):
            continue
        alpha = sum(1 for c in filled if re.search(r"[A-Za-z]", c) and not _looks_numeric(c))
        if alpha >= len(filled) * 0.7:
            return i
    return 0


def _dedupe_headers(headers):
    seen, out = {}, []
    for i, h in enumerate(headers):
        h = h or f"Column {i + 1}"
        if h in seen:
            seen[h] += 1
            h = f"{h} ({seen[h]})"
        else:
            seen[h] = 1
        out.append(h)
    return out


def _looks_numeric(s: str) -> bool:
    return bool(re.fullmatch(r"[\s₹$(),.\-+0-9]+", s))


def fingerprint(headers) -> str:
    norm = sorted(re.sub(r"[^a-z0-9]", "", h.lower()) for h in headers)
    return hashlib.sha256("|".join(norm).encode()).hexdigest()[:32]


# ------------------------------------------------------------------ heuristic

_SYNONYMS = {
    "date": ["tradedate", "transactiondate", "date", "orderexecutiontime", "executiontime", "time", "datetime", "timestamp", "createdat"],
    "symbol": ["symbol", "scrip", "tradingsymbol", "instrument", "stock", "scheme", "schemename", "fundname", "market", "pair", "coin", "asset", "security", "name"],
    "isin": ["isin", "isincode"],
    "side": ["tradetype", "side", "type", "buysell", "transactiontype", "action", "direction", "ordertype"],
    "quantity": ["quantity", "qty", "units", "executedqty", "filledqty", "volume", "shares", "amountcoin", "noofunits"],
    "price": ["price", "tradeprice", "avgprice", "averageprice", "rate", "nav", "executedprice", "unitprice"],
    "total_inr": ["inrvalue", "amountinr", "valueinr", "totalinr", "amount", "netamount", "value", "tradevalue", "consideration", "total", "grossamount"],
    "fees": ["brokerage", "fee", "fees", "commission", "tradingfee", "charges"],
}


def _norm(h):
    return re.sub(r"[^a-z0-9]", "", h.lower())


def heuristic_mapping(headers, rows):
    columns, confidence = {f: None for f in CANONICAL_FIELDS}, {f: 0.0 for f in CANONICAL_FIELDS}
    used = set()
    for field in CANONICAL_FIELDS:
        best, best_score = None, 0.0
        for h in headers:
            if h in used:
                continue
            n = _norm(h)
            for rank, syn in enumerate(_SYNONYMS[field]):
                score = 0.0
                if n == syn:
                    score = 0.9 - rank * 0.01
                elif syn in n and len(syn) >= 3:
                    score = 0.6 - rank * 0.01
                if field == "fees" and ("stt" in n or "gst" in n):
                    score = 0
                if score > best_score:
                    best, best_score = h, score
        if best:
            columns[field], confidence[field] = best, round(best_score, 2)
            used.add(best)

    sym_col = columns["symbol"]
    sym_values = [r[headers.index(sym_col)] for r in rows[:50]] if sym_col else []
    asset_class = guess_asset_class(sym_values, has_isin=columns["isin"] is not None)

    date_values = [r[headers.index(columns["date"])] for r in rows[:50]] if columns["date"] else []
    side_values = {"buy": [], "sell": []}
    if columns["side"]:
        idx = headers.index(columns["side"])
        for v in {r[idx] for r in rows[:500] if r[idx]}:
            s = classify_side(v)
            if s:
                side_values[s].append(v)

    return {
        "columns": columns,
        "confidence": confidence,
        "asset_class": asset_class,
        "day_first": detect_day_first(date_values),
        "side_values": side_values,
        "notes": [],
    }


_CRYPTO_TICKERS = {"BTC", "ETH", "USDT", "USDC", "SOL", "XRP", "DOGE", "MATIC", "POL", "BNB", "ADA", "DOT", "SHIB", "TRX", "LTC", "AVAX", "LINK"}


def guess_asset_class(symbol_values, has_isin=False):
    if has_isin:
        return "equity"
    hits = 0
    for v in symbol_values:
        u = v.upper()
        parts = re.split(r"[/\-_:]", u)
        if any(p in _CRYPTO_TICKERS for p in parts) or re.search(r"(INR|USDT|USDC)$", u):
            hits += 1
    return "crypto" if symbol_values and hits >= len(symbol_values) * 0.5 else "equity"


_BUY_WORDS = ["buy", "purchase", "bought", "sip", "acquire", "switch in", "switch-in"]
_SELL_WORDS = ["sell", "sale", "sold", "redemption", "redeem", "switch out", "switch-out", "dispose"]


def classify_side(value):
    v = value.strip().lower()
    if v in ("b", "buy", "bid", "long"):
        return "buy"
    if v in ("s", "sell", "ask"):
        return "sell"
    if any(w in v for w in _SELL_WORDS):
        return "sell"
    if any(w in v for w in _BUY_WORDS):
        return "buy"
    return None


def detect_day_first(values):
    """True if dates look DD/MM, False if MM/DD. Defaults to True (Indian convention)."""
    for v in values:
        m = re.match(r"\s*(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})", v)
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12:
            return True
        if b > 12:
            return False
    return True


# -------------------------------------------------------------------- Bedrock

SYSTEM_PROMPT = """You map columns of a trade history CSV (from an Indian stock broker, mutual fund statement, or crypto exchange) onto a fixed schema.

Canonical fields:
{fields}

Rules:
- Use ONLY header names that appear exactly in the provided header list, or null if no column fits.
- Never map one header to two fields.
- "fees" must be brokerage/commission only. Never map STT, GST, stamp duty or exchange turnover charges to fees.
- If a column holds the total rupee value of the trade, map it to total_inr. For crypto pairs not quoted in INR (e.g. ETH/USDT), total_inr is essential.
- asset_class is "equity" for stocks, ETFs and mutual funds, "crypto" for virtual digital assets.
- day_first is true if dates are written day-before-month (e.g. 03/04/2025 = 3 April).
- side_values lists the exact raw values from the side column meaning buy and sell. If there is no side column, use empty lists.
- confidence is 0 to 1 per field. Use below 0.8 when you are guessing.
- notes: at most 3 short, plain-English observations a user should check (e.g. "Amount column appears to include taxes").

Reply with ONLY a JSON object, no prose, no code fences:
{{"columns": {{"date": ..., "symbol": ..., "isin": ..., "side": ..., "quantity": ..., "price": ..., "total_inr": ..., "fees": ...}},
 "confidence": {{"date": 0.0, ...}},
 "asset_class": "equity" | "crypto",
 "day_first": true | false,
 "side_values": {{"buy": [...], "sell": [...]}},
 "notes": [...]}}"""


def bedrock_mapping(headers, rows):
    """Ask Bedrock for a mapping. Returns (mapping, None) or (None, error_message)."""
    model_id = os.environ.get("BEDROCK_MODEL_ID", "").strip()
    if os.environ.get("BEDROCK_ENABLED", "true").lower() != "true" or not model_id:
        return None, "AI mapping is turned off in this environment."
    try:
        import boto3  # available in the Lambda runtime

        client = boto3.client("bedrock-runtime")
        # Distinct side values help the model; send only headers + a few rows.
        payload = {
            "headers": headers,
            "sample_rows": rows[:SAMPLE_ROWS_FOR_AI],
        }
        system = SYSTEM_PROMPT.format(fields="\n".join(f"- {k}: {v}" for k, v in FIELD_DESCRIPTIONS.items()))
        resp = client.converse(
            modelId=model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": json.dumps(payload, ensure_ascii=False)}]}],
            inferenceConfig={"maxTokens": 1200, "temperature": 0},
        )
        text = "".join(block.get("text", "") for block in resp["output"]["message"]["content"])
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < 0:
            return None, "The AI reply did not contain a mapping."
        return validate_mapping(json.loads(text[start : end + 1]), headers), None
    except Exception as e:  # network, permissions, model access, bad JSON
        return None, f"AI mapping unavailable ({type(e).__name__}). Used keyword matching instead."


# ----------------------------------------------------------------- validation


def validate_mapping(raw, headers):
    """Coerce any mapping (AI output or user edit) into a safe, well-formed shape."""
    raw = raw if isinstance(raw, dict) else {}
    cols_in = raw.get("columns") or {}
    conf_in = raw.get("confidence") or {}
    columns, confidence, used = {}, {}, set()
    for f in CANONICAL_FIELDS:
        h = cols_in.get(f)
        if isinstance(h, str) and h in headers and h not in used:
            columns[f] = h
            used.add(h)
        else:
            columns[f] = None
        try:
            c = float(conf_in.get(f, 0.0 if columns[f] is None else 0.7))
        except (TypeError, ValueError):
            c = 0.5
        confidence[f] = max(0.0, min(1.0, c))

    asset_class = raw.get("asset_class") if raw.get("asset_class") in ASSET_CLASSES else "equity"
    day_first = raw.get("day_first") if isinstance(raw.get("day_first"), bool) else True
    sv = raw.get("side_values") or {}
    side_values = {
        k: [str(v) for v in (sv.get(k) or []) if isinstance(v, (str, int, float))][:50] for k in ("buy", "sell")
    }
    notes = [str(n)[:200] for n in (raw.get("notes") or []) if isinstance(n, str)][:3]
    return {
        "columns": columns,
        "confidence": confidence,
        "asset_class": asset_class,
        "day_first": day_first,
        "side_values": side_values,
        "notes": notes,
    }


def fill_gaps(primary, fallback):
    """Fill required fields the primary mapping left empty, using the fallback at low confidence."""
    out = json.loads(json.dumps(primary))
    used = {v for v in out["columns"].values() if v}
    needed = list(REQUIRED_FIELDS)
    if not out["columns"]["price"] and not out["columns"]["total_inr"]:
        needed += ["price", "total_inr"]
    for f in needed:
        if not out["columns"][f] and fallback["columns"].get(f) and fallback["columns"][f] not in used:
            out["columns"][f] = fallback["columns"][f]
            out["confidence"][f] = min(0.5, fallback["confidence"].get(f, 0.5))
            used.add(out["columns"][f])
    for k in ("buy", "sell"):
        if not out["side_values"][k]:
            out["side_values"][k] = fallback["side_values"][k]
    return out


def propose_mapping(headers, rows, template=None):
    """Returns (mapping, source, notes). source is 'template', 'ai' or 'keywords'."""
    heuristic = heuristic_mapping(headers, rows)
    if template:
        m = validate_mapping(template, headers)
        m["confidence"] = {f: (1.0 if m["columns"][f] else 0.0) for f in CANONICAL_FIELDS}
        return fill_gaps(m, heuristic), "template", []
    ai, err = bedrock_mapping(headers, rows)
    if ai:
        return fill_gaps(ai, heuristic), "ai", []
    return heuristic, "keywords", [err] if err else []
