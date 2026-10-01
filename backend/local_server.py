"""
Run the API locally without AWS:

    cd backend
    python3 local_server.py            # http://localhost:8787

Uses local files instead of S3/DynamoDB and keyword matching instead of
Bedrock (set BEDROCK_ENABLED=true + AWS credentials to try Bedrock locally).
"""

import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

os.environ.setdefault("LOCAL_MODE", "1")
os.environ.setdefault("BEDROCK_ENABLED", "false")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from app import handler  # noqa: E402

PORT = int(os.environ.get("PORT", "8787"))


class Handler(BaseHTTPRequestHandler):
    def _run(self):
        url = urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else None
        event = {
            "rawPath": url.path,
            "queryStringParameters": dict(parse_qsl(url.query)) or None,
            "requestContext": {"http": {"method": self.command}},
            "body": body,
        }
        r = handler(event)
        self.send_response(r["statusCode"])
        for k, v in r["headers"].items():
            self.send_header(k, v)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        if r["statusCode"] != 204:
            self.wfile.write(r["body"].encode("utf-8"))

    do_GET = do_POST = do_DELETE = do_OPTIONS = _run

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.command} {self.path}\n")


if __name__ == "__main__":
    print(f"Lotwise API running on http://localhost:{PORT}")
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
