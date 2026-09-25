from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("LAZY_FISH_HOST", "127.0.0.1")
    port = int(os.environ.get("LAZY_FISH_PORT", "8765"))
    uvicorn.run("xyzw_auto_clicker.app:app", host=host, port=port, reload=False, access_log=False)


if __name__ == "__main__":
    main()

