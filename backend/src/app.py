"""
Lotwise API (single Lambda behind an API Gateway HTTP API, payload v2).

Workspaces are anonymous: the browser generates a random UUID and sends it
with each request. There are no accounts; data auto-expires.

Routes
  GET    /health
  POST   /imports                       {workspaceId, filename, content}  -> mapping proposal
  POST   /imports/{id}/preview          {workspaceId, mapping}            -> re-validate an edited mapping
  POST   /imports/{id}/confirm          {workspaceId, mapping}            -> normalize + save
  GET    /imports?workspaceId=
  DELETE /imports/{id}?workspaceId=
  GET    /report?workspaceId=&fy=
  DELETE /workspace?workspaceId=
"""

import base64
import json
import re
import time
import traceback
import uuid

from engine import build_report
from mapper import fingerprint, parse_csv, propose_mapping, validate_mapping
from normalize import normalize_rows
from storage import store

MAX_UPLOAD_CHARS = 3_000_000
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_IMPORT_ID = re.compile(r"^[0-9a-f]{12}$")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def _resp(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def _body(event):
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise ApiError(400, "Request body must be JSON.")


def _ws(value):
    if not isinstance(value, str) or not _UUID.match(value):
        raise ApiError(400, "Missing or invalid workspaceId.")
    return value


def _import_id(value):
    if not _IMPORT_ID.match(value or ""):
        raise ApiError(404, "Import not found.")
    return value


def _review_payload(headers, rows, mapping):
    legs, errors = normalize_rows(headers, rows, mapping)
    return {
        "preview": legs[:8],
        "rowCount": len(rows),
        "legCount": len(legs),
        "errorCount": len(errors),
        "errors": errors[:8],
    }


# ------------------------------------------------------------------ handlers


def create_import(body):
    ws = _ws(body.get("workspaceId"))
    content = body.get("content")
    filename = str(body.get("filename") or "upload.csv")[:120]
    if not isinstance(content, str) or not content.strip():
        raise ApiError(400, "The file is empty.")
    if len(content) > MAX_UPLOAD_CHARS:
        raise ApiError(413, "This file is over 3 MB. Split it by year and upload each part.")
    try:
        headers, rows = parse_csv(content)
    except ValueError as e:
        raise ApiError(422, str(e))

    fp = fingerprint(headers)
    template = store().get_template(fp)
    mapping, source, notes = propose_mapping(headers, rows, template)

    import_id = uuid.uuid4().hex[:12]
    s = store()
    s.put_file(ws, import_id, "raw.csv", content)
    s.put_import(ws, import_id, {
        "importId": import_id,
        "filename": filename,
        "status": "pending",
        "fingerprint": fp,
        "source": source,
        "mapping": mapping,
        "createdAt": int(time.time()),
    })
    return {
        "importId": import_id,
        "filename": filename,
        "source": source,
        "sourceNotes": notes,
        "headers": headers,
        "sampleRows": rows[:5],
        "mapping": mapping,
        **_review_payload(headers, rows, mapping),
    }


def _load_pending(ws, import_id):
    meta = store().get_import(ws, import_id)
    if not meta:
        raise ApiError(404, "Import not found. It may have expired; upload the file again.")
    headers, rows = parse_csv(store().get_file(ws, import_id, "raw.csv"))
    return meta, headers, rows


def preview_import(import_id, body):
    ws = _ws(body.get("workspaceId"))
    _, headers, rows = _load_pending(ws, import_id)
    mapping = validate_mapping(body.get("mapping"), headers)
    return {"mapping": mapping, **_review_payload(headers, rows, mapping)}


def confirm_import(import_id, body):
    ws = _ws(body.get("workspaceId"))
    meta, headers, rows = _load_pending(ws, import_id)
    mapping = validate_mapping(body.get("mapping"), headers)
    legs, errors = normalize_rows(headers, rows, mapping, import_id=import_id)
    if not legs:
        msg = errors[0]["message"] if errors else "No trades could be read with this mapping."
        raise ApiError(422, msg)

    s = store()
    s.put_file(ws, import_id, "trades.json", json.dumps(legs))
    # A user-confirmed mapping becomes a shared template for this file layout.
    # Only column names and side labels are stored, never trade data.
    template = {k: mapping[k] for k in ("columns", "asset_class", "day_first", "side_values")}
    s.put_template(meta["fingerprint"], template, headers)
    meta.update({
        "status": "confirmed",
        "mapping": mapping,
        "assetClass": mapping["asset_class"],
        "legCount": len(legs),
        "errorCount": len(errors),
        "confirmedAt": int(time.time()),
    })
    for k in ("PK", "SK", "expiresAt"):
        meta.pop(k, None)
    s.put_import(ws, import_id, meta)
    return {"importId": import_id, "legCount": len(legs), "errorCount": len(errors), "errors": errors[:20]}


def list_imports(ws):
    items = store().list_imports(_ws(ws))
    out = [
        {k: i.get(k) for k in ("importId", "filename", "status", "source", "assetClass", "legCount", "errorCount", "createdAt")}
        for i in items
    ]
    return {"imports": sorted(out, key=lambda i: i.get("createdAt") or 0)}


def delete_import(ws, import_id):
    store().delete_import(_ws(ws), import_id)
    return {"deleted": import_id}


def delete_workspace(ws):
    ws = _ws(ws)
    for i in store().list_imports(ws):
        store().delete_import(ws, i["importId"])
    return {"deleted": True}


def report(ws, fy):
    ws = _ws(ws)
    legs = []
    for i in store().list_imports(ws):
        if i.get("status") == "confirmed":
            legs += json.loads(store().get_file(ws, i["importId"], "trades.json"))
    if fy is not None and not re.match(r"^\d{4}-\d{2}$", fy):
        raise ApiError(400, "fy must look like 2025-26.")
    if not legs:
        return {"fy": None, "years": [], "summary": None, "transfers": [], "holdings": [], "warnings": [], "empty": True}
    return build_report(legs, fy)


# -------------------------------------------------------------------- router


def handler(event, context=None):
    try:
        method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
        path = event.get("rawPath") or "/"
        q = event.get("queryStringParameters") or {}
        parts = [p for p in path.split("/") if p]

        if method == "OPTIONS":
            return _resp(204, {})
        if parts == ["health"]:
            return _resp(200, {"ok": True})
        if parts == ["imports"] and method == "POST":
            return _resp(200, create_import(_body(event)))
        if parts == ["imports"] and method == "GET":
            return _resp(200, list_imports(q.get("workspaceId")))
        if len(parts) == 3 and parts[0] == "imports" and method == "POST":
            iid = _import_id(parts[1])
            if parts[2] == "preview":
                return _resp(200, preview_import(iid, _body(event)))
            if parts[2] == "confirm":
                return _resp(200, confirm_import(iid, _body(event)))
        if len(parts) == 2 and parts[0] == "imports" and method == "DELETE":
            return _resp(200, delete_import(q.get("workspaceId"), _import_id(parts[1])))
        if parts == ["report"] and method == "GET":
            return _resp(200, report(q.get("workspaceId"), q.get("fy")))
        if parts == ["workspace"] and method == "DELETE":
            return _resp(200, delete_workspace(q.get("workspaceId")))
        raise ApiError(404, "Not found.")
    except ApiError as e:
        return _resp(e.status, {"error": e.message})
    except Exception:
        traceback.print_exc()
        return _resp(500, {"error": "Something went wrong on our side. Try again, and if it repeats, re-upload the file."})
