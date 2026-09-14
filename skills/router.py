from __future__ import annotations

from typing import Any

from .base import BaseSkill
from .category import BeautyCategorySkill, ElectronicsCategorySkill, FoodCategorySkill


class CategoryRouter:
    """Select exactly one category skill for a normalized product."""

    skills: tuple[BaseSkill, ...] = (FoodCategorySkill(), BeautyCategorySkill(), ElectronicsCategorySkill())

    def route(self, context: dict[str, Any]) -> BaseSkill:
        for skill in self.skills:
            if skill.can_handle(context):
                return skill
        raise ValueError(f"unsupported category: {context.get('category')}")
