"""Conservative, explicit category rules; implicit claims are handled by the LLM."""
import re
from typing import Any

from tools.quality_checks import is_blank, _flatten_strings


POLICIES = {
    "食品": {
        "shelf_life": ("保质期格式异常", "food_attr_002", "请填写可核对的保质期，如12个月。"),
        "ingredients": ("配料信息不明确", "food_attr_003", "按包装列出实际配料。"),
        "storage": ("储存信息不明确", "food_attr_004", "提供具体储存条件。"),
    },
    "美妆": {
        "skin_type": ("适用肤质表述不当", "beauty_attr_002", "写明适用肤质及限制。"),
        "usage": ("使用方式不明确", "beauty_attr_003", "根据标签补充用法。"),
        "precautions": ("注意事项不明确", "beauty_attr_004", "根据标签补充注意事项。"),
    },
    "3C": {
        "compatibility": ("兼容性说明缺失", "3c_attr_002", "提供具体设备、系统或协议范围。"),
        "warranty": ("保修信息缺失", "3c_attr_003", "明确有限保修期限和条件。"),
        "specifications": ("规格信息不明确", "3c_attr_004", "提供可核对的数值及单位。"),
    },
}

ATTRIBUTE_TYPES = {
    "食品": {"brand": (str,), "origin": (str,), "shelf_life": (str,), "ingredients": (str,), "storage": (str,)},
    "美妆": {"brand": (str,), "ingredients": (str,), "skin_type": (str,), "usage": (str,), "precautions": (str,)},
    "3C": {"brand": (str,), "model": (str,), "specifications": (str,), "compatibility": (str,), "warranty": (str,)},
}
TYPE_RULES = {"食品": "food_attr_001", "美妆": "beauty_attr_001", "3C": "3c_attr_001"}

CLAIMS = {
    "食品": [
        (r"提升免疫力|增强免疫力", "夸大宣传", "food_claim_002"),
        (r"喝一周就长高|一周长高|保证长高", "过度承诺", "food_claim_003"),
        (r"老人小孩都适合|适合所有人|人人适用", "适用人群表述不当", "food_audience_001"),
    ],
    "美妆": [
        (r"医学级|药用级", "功效宣称风险", "beauty_claim_001"),
        (r"一夜祛斑|永不反弹|三天见效", "过度承诺", "beauty_claim_003"),
        (r"有\S{0,8}成分就能\S{0,8}治", "成分功效推断", "beauty_claim_004"),
    ],
    "3C": [
        (r"兼容所有设备|永不断线|永不损坏", "过度承诺", "3c_claim_001"),
        (r"预防近视|治疗近视|零辐射", "误导性功效", "3c_claim_002"),
        (r"性能提升十倍|无限续航", "夸大宣传", "3c_claim_003"),
    ],
}


def category_specific_checker(product: dict[str, Any]) -> dict[str, Any]:
    category = product["category"]
    attributes = product.get("attributes", {})
    issues = []
    for field, expected_types in ATTRIBUTE_TYPES[category].items():
        if field not in attributes or is_blank(attributes.get(field)):
            continue
        value = attributes[field]
        if not isinstance(value, expected_types):
            issues.append({"issue_type": "属性类型无效", "field": f"attributes.{field}", "risk_level": "medium",
                           "rule_id": TYPE_RULES[category], "evidence": f"{field}类型为{type(value).__name__}，应为文本。",
                           "suggestion": f"将{field}改为可核对的文本值。"})
    for field, (issue_type, rule_id, suggestion) in POLICIES[category].items():
        value = attributes.get(field)
        if is_blank(value):
            continue  # RequiredAttributeSkill already reports empty values.
        invalid = isinstance(value, (dict, list, bool)) or str(value).strip() in {"未知", "随便", "见详情", "不详", "有", "无", "好", "正常"}
        if field == "warranty" and re.search(r"永久|终身|无限期", str(value)):
            invalid = True
        if field == "shelf_life":
            invalid = invalid or not re.fullmatch(r"\s*[1-9]\d*\s*(?:天|日|个月|月|年)\s*", str(value))
        if field in {"skin_type", "compatibility"}:
            invalid = invalid or str(value).strip() in {"所有", "全部", "所有人", "所有肤质", "所有设备"}
        if invalid:
            issues.append({"issue_type": issue_type, "field": f"attributes.{field}", "risk_level": "medium",
                           "rule_id": rule_id, "evidence": f"{field}={value}，信息无法明确核对。", "suggestion": suggestion})
    for field in ("title", "description"):
        for pattern, issue_type, rule_id in CLAIMS[category]:
            for match in re.finditer(pattern, product.get(field, "")):
                prefix = product[field][max(0, match.start()-4):match.start()]
                if re.search(r"不能|不可|不得|不承诺|不保证", prefix):
                    continue
                issues.append({"issue_type": issue_type, "field": field, "risk_level": "high", "rule_id": rule_id,
                               "matched_text": match.group(), "evidence": f"{field}包含“{match.group()}”",
                               "suggestion": "删除无法证实的承诺，保留已有的客观商品信息。"})
    # Structured promotion/after-sales fields are still customer-facing claims.
    # Apply the same category patterns to nested strings and retain a precise path.
    for field, text in _flatten_strings(attributes):
        for pattern, issue_type, rule_id in CLAIMS[category]:
            match = re.search(pattern, text)
            if match:
                issues.append({"issue_type": issue_type, "field": field, "risk_level": "high", "rule_id": rule_id,
                               "matched_text": match.group(), "evidence": f"{field}包含“{match.group()}”",
                               "suggestion": "删除无法证实的承诺，保留已有的客观商品信息。"})
    return {"tool_name": "category_specific_checker", "passed": not issues, "issues": issues,
            "metadata": {"category": category, "checked_rules": [p[1] for p in POLICIES[category].values()] + [p[2] for p in CLAIMS[category]]}}
