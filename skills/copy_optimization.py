import re

from app.schemas import CopyOutput
from skills.base import BaseSkill
from tools.category_checks import category_specific_checker
from tools.quality_checks import forbidden_word_checker
from tools.quality_checks import all_checks


MARKETING_TERMS = ("旗舰", "爆款", "特价", "正品", "新品", "官方", "限时", "促销", "热卖", "推荐", "好物")


def _shorten_title(title: str, max_length: int = 60) -> str:
    text = title.strip()
    for term in MARKETING_TERMS:
        # deterministic de-duplication keeps the first occurrence and all facts
        seen = False
        while text.count(term) > 1:
            pos = text.rfind(term)
            text = text[:pos] + text[pos + len(term):]
    if len(text) <= max_length:
        return text
    # Keep complete leading tokens/clauses and avoid cutting a Unicode code point.
    chunks = re.split(r"([\s,，;；|/]+)", text)
    out = ""
    for chunk in chunks:
        if len(out + chunk) > max_length:
            break
        out += chunk
    return out.rstrip(" ,，;；|/") or text[:max_length]


def safe_copy(context: dict) -> dict:
    """Remove complete affected clauses; never fabricate missing product facts."""
    product = context["product"]
    values = {}
    for field in ("title", "description"):
        text = product.get(field, "")
        spans = [issue.get("matched_text") for issue in context.get("issues", [])
                 if issue["field"] == field and issue.get("matched_text")]
        ranges = [(match.start(), match.end()) for span in spans for match in re.finditer(re.escape(span), text)]
        # Compare source offsets so evidence spanning several clauses removes all of them.
        chunks = list(re.finditer(r"[^。！？；;，,\n]+[。！？；;，,\n]*|[。！？；;，,\n]+", text))
        values[field] = "".join(chunk.group() for chunk in chunks
                                if not any(chunk.start() < end and chunk.end() > start for start, end in ranges)).strip()
    values["title"] = _shorten_title(values["title"] or "商品信息待补充")
    candidate = {**product, **values}
    # Also remove risky clauses found during a second independent rule check.
    remaining = forbidden_word_checker(values["title"], values["description"], candidate.get("attributes", {}))["issues"]
    remaining += category_specific_checker(candidate)["issues"]
    for issue in remaining:
        field, span = issue["field"], issue.get("matched_text")
        if field in values and span:
            values[field] = "".join(chunk for chunk in re.split(r"(?<=[。！？；;，,\n])", values[field]) if span not in chunk).strip()
    return {"optimized_title": _shorten_title(values["title"] or "商品信息待补充"),
            "optimized_description": values["description"],
            "rewrite_reason": "删除已命中风险或矛盾表述所在的分句，保留原有客观信息；缺失属性需依据商品实物补齐。若标题被全部删除，使用待补充标记。"}


class CopywritingOptimizationSkill(BaseSkill):
    name = "copy_optimization"
    description = "根据已发现的问题生成保守改写并验证。"

    async def run(self, context):
        if not context.get("issues") and not context.get("required_failed"):
            return self.result(skill_name=self.name, passed=True, metadata={
                "optimized_title": context["product"]["title"],
                "optimized_description": context["product"].get("description", ""),
                "rewrite_reason": "未发现问题，保留原文。",
            })
        safe = safe_copy(context)
        candidate = {**context["product"], "title": safe["optimized_title"], "description": safe["optimized_description"]}
        unresolved = [issue for check in all_checks(candidate) for issue in check["issues"]]
        warnings = []
        if context.get("mode") == "full" and context.get("llm") and context["llm"].enabled and getattr(context["llm"], "optional_generations", True):
            try:
                output = self.model(context, "optimization", {
                    "product": context["product"], "issues": context["issues"], "rules": context.get("rules", []),
                    "permitted_copy": safe,
                    "requirements": "为保证不新增商品事实，本演示采用保守改写：标题和详情必须使用permitted_copy提供的文本，仅生成原因说明。",
                }, CopyOutput)
                if output.optimized_title != safe["optimized_title"] or output.optimized_description != safe["optimized_description"]:
                    raise ValueError("改写新增内容或改变未经确认的事实")
                # The explanation is a product-facing claim too. Keep the verified
                # deterministic reason; free-form model text is not evidence.
            except Exception as exc:
                warnings.append(f"文案优化模型不可用或输出未通过校验（{type(exc).__name__}），已使用保守删除方案。")
        return self.result(skill_name=self.name, passed=not unresolved,
                           issues=unresolved,
                           metadata={**safe, "warnings": warnings,
                                     "rewrite_applied": safe["optimized_title"] != context["product"]["title"] or safe["optimized_description"] != context["product"].get("description", ""),
                                     "unresolved_issues": unresolved})
