from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
import time


class BaseSkill(ABC):
    """Small, uniform contract for a business capability in the Agent."""

    name: str = "base_skill"
    description: str = ""

    def can_handle(self, context: dict[str, Any]) -> bool:
        return True

    @abstractmethod
    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        """Return ``skill_name``, ``passed``, ``issues`` and ``metadata``."""

    def tool(self, context: dict[str, Any], function, *args, **kwargs) -> dict[str, Any]:
        started = time.perf_counter()
        emit = context.get("emit")
        try:
            result = function(*args, **kwargs)
        except Exception as exc:
            if emit:
                emit(function.__name__, skill_name=self.name, tool_name=function.__name__,
                     input_summary=f"product_id={context['product']['product_id']}",
                     output_summary=type(exc).__name__, status="failure", started=started)
            raise
        if emit:
            emit(result["tool_name"], skill_name=self.name, tool_name=result["tool_name"],
                 input_summary=f"product_id={context['product']['product_id']}",
                 output_summary=f"passed={result['passed']}, issues={len(result['issues'])}", started=started)
        return result

    def model(self, context: dict[str, Any], purpose: str, payload: dict, schema):
        started = time.perf_counter()
        emit = context.get("emit")
        try:
            result = context["llm"].generate_json(purpose, payload, schema)
        except Exception as exc:
            if emit:
                emit(purpose, skill_name=self.name, tool_name=f"deepseek.{purpose}",
                     input_summary=f"category={context['category']}", output_summary=type(exc).__name__,
                     status="failure", started=started)
            raise
        if emit:
            emit(purpose, skill_name=self.name, tool_name=f"deepseek.{purpose}",
                 input_summary=f"category={context['category']}", output_summary="JSON Schema校验通过", started=started)
        return result

    @staticmethod
    def result(*, passed: bool, issues: list[dict[str, Any]] | None = None,
               metadata: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
        return {
            "skill_name": extra.pop("skill_name", ""),
            "passed": passed,
            "issues": issues or [],
            "metadata": metadata or {},
            **extra,
        }
