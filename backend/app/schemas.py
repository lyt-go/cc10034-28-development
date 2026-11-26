"""通用接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None
    # 保留复核/争用场景的增强返回，未使用这些字段的模块保持默认值。
    receipt: dict[str, Any] | None = None
    event: dict[str, Any] | None = None
    conflict: bool = False
    next_actions: list[str] = Field(default_factory=list)
    next_action: str | None = None
    download_url: str | None = None


class EntryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    action: str
    remark: str | None = None
    days: int | None = None
