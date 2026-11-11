"""Validate, pin, ingest, and rebuild versioned benchmark data."""

from __future__ import annotations

import argparse
import json
import os

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from data_platform.core import MANIFEST, catalog, sha256, source_path, validate_sources
from data_platform.ingestions import record_ingestion
from data_platform.rebuild import rebuild_database
from evals.runners.import_retail_json import import_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="check source quality and pinned checksums")
    pin = commands.add_parser("pin", help="pin validated sources as a new dataset version")
    pin.add_argument("--version", required=True)
    ingest = commands.add_parser("ingest", help="import into empty Commerce tables")
    ingest.add_argument("--database-url", help="defaults to DATABASE_URL")
    reset = commands.add_parser("rebuild", help="replace all Commerce rows in one transaction")
    reset.add_argument("--database-url", required=True)
    reset.add_argument("--confirm-database", required=True, help="exact target database name or path")
    reset.add_argument("--apply", action="store_true", help="required to execute deletion and import")
    args = parser.parse_args(argv)

    if args.command == "pin":
        validate_sources(verify_manifest=False)
        if not args.version.strip():
            parser.error("version must be nonempty")
        previous = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if args.version == previous["version"]:
            parser.error("new version must differ from the current version")
        pinned = {
            "version": args.version,
            "sources": {name: {"sha256": sha256(source_path(name))} for name in catalog()},
        }
        MANIFEST.write_text(json.dumps(pinned, indent=2) + "\n", encoding="utf-8")
        print(f"Pinned dataset {args.version}; commit the source files and manifest together")
        return 0

    summary = validate_sources()
    if args.command == "validate":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    database_url = args.database_url or os.getenv("DATABASE_URL")
    if not database_url:
        parser.error("provide --database-url or DATABASE_URL")
    target = make_url(database_url).database
    if not target:
        parser.error("database URL must name a database")
    if args.command == "rebuild":
        if args.confirm_database != target:
            parser.error("--confirm-database must exactly match the URL database name or path")
        if not args.apply:
            print(f"Dry run: would replace all Commerce rows in database {target!r} with {summary['version']}")
            return 0
        engine = create_engine(database_url)
        try:
            counts = rebuild_database(engine)
        finally:
            engine.dispose()
        record_ingestion(operation="rebuild", database=target, counts=counts, changed=True)
        print(f"Rebuilt {target!r} from {summary['version']}: {json.dumps(counts)}")
        return 0

    engine = create_engine(database_url)
    try:
        imported, counts = import_json(engine, source_path("retail"))
    finally:
        engine.dispose()
    record_ingestion(operation="ingest", database=target, counts=counts, changed=imported)
    print(json.dumps({"imported": imported, "version": summary["version"], "counts": counts}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
