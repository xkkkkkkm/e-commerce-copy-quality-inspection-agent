from __future__ import annotations

from typing import Any

from tools.quality_checks import attribute_consistency_checker
from tools.category_checks import category_specific_checker

from .base import BaseSkill


class CategoryQualitySkill(BaseSkill):
    category = ""

    def can_handle(self, context: dict[str, Any]) -> bool:
        return context.get("category") == self.category

    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        results = [self.tool(context, attribute_consistency_checker, context["product"]),
                   self.tool(context, category_specific_checker, context["product"])]
        issues = [issue for result in results for issue in result["issues"]]
        return {"skill_name": self.name, "passed": not issues, "issues": issues,
                "metadata": {"category": self.category, "tools": [result["tool_name"] for result in results]}}


class FoodCategorySkill(CategoryQualitySkill):
    name = "food_category"
    description = "食品类目属性与文案一致性检查。"
    category = "食品"


class BeautyCategorySkill(CategoryQualitySkill):
    name = "beauty_category"
    description = "美妆类目属性与文案一致性检查。"
    category = "美妆"


class ElectronicsCategorySkill(CategoryQualitySkill):
    name = "3c_category"
    description = "3C 类目属性与文案一致性检查。"
    category = "3C"


# Public names in the implementation plan; old Week 2 imports remain valid.
FoodQualitySkill = FoodCategorySkill
BeautyQualitySkill = BeautyCategorySkill
ElectronicsQualitySkill = ElectronicsCategorySkill
