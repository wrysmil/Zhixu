"""从宿主侧检查 qinglu Q7 健康端点。"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request

CHECK_HOST = os.environ.get("Q7_CHECK_HOST", "127.0.0.1")
CHECK_PORT = os.environ.get("Q7_CHECK_PORT", "8137")
URL = f"http://{CHECK_HOST}:{CHECK_PORT}/ready-q7"


def main() -> int:
    try:
        with urllib.request.urlopen(URL, timeout=3) as response:
            body = response.read().decode("utf-8")
            print(f"{response.status} {body}")
            return 0 if response.status == 200 else 1
    except (urllib.error.URLError, OSError) as exc:
        print(f"probe failed: {URL}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
