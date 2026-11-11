"""Dataset manifest and quality rules shared by import, reset, and evaluation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CATALOG = Path(__file__).with_name("catalog.json")
MANIFEST = Path(__file__).with_name("manifest.json")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def catalog() -> dict[str, dict[str, Any]]:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def manifest() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def source_path(name: str) -> Path:
    return ROOT / catalog()[name]["source"]


def _positive_amount(value: Any) -> bool:
    try:
        return float(value) >= 0
    except (ValueError, TypeError):
        return False


def quality_errors(
    database: dict[str, Any], tasks: list[dict[str, Any]], splits: dict[str, Any]
) -> list[str]:
    """Check identifiers, references, amounts, and split membership."""
    errors: list[str] = []
    if not isinstance(database, dict) or set(database) != {"products", "users", "orders"}:
        return ["retail data must contain products, users, and orders"]
    products, users, orders = (database[name] for name in ("products", "users", "orders"))
    if any(not isinstance(value, dict) or not value for value in (products, users, orders)):
        return ["products, users, and orders must be nonempty objects"]
    variant_ids: set[str] = set()
    for product_id, product in products.items():
        if product.get("product_id") != product_id:
            errors.append(f"product {product_id}: ID differs from key")
        if not product.get("name"):
            errors.append(f"product {product_id}: missing name")
        for item_id, variant in product.get("variants", {}).items():
            if item_id in variant_ids or variant.get("item_id") != item_id:
                errors.append(f"variant {item_id}: duplicate or mismatched ID")
            variant_ids.add(item_id)
            if not _positive_amount(variant.get("price")):
                errors.append(f"variant {item_id}: invalid price")
    for user_id, user in users.items():
        if user.get("user_id") != user_id or not user.get("email"):
            errors.append(f"user {user_id}: mismatched ID or missing email")
        for method_id, method in user.get("payment_methods", {}).items():
            if method.get("id") != method_id:
                errors.append(f"user {user_id}: payment method {method_id} ID differs from key")
        for order_id in user.get("orders", []):
            if order_id not in orders or orders[order_id].get("user_id") != user_id:
                errors.append(f"user {user_id}: invalid order link {order_id}")
    for order_id, order in orders.items():
        user_id = order.get("user_id")
        if order.get("order_id") != order_id or user_id not in users:
            errors.append(f"order {order_id}: mismatched ID or missing user")
            continue
        if order_id not in users[user_id].get("orders", []):
            errors.append(f"order {order_id}: absent from user order list")
        for item in order.get("items", []):
            product = products.get(item.get("product_id"))
            if product is None or item.get("item_id") not in product.get("variants", {}):
                errors.append(f"order {order_id}: invalid item {item.get('item_id')}")
            if not _positive_amount(item.get("price")):
                errors.append(f"order {order_id}: invalid item price")
        for payment in order.get("payment_history", []):
            if payment.get("payment_method_id") not in users[user_id].get("payment_methods", {}):
                errors.append(f"order {order_id}: unknown payment method")
            if not _positive_amount(payment.get("amount")):
                errors.append(f"order {order_id}: invalid payment amount")
    if not isinstance(tasks, list) or not tasks:
        return errors + ["tasks must be a nonempty array"]
    task_ids = [str(task.get("id")) for task in tasks if isinstance(task, dict)]
    if len(task_ids) != len(tasks) or len(set(task_ids)) != len(task_ids):
        errors.append("task IDs must be unique and present")
    if not isinstance(splits, dict) or not {"train", "test", "base"} <= set(splits):
        return errors + ["splits must contain train, test, and base"]
    known_ids = set(task_ids)
    for split, ids in splits.items():
        if not isinstance(ids, list) or len(ids) != len(set(ids)):
            errors.append(f"split {split}: duplicate IDs or invalid list")
            continue
        missing = set(map(str, ids)) - known_ids
        if missing:
            errors.append(f"split {split}: unknown task IDs {sorted(missing)}")
    if set(map(str, splits.get("train", []))) & set(map(str, splits.get("test", []))):
        errors.append("train and test splits overlap")
    if set(map(str, splits.get("base", []))) != known_ids:
        errors.append("base split must contain every task")
    return errors


def validate_sources(*, verify_manifest: bool = True) -> dict[str, Any]:
    sources = {name: source_path(name) for name in catalog()}
    data = {name: json.loads(path.read_text(encoding="utf-8")) for name, path in sources.items()}
    errors = quality_errors(data["retail"], data["tasks"], data["splits"])
    if verify_manifest:
        pinned = manifest()["sources"]
        for name, path in sources.items():
            if sha256(path) != pinned[name]["sha256"]:
                errors.append(f"{name}: content differs from the pinned dataset version")
    if errors:
        raise ValueError("Dataset validation failed:\n" + "\n".join(errors[:30]))
    return {
        "version": manifest()["version"],
        "counts": {
            "products": len(data["retail"]["products"]),
            "users": len(data["retail"]["users"]),
            "orders": len(data["retail"]["orders"]),
            "tasks": len(data["tasks"]),
            **{f"{name}_tasks": len(ids) for name, ids in data["splits"].items()},
        },
    }
