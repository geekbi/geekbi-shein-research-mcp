"""Pydantic input models built from the verified platform API contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from .config import PLATFORM_LABEL, RESULT_WINDOW, TOOL_SPECS


TYPE_MAP = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "datetime": datetime,
    "list_int": list[int],
}


class QueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_contract(self):
        fields = type(self).model_fields
        for name in fields:
            if not name.endswith("Min"):
                continue
            maximum_name = f"{name[:-3]}Max"
            if maximum_name not in fields:
                continue
            minimum = getattr(self, name)
            maximum = getattr(self, maximum_name)
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ValueError(f"{name} 不能大于 {maximum_name}")
        if hasattr(self, "page") and hasattr(self, "size"):
            if self.page * self.size > RESULT_WINDOW:
                raise ValueError(f"{PLATFORM_LABEL} 查询最多访问前 {RESULT_WINDOW} 条，请缩小筛选范围")
        if PLATFORM_LABEL == "Coupang" and hasattr(self, "siteId") and self.siteId != 1:
            raise ValueError("Coupang 当前仅支持韩国站，siteId=1")
        required_one_of = getattr(type(self), "__required_one_of__", ())
        if required_one_of and not any(getattr(self, name) not in (None, "") for name in required_one_of):
            raise ValueError(f"至少提供一个参数：{', '.join(required_one_of)}")
        for name in fields:
            value = getattr(self, name)
            if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} 必须包含时区偏移")
        return self

    def query_items(self, *, exclude: set[str] | None = None) -> list[tuple[str, str]]:
        payload = self.model_dump(mode="json", exclude_none=True, exclude=exclude or set())
        items: list[tuple[str, str]] = []
        for key, value in payload.items():
            values = value if isinstance(value, list) else [value]
            for item in values:
                if isinstance(item, bool):
                    encoded = "true" if item else "false"
                else:
                    encoded = str(item)
                items.append((key, encoded))
        return items


def _annotation(spec: dict[str, Any]):
    if spec["type"] == "literal":
        return Literal.__getitem__(tuple(spec["values"]))
    return TYPE_MAP[spec["type"]]


def _build_model(tool: dict[str, Any]):
    fields = {}
    for spec in tool["fields"]:
        annotation = _annotation(spec)
        required = spec.get("required", False)
        if not required:
            annotation = annotation | None
        default = ... if required else spec.get("default", None)
        constraints = {
            key: spec[key]
            for key in ("ge", "le", "min_length", "max_length")
            if key in spec
        }
        fields[spec["name"]] = (
            annotation,
            Field(default, description=spec["description"], **constraints),
        )
    model = create_model(tool["model"], __base__=QueryInput, **fields)
    model.__required_one_of__ = tuple(tool.get("one_of", ()))
    return model


MODEL_CLASSES = {
    tool["key"]: _build_model(tool)
    for tool in TOOL_SPECS
    if tool["kind"] != "site"
}
globals().update({model.__name__: model for model in MODEL_CLASSES.values()})
