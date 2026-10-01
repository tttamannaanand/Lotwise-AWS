import json
import os
import sys
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["LOCAL_MODE"] = "1"
os.environ["BEDROCK_ENABLED"] = "false"
os.environ["LOCAL_DATA_DIR"] = tempfile.mkdtemp()

from app import handler  # noqa: E402
from engine import _is_long_term, build_report  # noqa: E402
from mapper import fingerprint, heuristic_mapping, parse_csv, validate_mapping  # noqa: E402
from normalize import normalize_rows, parse_date, parse_number, split_pair  # noqa: E402
from tax_rules import fy_for_date  # noqa: E402

SAMPLES = ROOT.parent / "samples"


def load(name):
    return (SAMPLES / name).read_text(encoding="utf-8")


# ------------------------------------------------------------ parsing helpers


def test_parse_number_indian_grouping_and_symbols():
    assert parse_number("1,52,961.83") == Decimal("152961.83")
    assert parse_number("₹ 25,000.00") == Decimal("25000.00")
    assert parse_number("(1,200)") == Decimal("-1200")
    assert parse_number("0.00005BTC") == Decimal("0.00005")
    assert parse_number("") is None


def test_parse_date_formats():
    assert parse_date("10-Jan-2025") == "2025-01-10"
    assert parse_date("05/05/2025 10:12") == "2025-05-05"
    assert parse_date("03/04/2025", day_first=True) == "2025-04-03"
    assert parse_date("03/04/2025", day_first=False) == "2025-03-04"
    assert parse_date("2024-03-11T10:14:22") == "2024-03-11"


def test_split_pair():
    assert split_pair("BTC/INR") == ("BTC", "INR")
    assert split_pair("ETHUSDT") == ("ETH", "USDT")
    assert split_pair("SOL") == ("SOL", "INR")


def test_fy_and_holding_period():
    assert fy_for_date("2025-04-01") == "2025-26"
    assert fy_for_date("2026-03-31") == "2025-26"
    # exactly 12 months is NOT more than 12 months
    assert not _is_long_term("2025-03-10", "2026-03-10", 12)
    assert _is_long_term("2025-03-10", "2026-03-11", 12)


def test_fingerprint_ignores_order_and_case():
    assert fingerprint(["Date", "Qty"]) == fingerprint(["qty", "DATE"])


# -------------------------------------------------------------- heuristics


def test_heuristic_maps_all_three_samples():
    h, r = parse_csv(load("broker_tradebook.csv"))
    m = heuristic_mapping(h, r)
    assert m["columns"]["date"] == "trade_date"
    assert m["columns"]["fees"] is None  # STT must never be treated as brokerage
    assert m["asset_class"] == "equity"

    h, r = parse_csv(load("mf_statement.csv"))
    assert h[0] == "Scheme Name"  # preamble lines skipped
    m = heuristic_mapping(h, r)
    assert m["columns"]["total_inr"] == "Amount (₹)"
    assert "Redemption" in m["side_values"]["sell"]

    h, r = parse_csv(load("crypto_exchange.csv"))
    m = heuristic_mapping(h, r)
    assert m["asset_class"] == "crypto"
    assert m["columns"]["symbol"] == "Market"
    assert m["columns"]["total_inr"] == "INR Value"


def test_validate_mapping_drops_hallucinated_columns():
    m = validate_mapping({"columns": {"date": "Not A Header", "symbol": "Market"}}, ["Market", "Date"])
    assert m["columns"]["date"] is None
    assert m["columns"]["symbol"] == "Market"


# ------------------------------------------------------------------- engine


def legs_for(name):
    h, r = parse_csv(load(name))
    legs, errors = normalize_rows(h, r, heuristic_mapping(h, r), import_id=name)
    assert errors == []
    return legs


def test_equity_fifo_and_terms():
    rep = build_report(legs_for("broker_tradebook.csv"), "2025-26")
    kaveri = next(t for t in rep["transfers"] if t["symbol"] == "KAVERIPOWER")
    terms = [(p["term"], p["quantity"], p["gain"]) for p in kaveri["parts"]]
    assert terms == [("LT", "400", "173240.00"), ("ST", "100", "35750.00")]
    eq = rep["summary"]["equity"]
    assert eq["lt_gains"] == "288770.00"  # 173,240 + 115,530
    assert eq["st_losses"] == "23550.00"  # DECCANTEXT


def test_mutual_fund_redemption_splits_lots():
    rep = build_report(legs_for("mf_statement.csv"), "2025-26")
    (t,) = rep["transfers"]
    assert [p["term"] for p in t["parts"]] == ["LT", "LT", "LT", "ST", "ST"]


def test_crypto_losses_not_set_off_and_swaps_taxed():
    rep = build_report(legs_for("crypto_exchange.csv"), "2025-26")
    vda = rep["summary"]["vda"]
    # USDT disposal on ETH buy: 2940 USDT, cost 260,190, value 261,366 -> 1,176
    # BTC 28,500 ; SOL 34,500 ; ETH sale loss 39,720 (not offset)
    assert vda["gains"] == "64176.00"
    assert vda["losses_not_set_off"] == "39720.00"
    assert rep["summary"]["tax"]["vda"] == "19252.80"


def test_combined_tax_estimate():
    legs = legs_for("broker_tradebook.csv") + legs_for("mf_statement.csv") + legs_for("crypto_exchange.csv")
    rep = build_report(legs, "2025-26")
    s = rep["summary"]
    eq, tax = s["equity"], s["tax"]
    net_st, net_lt = Decimal(eq["net_st"]), Decimal(eq["net_lt_before_exemption"])
    assert eq["ltcg_exemption_used"] == "125000.00"
    expected = (net_st * Decimal("0.20") + (net_lt - 125000) * Decimal("0.125") + Decimal("64176") * Decimal("0.30")) * Decimal("1.04")
    assert abs(Decimal(tax["total"]) - expected) < Decimal("0.05")
    assert rep["years"] == ["2026-27", "2025-26"]


def test_unmatched_sale_warns():
    legs = [{"date": "2025-06-01", "asset_class": "equity", "key": "X", "symbol": "X", "isin": None, "side": "SELL",
             "quantity": "10", "value_inr": "1000", "fees_inr": "0", "row": 1, "import_id": "t"}]
    rep = build_report(legs, "2025-26")
    assert any(w["code"] == "unmatched" for w in rep["warnings"])
    assert rep["summary"]["equity"]["st_gains"] == "0.00"


def test_unconfigured_year_has_no_tax():
    legs = [
        {"date": "2023-06-01", "asset_class": "equity", "key": "X", "symbol": "X", "isin": None, "side": "BUY",
         "quantity": "10", "value_inr": "1000", "fees_inr": "0", "row": 1, "import_id": "t"},
        {"date": "2024-06-01", "asset_class": "equity", "key": "X", "symbol": "X", "isin": None, "side": "SELL",
         "quantity": "10", "value_inr": "2000", "fees_inr": "0", "row": 2, "import_id": "t"},
    ]
    rep = build_report(legs, "2024-25")
    assert rep["summary"]["tax"] is None


# -------------------------------------------------------------- API end-to-end


def call(method, path, body=None, query=None):
    r = handler({
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "body": json.dumps(body) if body is not None else None,
        "queryStringParameters": query,
    })
    return r["statusCode"], json.loads(r["body"])


def test_api_flow_and_template_reuse():
    ws = str(uuid.uuid4())
    for name in ("broker_tradebook.csv", "mf_statement.csv", "crypto_exchange.csv"):
        code, prop = call("POST", "/imports", {"workspaceId": ws, "filename": name, "content": load(name)})
        assert code == 200, prop
        assert prop["source"] == "keywords"
        assert prop["errorCount"] == 0
        code, conf = call("POST", f"/imports/{prop['importId']}/confirm", {"workspaceId": ws, "mapping": prop["mapping"]})
        assert code == 200 and conf["legCount"] > 0

    # Same layout again -> saved template, no AI or keyword guessing needed
    code, prop = call("POST", "/imports", {"workspaceId": ws, "filename": "again.csv", "content": load("crypto_exchange.csv")})
    assert prop["source"] == "template"

    code, rep = call("GET", "/report", query={"workspaceId": ws, "fy": "2025-26"})
    assert code == 200 and rep["summary"]["tax"]["total"]

    code, _ = call("DELETE", "/workspace", query={"workspaceId": ws})
    code, rep = call("GET", "/report", query={"workspaceId": ws})
    assert rep.get("empty")


def test_api_rejects_bad_workspace():
    code, body = call("GET", "/report", query={"workspaceId": "../../etc"})
    assert code == 400


def test_bedrock_path_with_simulated_model(monkeypatch):
    """Simulates a Bedrock Converse reply, including a hallucinated column and prose around the JSON."""
    import types

    import mapper

    reply = {
        "columns": {"date": "Date", "symbol": "Market", "isin": None, "side": "Side", "quantity": "Executed Qty",
                    "price": "Avg Price", "total_inr": "INR Value", "fees": "Made Up Column"},
        "confidence": {"date": 0.97, "symbol": 0.95, "side": 0.99, "quantity": 0.9, "price": 0.7, "total_inr": 0.92},
        "asset_class": "crypto", "day_first": True,
        "side_values": {"buy": ["BUY"], "sell": ["SELL"]},
        "notes": ["Fees are charged in different coins; they are not deductible for crypto."],
    }

    class FakeClient:
        def converse(self, **kwargs):
            assert "INR Value" in kwargs["messages"][0]["content"][0]["text"]
            return {"output": {"message": {"content": [{"text": "Here you go:\n" + json.dumps(reply)}]}}}

    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda name: FakeClient()))
    monkeypatch.setenv("BEDROCK_ENABLED", "true")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-model")

    h, r = parse_csv(load("crypto_exchange.csv"))
    m, source, notes = mapper.propose_mapping(h, r)
    assert source == "ai"
    assert m["columns"]["fees"] is None  # hallucinated header dropped
    assert m["confidence"]["price"] == 0.7  # low confidence preserved for the UI to flag
    legs, errors = normalize_rows(h, r, m)
    assert errors == [] and len(legs) == 10


def test_bedrock_failure_falls_back(monkeypatch):
    import types

    import mapper

    class Broken:
        def converse(self, **kwargs):
            raise RuntimeError("AccessDenied")

    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda name: Broken()))
    monkeypatch.setenv("BEDROCK_ENABLED", "true")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-model")
    h, r = parse_csv(load("broker_tradebook.csv"))
    m, source, notes = mapper.propose_mapping(h, r)
    assert source == "keywords" and notes and m["columns"]["quantity"] == "quantity"
