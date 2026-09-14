"""Idempotently add catalog examples; never invent inspection or publish history."""
import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def seed_catalog() -> int:
    from sqlalchemy import select
    from app.catalog_schemas import ProductCreate
    from db.catalog_models import CATALOG_TABLES, ManagedProduct
    from db.session import Base, SessionLocal, engine
    from services.catalog import CatalogError, create_product

    Base.metadata.create_all(bind=engine, tables=CATALOG_TABLES)
    originals = json.loads((ROOT / "data/samples/products.json").read_text(encoding="utf-8"))
    count = 0
    merchants = {"食品": "谷物集食品旗舰店", "美妆": "简肌美妆旗舰店", "3C": "数码优选专营店"}
    with SessionLocal() as db:
        for category, merchant_name in merchants.items():
            for original in [item for item in originals if item["category"] == category][:4]:
                product_id = "catalog_" + original["product_id"]
                if db.scalar(select(ManagedProduct.id).where(ManagedProduct.product_id == product_id)):
                    continue
                product = {key: original[key] for key in ("category", "title", "description", "attributes")}
                try:
                    create_product(db, ProductCreate(**product, product_id=product_id, merchant_name=merchant_name), "system:seed")
                    count += 1
                except CatalogError as exc:
                    if exc.status_code != 409:
                        raise
                    # Concurrent startup may already have inserted this same example.
                    if not db.scalar(select(ManagedProduct.id).where(ManagedProduct.product_id == product_id)):
                        raise
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", action="store_true", help="对缺少当前成功质检的演示商品执行真实规则质检（不会发布）")
    args = parser.parse_args()
    inserted = seed_catalog()
    print(f"catalog seed complete: inserted={inserted}")
    if args.inspect:
        from app.main import perform_inspection
        from db.session import SessionLocal
        from services.catalog import inspect_product, list_products
        with SessionLocal() as db:
            products = list_products(db, q="catalog_", risk="uninspected", page_size=100)["items"]
            for product in products:
                if not product["product_id"].startswith("catalog_"):
                    continue
                result = inspect_product(db, product["id"], product["version"], "rules", "system:seed", perform_inspection)
                print(f"{product['product_id']}: {result['report']['status']} / {result['report']['risk_level']}")


if __name__ == "__main__":
    main()
