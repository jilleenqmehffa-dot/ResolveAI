"""Apply repeatable network faults to the Agent -> Commerce HTTP proxy."""

from __future__ import annotations

import argparse
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROXY = "agent_commerce_http"


def call(api: str, path: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{api.rstrip('/')}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    with urlopen(request, timeout=5) as response:
        content = response.read()
    return json.loads(content) if content else {}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=("status", "reset", "latency", "timeout", "disconnect", "down"))
    parser.add_argument("--ms", type=int, default=1000, help="latency or timeout duration in milliseconds")
    parser.add_argument("--api", default="http://127.0.0.1:8474")
    args = parser.parse_args()
    if args.ms < 0:
        parser.error("--ms must be nonnegative")

    try:
        if args.scenario == "status":
            result = call(args.api, f"/proxies/{PROXY}")
        else:
            call(args.api, "/reset", {})
            if args.scenario == "down":
                result = call(args.api, f"/proxies/{PROXY}", {"enabled": False})
            elif args.scenario == "reset":
                result = call(args.api, f"/proxies/{PROXY}")
            else:
                kind = "reset_peer" if args.scenario == "disconnect" else args.scenario
                attribute = "latency" if kind == "latency" else "timeout"
                result = call(
                    args.api,
                    f"/proxies/{PROXY}/toxics",
                    {
                        "name": f"{kind}_downstream",
                        "type": kind,
                        "stream": "downstream",
                        "attributes": {attribute: 0 if kind == "reset_peer" else args.ms},
                    },
                )
    except (HTTPError, URLError, TimeoutError) as exc:
        parser.exit(1, f"Toxiproxy request failed: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
