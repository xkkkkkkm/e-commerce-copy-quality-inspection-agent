"""Strict inputs for the administrator's versioned product workflow."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas import Category, ProductInput


ProductStatus = Literal["draft", "pending", "published", "rejected", "offline"]


class ProductCreate(ProductInput):
    model_config = ConfigDict(extra="forbid")

    category: Category
    merchant_name: str = Field(min_length=1, max_length=128)

    @field_validator("merchant_name")
    @classmethod
    def nonempty_merchant(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("商家名称不能为空")
        return value


class ProductUpdate(ProductCreate):
    expected_version: int = Field(ge=1, strict=True)


class ProductAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1, strict=True)
    expected_inspection_id: int | None = Field(default=None, ge=1, strict=True)
    expected_status: ProductStatus | None = None
    reason: str = Field(default="", max_length=2000)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        return value.strip()


class ProductInspect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1, strict=True)
    mode: Literal["rules", "full"] = "rules"


class PublicationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int = Field(ge=1, strict=True)
    expected_version: int | None = Field(default=None, ge=1, strict=True)


class BatchPublicationPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[PublicationCandidate] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_items(self):
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("批量发布不能包含重复商品")
        return self


class PublicationItem(PublicationCandidate):
    expected_version: int = Field(ge=1, strict=True)
    expected_status: Literal["pending", "offline"]
    expected_inspection_id: int = Field(ge=1, strict=True)


class BatchPublication(BatchPublicationPreview):
    items: list[PublicationItem] = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def review_note(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("批量发布必须填写审核说明")
        return value
