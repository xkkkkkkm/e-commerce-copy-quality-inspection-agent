import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_json(relative_path: str):
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def seed(*, import_rules: bool = False) -> tuple[int, int]:
    """Insert missing sample/rule fixtures; existing rules are immutable by default.

    The return value remains (new sample count, new rule count) for existing
    callers. Evaluation gold is upserted independently of product existence,
    so rerunning this script also repairs earlier partial seed runs.
    """
    from sqlalchemy import select
    from db.models import EvaluationCase, ProductSample, QualityRule
    from db.session import Base, SessionLocal, engine
    from evaluator.run_eval import product_payload

    Base.metadata.create_all(bind=engine)
    originals = load_json("data/samples/products.json")
    evaluation = load_json("data/evaluation/cases.json")
    original_ids = {item["product_id"] for item in originals}
    samples = {item["product_id"]: item for item in [*originals, *evaluation]}
    # Startup may fill a brand-new database, but only an explicit import is
    # allowed to replace an existing MySQL rule row.
    rules = load_json("data/rules/rules.json")
    sample_count = rule_count = 0
    with SessionLocal() as db:
        for item in samples.values():
            if not db.scalar(select(ProductSample.id).where(ProductSample.product_id == item["product_id"])):
                product = product_payload(item)
                source = "manual" if item["product_id"] in original_ids else "synthetic_evaluation"
                db.add(ProductSample(**product, source=source))
                sample_count += 1
        for item in evaluation:
            product = product_payload(item)
            # The current database schema has no expected_risk_level column.
            # Keep it with the fixture, and filter annotations before ProductInput validation.
            product["expected_risk_level"] = item["expected_risk_level"]
            stored = db.scalar(select(EvaluationCase).where(EvaluationCase.case_id == item["case_id"]))
            if stored is None:
                db.add(EvaluationCase(case_id=item["case_id"], product_json=product,
                                      expected_issues=item["expected_issues"]))
            else:
                stored.product_json = product
                stored.expected_issues = item["expected_issues"]
                # Preserve previous predicted_issues and metrics for review.
        for item in rules:
            stored = db.scalar(select(QualityRule).where(QualityRule.rule_id == item["rule_id"]))
            if stored is None:
                db.add(QualityRule(**item))
                rule_count += 1
            elif import_rules:
                # Explicit fixture imports are the only operation allowed to
                # replace an existing rule. Normal startup preserves MySQL.
                for key, value in item.items():
                    setattr(stored, key, value)
        db.commit()
    return sample_count, rule_count


if __name__ == "__main__":
    inserted_samples, inserted_rules = seed(import_rules=True)
    print(f"seed complete: inserted_samples={inserted_samples}, inserted_rules={inserted_rules}, evaluation_cases=50 upserted")
