import pytest
from pydantic import ValidationError

from app.schemas import ProductInput
from skills.copy_optimization import safe_copy
from tools.quality_checks import attribute_consistency_checker, forbidden_word_checker


def test_attributes_are_bounded_and_json_compatible():
    assert ProductInput(product_id="p", title="商品", attributes={"x": {"y": "z"}})
    with pytest.raises(ValidationError):
        ProductInput(product_id="p", title="商品", attributes={"x": "😀" * 5000})


def test_nested_attribute_strings_are_checked():
    result = forbidden_word_checker("商品", "", {"after_sales": ["永久保修"]})
    assert not result["passed"]
    assert result["issues"][0]["field"] == "attributes.after_sales[0]"


def test_duration_units_are_normalized_for_consistency():
    result = attribute_consistency_checker({"attributes": {"warranty": "1年"}, "title": "保修期限：12个月", "description": ""})
    assert result["passed"]


def test_safe_copy_deduplicates_and_bounds_title():
    result = safe_copy({"product": {"category": "食品", "title": "旗舰爆款旗舰" + "燕麦" * 30, "description": ""}, "issues": []})
    assert len(result["optimized_title"]) <= 60
    assert result["optimized_title"].count("旗舰") <= 1
