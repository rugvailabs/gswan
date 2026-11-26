"""Request and response shapes. Python uses snake_case; JSON uses camelCase (via the alias generator)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ItemIn(ApiModel):
    name: str = Field(min_length=1, max_length=200)


class Item(ApiModel):
    id: str
    name: str
    created_at: str


class ItemPage(ApiModel):
    items: list[Item]
    total: int
    limit: int
    offset: int
