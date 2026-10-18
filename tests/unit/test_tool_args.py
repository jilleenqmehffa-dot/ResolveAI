"""Commerce tool arguments reject blank IDs and undeclared fields."""

import pytest
from pydantic import ValidationError

from services.agent_service.app.agents.tools.schemas import (
    EmptyToolArgs,
    GetItemArgs,
    GetOrderArgs,
    GetProductArgs,
)


@pytest.mark.parametrize(
    ("schema", "field"),
    [
        (GetProductArgs, "product_id"),
        (GetItemArgs, "item_id"),
        (GetOrderArgs, "order_id"),
    ],
)
def test_id_args_strip_whitespace_and_reject_invalid_values(schema, field) -> None:
    assert getattr(schema.model_validate({field: "  id-1  "}), field) == "id-1"
    for value in ("", "   ", 123):
        with pytest.raises(ValidationError):
            schema.model_validate({field: value})
    with pytest.raises(ValidationError):
        schema.model_validate({field: "id-1", "user_id": "another-user"})


def test_no_argument_tools_reject_extra_fields() -> None:
    assert EmptyToolArgs.model_validate({}).model_dump() == {}
    with pytest.raises(ValidationError):
        EmptyToolArgs.model_validate({"user_id": "another-user"})
