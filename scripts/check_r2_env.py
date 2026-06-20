"""Print masked R2 env vars and test connectivity (same load path as uploader-api)."""

from __future__ import annotations

import os
import re
import sys

from dotenv import find_dotenv, load_dotenv


def mask(value: str) -> str:
    if not value:
        return "(not set)"
    if len(value) <= 8:
        return value[:2] + "***"
    return f"{value[:4]}...{value[-4:]}  (len={len(value)})"


def check(value: str) -> list[str]:
    issues: list[str] = []
    if not value:
        issues.append("MISSING")
        return issues
    if value != value.strip():
        issues.append("HAS_LEADING_OR_TRAILING_WHITESPACE")
    if value[0] in "\"'" or value[-1] in "\"'":
        issues.append("HAS_QUOTES")
    if "\n" in value or "\r" in value:
        issues.append("HAS_NEWLINE")
    return issues


def main() -> int:
    dotenv_path = find_dotenv(usecwd=True)
    print("=== dotenv (same as api/deps.py) ===")
    print(f"find_dotenv(usecwd=True): {dotenv_path or '(not found)'}")
    print(f"cwd: {os.getcwd()}")
    print(f"load_dotenv returned: {load_dotenv(find_dotenv(usecwd=True))}")

    r2_vars = [
        "UPLOADER_CONFIG",
        "CLOUDFLARE_R2_BUCKET",
        "CLOUDFLARE_R2_ENDPOINT_URL",
        "CLOUDFLARE_R2_REGION",
        "CLOUDFLARE_R2_ACCESS_KEY_ID",
        "CLOUDFLARE_R2_SECRET_ACCESS_KEY",
        "CLOUDFLARE_R2_ACCOUNT_ID",
        "CLOUDFLARE_R2_API_TOKEN",
    ]

    print("\n=== os.environ ===")
    for name in r2_vars:
        raw = os.environ.get(name, "")
        issues = check(raw)
        if name in (
            "CLOUDFLARE_R2_BUCKET",
            "CLOUDFLARE_R2_ENDPOINT_URL",
            "CLOUDFLARE_R2_REGION",
            "UPLOADER_CONFIG",
        ):
            display = raw or "(not set)"
        else:
            display = mask(raw)
        suffix = f"  !!! {', '.join(issues)}" if issues else ""
        print(f"  {name}={display}{suffix}")

    from uploader.object_storage import _env, storage_bucket, _s3_client

    print("\n=== object_storage helpers ===")
    print(f"  storage_bucket() -> {storage_bucket()!r}")
    endpoint = _env("CLOUDFLARE_R2_ENDPOINT_URL")
    print(f"  _env(CLOUDFLARE_R2_ENDPOINT_URL) -> {endpoint!r}")

    acct_env = os.environ.get("CLOUDFLARE_R2_ACCOUNT_ID", "")
    match = re.search(r"https://([a-f0-9]+)\.r2\.cloudflarestorage\.com", endpoint or "")
    if match:
        eid = match.group(1)
        status = "OK" if eid == acct_env else f"MISMATCH (env has {acct_env!r})"
        print(f"  account id in endpoint: {eid} ({status})")
    if endpoint and endpoint.rstrip("/") != endpoint:
        print("  WARNING: endpoint has trailing slash")
    if endpoint and endpoint.count("/") > 3:
        print("  WARNING: endpoint should NOT include bucket name in the path")

    api_token = os.environ.get("CLOUDFLARE_R2_API_TOKEN", "")
    if api_token.startswith("cfat_"):
        print("  NOTE: CLOUDFLARE_R2_API_TOKEN (cfat_*) is NOT used by uploader — only ACCESS_KEY_ID + SECRET")

    print("\n=== R2 connectivity ===")
    from botocore.exceptions import ClientError

    bucket = storage_bucket()
    client = _s3_client()
    tests = [
        ("GET config/channels.yaml", lambda: client.get_object(Bucket=bucket, Key="config/channels.yaml")),
        (
            "GET state/mmmactually/upload_registry.txt",
            lambda: client.get_object(Bucket=bucket, Key="state/mmmactually/upload_registry.txt"),
        ),
        ("PUT _diag/test.txt", lambda: client.put_object(Bucket=bucket, Key="_diag/test.txt", Body=b"ok")),
    ]
    for label, fn in tests:
        try:
            result = fn()
            if label.startswith("GET"):
                size = len(result["Body"].read())
                print(f"  {label}: OK ({size} bytes)")
            else:
                print(f"  {label}: OK")
        except ClientError as exc:
            err = exc.response.get("Error", {})
            print(f"  {label}: FAIL {err.get('Code')} — {err.get('Message')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
