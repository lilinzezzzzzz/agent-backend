import pytest
from pydantic import ValidationError

from internal.schemas import AllowExtraModel, ForbidExtraModel, OrmIgnoreExtraModel


class StrictDemoSchema(ForbidExtraModel):
    name: str


class FlexibleDemoSchema(AllowExtraModel):
    name: str


class OrmDemoSchema(OrmIgnoreExtraModel):
    name: str


class DemoOrmObject:
    name = "demo"
    ignored = "extra"


def test_forbid_extra_model_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        StrictDemoSchema.model_validate({"name": "demo", "unknown": "value"})


def test_allow_extra_model_keeps_unknown_fields() -> None:
    schema = FlexibleDemoSchema.model_validate({"name": "demo", "unknown": "value"})

    assert schema.name == "demo"
    assert schema.model_dump()["unknown"] == "value"


def test_orm_ignore_extra_model_reads_attributes_and_ignores_unknown_fields() -> None:
    schema = OrmDemoSchema.model_validate(DemoOrmObject())

    assert schema.model_dump() == {"name": "demo"}
