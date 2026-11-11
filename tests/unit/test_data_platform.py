"""Version checks, atomic rebuild, and authenticated read-only serving."""

import json
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from data_platform.api import app
from data_platform.cli import main as platform_main
from data_platform.core import quality_errors, validate_sources
from data_platform.ingestions import list_ingestions
from data_platform.rebuild import rebuild_database
from data_platform.runs import get_run, list_runs, record_run
from evals.runners.import_retail_json import import_json
from services.commerce_service.app.models import Base


def test_pinned_sources_and_bad_references() -> None:
    assert validate_sources()["counts"]["test_tasks"] == 40
    root = Path(__file__).resolve().parents[2] / "evals/datasets"
    database = json.loads((root / "db.json").read_text())
    tasks = json.loads((root / "tasks.json").read_text())
    splits = json.loads((root / "split_tasks.json").read_text())
    broken = deepcopy(database)
    order = next(iter(broken["orders"].values()))
    order["user_id"] = "missing-user"
    assert any("missing user" in error for error in quality_errors(broken, tasks, splits))


def test_rebuild_restores_seed_and_clears_new_rows(tmp_path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'commerce.sqlite'}")
    Base.metadata.create_all(engine)
    source = Path(__file__).resolve().parents[2] / "evals/datasets/db.json"
    import_json(engine, source)
    products = Base.metadata.tables["products"]
    with engine.begin() as connection:
        first = connection.execute(select(products.c.product_id).limit(1)).scalar_one()
        connection.execute(products.update().where(products.c.product_id == first).values(name="Changed"))
    counts = rebuild_database(engine)
    assert counts["products"] == 50
    with engine.connect() as connection:
        assert connection.execute(select(products.c.name).where(products.c.product_id == first)).scalar_one() != "Changed"
    engine.dispose()


def test_rebuild_cli_requires_matching_database_and_apply(tmp_path) -> None:
    path = tmp_path / "commerce.sqlite"
    url = f"sqlite+pysqlite:///{path}"
    assert platform_main([
        "rebuild", "--database-url", url, "--confirm-database", str(path)
    ]) == 0
    assert not path.exists()


def test_ingest_records_versioned_receipt(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_PLATFORM_INGESTIONS_DIR", str(tmp_path / "ingestions"))
    path = tmp_path / "commerce.sqlite"
    url = f"sqlite+pysqlite:///{path}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    engine.dispose()
    assert platform_main(["ingest", "--database-url", url]) == 0
    receipts = list_ingestions()
    assert len(receipts) == 1
    assert receipts[0]["dataset_version"] == "retail-bench-v1"
    assert receipts[0]["counts"]["orders"] == 1000
    assert receipts[0]["database"] == str(path)


def test_run_registry_and_api_are_read_only_and_authenticated(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_PLATFORM_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("AGENT_INTERNAL_TOKEN", "test-token")
    source = Path(__file__).resolve().parents[2] / "evals/datasets"
    rows = [{"task_id": "12", "status": "SUCCEEDED", "scores": {"task_success": 1, "trajectory": 0.5}, "error": None}]
    first = record_run(rows, task_path=source / "tasks.json", database_path=source / "db.json", split="test", output=tmp_path / "out.json")
    second = record_run(rows, task_path=source / "tasks.json", database_path=source / "db.json", split="test", output=tmp_path / "out2.json")
    assert get_run(first["run_id"]) == first
    assert len(list_runs()) == 2
    client = TestClient(app)
    assert client.get("/datasets").status_code == 401
    headers = {"X-Agent-Token": "test-token"}
    assert client.get("/datasets", headers=headers).json()["version"] == "retail-bench-v1"
    assert len(client.get("/runs", headers=headers).json()) == 2
    comparison = client.get(f"/runs/compare?left={first['run_id']}&right={second['run_id']}", headers=headers).json()
    assert comparison["same_dataset"]
    assert comparison["score_delta"]["task_success"] == 0
    assert client.get("/runs/not-a-uuid", headers=headers).status_code == 404
