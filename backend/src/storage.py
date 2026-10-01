"""
Storage.

AWS mode (default):
  S3 bucket   workspaces/{ws}/imports/{id}/raw.csv      original upload
              workspaces/{ws}/imports/{id}/trades.json  normalized legs
  DynamoDB    PK=WS#{ws}   SK=IMPORT#{id}   import metadata (TTL: expiresAt)
              PK=TEMPLATE  SK={fingerprint} confirmed column mapping (no user data)

Local mode (LOCAL_MODE=1): same layout on disk under LOCAL_DATA_DIR, for
running and testing without an AWS account.

Workspace data expires after DATA_RETENTION_DAYS (DynamoDB TTL + S3 lifecycle).
"""

import json
import os
import time
from decimal import Decimal
from pathlib import Path

RETENTION_DAYS = int(os.environ.get("DATA_RETENTION_DAYS", "14"))
LOCAL = os.environ.get("LOCAL_MODE") == "1"


def _now():
    return int(time.time())


def _expiry():
    return _now() + RETENTION_DAYS * 86400


def _plain(obj):
    """DynamoDB returns Decimals; convert to JSON-friendly types."""
    if isinstance(obj, list):
        return [_plain(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    return obj


def _ddb_safe(obj):
    """DynamoDB rejects floats; store them as Decimal."""
    return json.loads(json.dumps(obj), parse_float=Decimal)


class AwsStore:
    def __init__(self):
        import boto3

        self.s3 = boto3.client("s3")
        self.bucket = os.environ["BUCKET_NAME"]
        self.table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])

    def _key(self, ws, import_id, name):
        return f"workspaces/{ws}/imports/{import_id}/{name}"

    def put_file(self, ws, import_id, name, body):
        self.s3.put_object(Bucket=self.bucket, Key=self._key(ws, import_id, name), Body=body.encode("utf-8"))

    def get_file(self, ws, import_id, name):
        obj = self.s3.get_object(Bucket=self.bucket, Key=self._key(ws, import_id, name))
        return obj["Body"].read().decode("utf-8")

    def put_import(self, ws, import_id, item):
        item = dict(item, PK=f"WS#{ws}", SK=f"IMPORT#{import_id}", expiresAt=_expiry())
        self.table.put_item(Item=_ddb_safe(item))

    def get_import(self, ws, import_id):
        r = self.table.get_item(Key={"PK": f"WS#{ws}", "SK": f"IMPORT#{import_id}"})
        return _plain(r.get("Item")) if r.get("Item") else None

    def list_imports(self, ws):
        from boto3.dynamodb.conditions import Key

        items, kwargs = [], {"KeyConditionExpression": Key("PK").eq(f"WS#{ws}") & Key("SK").begins_with("IMPORT#")}
        while True:
            r = self.table.query(**kwargs)
            items += r.get("Items", [])
            if "LastEvaluatedKey" not in r:
                break
            kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
        return [_plain(i) for i in items if i.get("expiresAt", 0) > _now()]

    def delete_import(self, ws, import_id):
        self.table.delete_item(Key={"PK": f"WS#{ws}", "SK": f"IMPORT#{import_id}"})
        prefix = f"workspaces/{ws}/imports/{import_id}/"
        r = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
        objs = [{"Key": o["Key"]} for o in r.get("Contents", [])]
        if objs:
            self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": objs})

    def get_template(self, fp):
        r = self.table.get_item(Key={"PK": "TEMPLATE", "SK": fp})
        return _plain(r["Item"]["mapping"]) if r.get("Item") else None

    def put_template(self, fp, mapping, headers):
        self.table.update_item(
            Key={"PK": "TEMPLATE", "SK": fp},
            UpdateExpression="SET mapping = :m, headers = :h, updatedAt = :t ADD uses :one",
            ExpressionAttributeValues={":m": _ddb_safe(mapping), ":h": headers, ":t": _now(), ":one": 1},
        )


class LocalStore:
    def __init__(self):
        self.root = Path(os.environ.get("LOCAL_DATA_DIR", "./.localdata"))
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, ws, import_id):
        d = self.root / "workspaces" / ws / "imports" / import_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def put_file(self, ws, import_id, name, body):
        (self._dir(ws, import_id) / name).write_text(body, encoding="utf-8")

    def get_file(self, ws, import_id, name):
        return (self._dir(ws, import_id) / name).read_text(encoding="utf-8")

    def put_import(self, ws, import_id, item):
        (self._dir(ws, import_id) / "meta.json").write_text(json.dumps(dict(item, expiresAt=_expiry())))

    def get_import(self, ws, import_id):
        p = self.root / "workspaces" / ws / "imports" / import_id / "meta.json"
        return json.loads(p.read_text()) if p.exists() else None

    def list_imports(self, ws):
        base = self.root / "workspaces" / ws / "imports"
        if not base.exists():
            return []
        return [json.loads((d / "meta.json").read_text()) for d in base.iterdir() if (d / "meta.json").exists()]

    def delete_import(self, ws, import_id):
        import shutil

        shutil.rmtree(self.root / "workspaces" / ws / "imports" / import_id, ignore_errors=True)

    def _tpath(self, fp):
        d = self.root / "templates"
        d.mkdir(exist_ok=True)
        return d / f"{fp}.json"

    def get_template(self, fp):
        p = self._tpath(fp)
        return json.loads(p.read_text())["mapping"] if p.exists() else None

    def put_template(self, fp, mapping, headers):
        self._tpath(fp).write_text(json.dumps({"mapping": mapping, "headers": headers}))


_store = None


def store():
    global _store
    if _store is None:
        _store = LocalStore() if LOCAL else AwsStore()
    return _store
