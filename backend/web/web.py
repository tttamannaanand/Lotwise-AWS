"""Serves the built website from the private web bucket through API Gateway.

Used instead of CloudFront, which new AWS accounts can't create until verified.
"""
import base64
import mimetypes
import os

import boto3

s3 = boto3.client("s3")
BUCKET = os.environ["WEB_BUCKET"]
TEXT_TYPES = ("text/", "application/javascript", "application/json", "image/svg+xml")


def handler(event, context=None):
    key = (event.get("rawPath") or "/").lstrip("/") or "index.html"
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
    except s3.exceptions.NoSuchKey:
        # SPA fallback: unknown paths get the app shell
        key = "index.html"
        obj = s3.get_object(Bucket=BUCKET, Key=key)
    except Exception:
        key = "index.html"
        obj = s3.get_object(Bucket=BUCKET, Key=key)
    ctype = obj.get("ContentType") or mimetypes.guess_type(key)[0] or "application/octet-stream"
    if ctype == "binary/octet-stream":
        ctype = mimetypes.guess_type(key)[0] or ctype
    body = obj["Body"].read()
    headers = {"Content-Type": ctype, "Cache-Control": obj.get("CacheControl") or "no-cache"}
    if ctype.startswith(TEXT_TYPES):
        return {"statusCode": 200, "headers": headers, "body": body.decode("utf-8")}
    return {"statusCode": 200, "headers": headers, "body": base64.b64encode(body).decode(), "isBase64Encoded": True}
