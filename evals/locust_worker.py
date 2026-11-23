"""Start the concurrency executor after applying gevent's process-wide patch."""

from __future__ import annotations

from gevent import monkey

monkey.patch_all()

import json
import os
import sys
from pathlib import Path

from gevent import sleep

from evals.executors.concurrency import run_locust


def main() -> None:
    source, destination = map(Path, sys.argv[1:3])
    workload = json.loads(source.read_text(encoding="utf-8"))
    if len(sys.argv) == 3:
        payload = run_locust(workload, os.environ["RESOLVEAI_LOAD_TOKEN"])
        destination.write_text(json.dumps(payload), encoding="utf-8")
        return

    port = int(sys.argv[3])
    release = Path(sys.argv[4])

    def publish_and_serve(payload: dict) -> None:
        pending = destination.with_suffix(".pending")
        pending.write_text(json.dumps(payload), encoding="utf-8")
        pending.replace(destination)
        while not release.exists():
            sleep(0.2)

    run_locust(workload, os.environ["RESOLVEAI_LOAD_TOKEN"], web_port=port,
               on_complete=publish_and_serve)


if __name__ == "__main__":
    main()
