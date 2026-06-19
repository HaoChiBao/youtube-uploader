"""Uvicorn entry point: uploader-api"""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    host = os.environ.get("UPLOADER_API_HOST", "127.0.0.1")
    port = int(os.environ.get("UPLOADER_API_PORT", "8000"))
    uvicorn.run("api.app:app", host=host, port=port, reload=os.environ.get("UPLOADER_API_RELOAD") == "1")


if __name__ == "__main__":
    main()
