"""Start the concurrency executor after applying gevent's process-wide patch."""

from __future__ import annotations

from gevent import monkey

monkey.patch_all()

import json
import os
import sys
from pathlib import Path

from evals.executors.concurrency import run_locust


def main() -> None:
    source, destination = map(Path, sys.argv[1:3])
    workload = json.loads(source.read_text(encoding="utf-8"))
    payload = run_locust(workload, os.environ["RESOLVEAI_LOAD_TOKEN"])
    destination.write_text(json.dumps(payload), encoding="utf-8")


if __name__ == "__main__":
    main()
