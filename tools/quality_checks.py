import re
from typing import Any

from app.schemas import InspectionReport


REQUIRED_ATTRIBUTES = {
    "食品": ["brand", "origin", "shelf_life", "ingredients", "storage"],
    "美妆": ["brand", "ingredients", "skin_type", "usage", "precautions"],
    "3C": ["brand", "model", "specifications", "compatibility", "warranty"],
}

HIGH_RISK_TERMS = {
    "治疗": "医疗功效宣称",
    "根治": "医疗功效宣称",
    "失眠克星": "医疗功效暗示",
    "全网最低": "绝对化表达",
    "绝对有效": "绝对化表达",
    "100%": "绝对化表达",
    "国家级": "绝对化表达",
    "无副作用": "绝对化表达",
    "永久": "绝对化表达",
    "治愈": "医疗功效宣称",
}


def title_length_checker(title: str, category: str, max_length: int = 60) -> dict[str, Any]:
    passed = len(title) <= max_length
    return {
        "tool_name": "title_length_checker",
        "passed": passed,
        "issues": [] if passed else [{
            "issue_type": "标题过长", "field": "title", "risk_level": "low",
            "evidence": f"标题长度为 {len(title)}，超过建议上限 {max_length}",
            "suggestion": "删除重复修饰词，保留品牌、品类和核心规格。",
        }],
        "metadata": {"length": len(title), "max_length": max_length, "category": category},
    }


def is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == [] or value == {}


def required_attribute_checker(category: str, attributes: dict[str, Any]) -> dict[str, Any]:
    missing = [name for name in REQUIRED_ATTRIBUTES.get(category, []) if is_blank(attributes.get(name))]
    return {
        "tool_name": "required_attribute_checker",
        "passed": not missing,
        "issues": [{
            "issue_type": "关键信息缺失", "field": "attributes", "risk_level": "medium",
            "evidence": f"{category}类商品缺少必填属性：{', '.join(missing)}",
            "suggestion": f"补充属性字段：{', '.join(missing)}。",
        }] if missing else [],
        "metadata": {"required": REQUIRED_ATTRIBUTES.get(category, []), "missing": missing},
    }


def _flatten_strings(value: Any, path: str = "attributes"):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _flatten_strings(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _flatten_strings(item, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


def forbidden_word_checker(title: str, description: str, attributes: dict[str, Any] | None = None) -> dict[str, Any]:
    issues = []
    fields = [("title", title), ("description", description)]
    fields.extend(_flatten_strings(attributes or {}))
    for field, text in fields:
        for term, issue_type in HIGH_RISK_TERMS.items():
            if term == "100%":
                # A material proportion is a fact, not an efficacy guarantee.
                matched = bool(re.search(r"100%\s*(?:有效|治愈|治疗|安全|无副作用|成功|纯天然|永不)", text))
            else:
                matched = any(not re.search(r"(?:不用于|不能|不可|不得|不承诺|不保证|不具备|非|并非)$", text[max(0, m.start()-5):m.start()])
                              for m in re.finditer(re.escape(term), text))
            if matched:
                issues.append({
                    "issue_type": issue_type, "field": field, "risk_level": "high",
                    "evidence": f"{field}中命中高风险表达“{term}”",
                    "suggestion": "删除治疗、绝对化或无法验证的承诺，改为客观描述商品信息。",
                    "matched_text": term,
                })
    return {"tool_name": "forbidden_word_checker", "passed": not issues, "issues": issues,
            "metadata": {"matched_count": len(issues)}}


def keyword_stuffing_checker(title: str) -> dict[str, Any]:
    terms = [term for term in ("旗舰", "爆款", "特价", "正品", "新品", "官方") if title.count(term) > 1]
    issues = [{"issue_type": "关键词堆砌", "field": "title", "risk_level": "low",
               "evidence": f"关键词重复：{', '.join(terms)}", "suggestion": "删除重复关键词，保留最相关的商品属性。"}] if terms else []
    return {"tool_name": "keyword_stuffing_checker", "passed": not issues, "issues": issues,
            "metadata": {"repeated_terms": terms}}


def title_core_attribute_checker(title: str, category: str, attributes: dict[str, Any]) -> dict[str, Any]:
    """Only flag an explicitly supplied product type or an all-marketing title."""
    core = attributes.get("product_type")
    remainder = re.sub(r"旗舰|爆款|特价|正品|新品|官方|限时|促销|热卖|推荐|好物|商品|[\s!！]", "", title)
    missing = ["category_core"] if not remainder or (isinstance(core, str) and core.strip() and core not in title) else []
    issues = [{
        "issue_type": "标题核心属性缺失", "field": "title", "risk_level": "low",
        "evidence": f"标题未识别出{category}类商品的品类或核心属性",
        "suggestion": "在标题中保留品牌、品类和核心规格等识别信息。",
    }] if missing else []
    return {"tool_name": "title_core_attribute_checker", "passed": not issues, "issues": issues,
            "metadata": {"checked_fields": ["category_core"], "missing": missing}}


def attribute_consistency_checker(product: dict[str, Any]) -> dict[str, Any]:
    """Find explicit ``field:value`` claims that disagree with structured attributes."""
    attributes = product.get("attributes") or {}
    labels = {
        "brand": "品牌", "origin": "产地", "model": "型号",
        "compatibility": "兼容性", "skin_type": "适用肤质", "shelf_life": "保质期",
        "warranty": "保修期限", "specifications": "规格",
    }
    issues = []
    for field, label in labels.items():
        expected = attributes.get(field)
        if is_blank(expected) or not isinstance(expected, (str, int, float)):
            continue
        for text_field in ("title", "description"):
            for match in re.finditer(rf"{re.escape(label)}\s*[:：]\s*([^，,。；;\n]+)", product.get(text_field, "")):
                declared = match.group(1).strip()
                if _normalized_measure(declared) != _normalized_measure(str(expected)):
                    issues.append({
                        "issue_type": "属性与文案冲突", "field": text_field, "risk_level": "medium",
                        "evidence": f"{label}结构化属性为“{expected}”，文案声明为“{match.group(1).strip()}”",
                        "suggestion": f"统一{label}信息，并以商品实际标签或规格为准。",
                        "matched_text": match.group(0),
                    })
    return {"tool_name": "attribute_consistency_checker", "passed": not issues, "issues": issues,
            "metadata": {"checked_fields": list(labels), "conflict_count": len(issues)}}


def _normalized_measure(value: str) -> str:
    text = re.sub(r"\s+", "", str(value)).casefold()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(天|日|个月|月|年|小时|时)", text)
    if not match:
        return text
    amount, unit = float(match.group(1)), match.group(2)
    factors = {"天": 1, "日": 1, "个月": 30, "月": 30, "年": 360, "小时": 1 / 24, "时": 1 / 24}
    return f"{amount * factors[unit]:g}天"


def risk_score_calculator(issues: list[dict[str, Any]]) -> dict[str, Any]:
    weights = {"high": 5, "medium": 3, "low": 1, "pass": 0}
    score = sum(weights.get(issue.get("risk_level", "low"), 1) for issue in issues)
    level = "high" if score >= 5 or any(i.get("risk_level") == "high" for i in issues) else "medium" if score >= 3 else "low" if score else "pass"
    return {"tool_name": "risk_score_calculator", "passed": True, "issues": [], "metadata": {"score": score, "risk_level": level}}


def json_schema_validator(report: dict[str, Any]) -> dict[str, Any]:
    """Validate the public inspection report without calling a model."""
    try:
        InspectionReport.model_validate(report)
    except Exception as exc:
        return {"tool_name": "json_schema_validator", "passed": False, "issues": [{
            "issue_type": "输出结构无效", "field": "report", "risk_level": "high",
            "evidence": str(exc).splitlines()[0][:500],
            "suggestion": "按 InspectionReport 结构补齐字段并检查字段类型。",
        }], "metadata": {"valid": False}}
    return {"tool_name": "json_schema_validator", "passed": True, "issues": [],
            "metadata": {"valid": True}}


def all_checks(product: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        title_length_checker(product["title"], product["category"]),
        keyword_stuffing_checker(product["title"]),
        forbidden_word_checker(product["title"], product.get("description", ""), product.get("attributes", {})),
        required_attribute_checker(product["category"], product.get("attributes", {})),
        attribute_consistency_checker(product),
    ]
