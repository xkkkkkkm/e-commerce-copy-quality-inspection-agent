"""Reproducible synthetic merchant submissions; generation requires no database.

The scenario describes deliberately generated input, never an inspection result.
Persistence imports stay inside delivery so JSON previews also work offline.
"""
from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
import random
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.catalog_schemas import ProductCreate


GENERATOR_VERSION = "1.0.0"
Scenario = Literal["mixed", "clean", "risky", "missing", "conflict"]
CATEGORIES = ("食品", "美妆", "3C")
SCENARIOS = ("clean", "risky", "missing", "conflict")
MERCHANTS = {
    "食品": ("模拟·禾集食品店", "模拟·山谷食仓", "模拟·晨食杂货铺"),
    "美妆": ("模拟·沐光护理店", "模拟·青叶美妆店", "模拟·简颜日用店"),
    "3C": ("模拟·星桥数码店", "模拟·栖木配件店", "模拟·轻舟电子店"),
}


class SimulationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(default=30, ge=1, le=100, strict=True)
    seed: int = Field(default=42, ge=0, le=2**32 - 1, strict=True)
    scenario: Scenario = "mixed"
    batch_id: str = Field(default="demo-42", min_length=1, max_length=64,
                          pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class DeliveryRequest(SimulationRequest):
    enqueue_inspection: bool = Field(default=True, strict=True)


def _digest(value: dict) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def _food(rng: random.Random) -> dict:
    name, ingredients, quantities = rng.choice((
        ("原味燕麦片", "燕麦", (350, 500, 750)),
        ("烘焙腰果", "腰果、食用盐", (180, 250, 400)),
        ("茉莉花茶", "绿茶、茉莉鲜花", (80, 120, 200)),
        ("东北大米", "大米", (1000, 2500, 5000)),
    ))
    amount = rng.choice(quantities)
    brand = rng.choice(("禾集", "晨谷", "山间食光"))
    return {"title": f"{brand} {name} {amount}g 袋装",
            "description": f"{name}，净含量{amount}g。独立袋装，开封后请密封保存，食用前查看包装说明。",
            "attributes": {"brand": brand, "product_type": name, "origin": rng.choice(("浙江", "山东", "黑龙江")),
                           "shelf_life": rng.choice(("6个月", "9个月", "12个月")),
                           "ingredients": ingredients, "storage": "阴凉干燥处密封保存", "net_weight": f"{amount}g"}}


def _beauty(rng: random.Random) -> dict:
    name, ingredients, usage, quantities = rng.choice((
        ("保湿乳液", "水、甘油、角鲨烷", "洁面后取适量均匀涂抹于面部", (80, 100, 150)),
        ("洁面乳", "水、甘油、椰油酰甘氨酸钠", "湿润面部后取适量揉出泡沫，按摩后用清水洗净", (100, 120, 150)),
        ("护手霜", "水、甘油、乳木果脂", "洗手后取适量涂抹于双手", (30, 50, 75)),
        ("保湿化妆水", "水、甘油、透明质酸钠", "洁面后取适量轻拍面部，避开眼周", (120, 150, 200)),
    ))
    amount = rng.choice(quantities)
    brand = rng.choice(("沐光", "青叶", "简颜"))
    return {"title": f"{brand} {name} {amount}ml 日常护理",
            "description": f"{name}，容量{amount}ml，适合日常清洁护理流程。按包装用法使用，初次使用先做局部测试。",
            "attributes": {"brand": brand, "product_type": name, "ingredients": ingredients,
                           "skin_type": rng.choice(("中性及干性肌肤", "中性及混合性肌肤")),
                           "usage": usage, "precautions": "仅供外用，避免接触眼睛，出现不适请停止使用", "volume": f"{amount}ml"}}


def _electronics(rng: random.Random) -> dict:
    name, specifications, compatibility = rng.choice((
        ("USB-C数据线", "长度1m，最高60W充电，USB 2.0数据传输", "支持USB-C接口的手机和平板，充电功率取决于设备及充电器"),
        ("USB-C充电器", "单口输出30W，输入100-240V", "支持USB PD协议的USB-C设备，需搭配相应充电线"),
        ("蓝牙耳机", "蓝牙5.3，单次播放约6小时", "支持蓝牙音频协议的Android及iOS设备"),
        ("无线鼠标", "2.4GHz连接，三档DPI，使用1节AA电池", "Windows 10及以上或macOS 12及以上，需要USB-A接口"),
    ))
    brand = rng.choice(("星桥", "栖木", "轻舟"))
    model = f"{rng.choice(('XQ', 'QM', 'QZ'))}-{rng.randrange(100, 1000)}"
    return {"title": f"{brand} {name} {model}",
            "description": f"{name}，{specifications}。购买前核对设备接口和系统要求，使用体验受设备与环境影响。",
            "attributes": {"brand": brand, "product_type": name, "model": model,
                           "specifications": specifications, "compatibility": compatibility,
                           "warranty": "12个月有限保修，非人为损坏适用", "color": rng.choice(("白色", "深灰色", "蓝色"))}}


def _apply_scenario(product: dict, scenario: str, rng: random.Random) -> None:
    category = product["category"]
    if scenario == "risky":
        claims = {
            "食品": ("本品能够治疗失眠。", "食用一周保证长高。", "本品增强免疫力，人人适用。"),
            "美妆": ("使用后绝对有效，一夜祛斑。", "医学级护理，三天见效。", "可以根治痘痘，无副作用。"),
            "3C": ("兼容所有设备，永不断线。", "性能提升十倍，支持无限续航。", "具有预防近视效果，零辐射。"),
        }
        product["description"] += rng.choice(claims[category])
    elif scenario == "missing":
        fields = {"食品": ("origin", "shelf_life", "storage"),
                  "美妆": ("skin_type", "usage", "precautions"),
                  "3C": ("model", "compatibility", "warranty")}[category]
        for field in rng.sample(fields, rng.choice((1, 2))):
            product["attributes"].pop(field)
    elif scenario == "conflict":
        # Explicit label/value pairs exercise the existing consistency checker.
        label, contradicted = {
            "食品": ("保质期", "24个月"),
            "美妆": ("适用肤质", "油性肌肤"),
            "3C": ("型号", "OTHER-999"),
        }[category]
        product["description"] += f"{label}：{contradicted}。"


def generate_preview(request: SimulationRequest | dict) -> dict:
    """Return a stable export envelope, without time, network or database inputs."""
    request = SimulationRequest.model_validate(request)
    namespace = _digest({"seed": request.seed, "batch_id": request.batch_id})[:32]
    products = []
    for index in range(request.count):
        # A per-item PRNG keeps data independent from call ordering/global random.
        rng = random.Random(_digest({"seed": request.seed, "batch_id": request.batch_id, "index": index}))
        category = CATEGORIES[index % len(CATEGORIES)]
        scenario = SCENARIOS[index % len(SCENARIOS)] if request.scenario == "mixed" else request.scenario
        provenance = {"source": "synthetic", "generator_version": GENERATOR_VERSION,
                      "batch_id": request.batch_id, "seed": request.seed,
                      "scenario": scenario, "requested_scenario": request.scenario,
                      "batch_count": request.count, "ordinal": index + 1}
        product = {"product_id": f"sim_{namespace}_{index + 1:03d}",
                   "merchant_name": MERCHANTS[category][(index // 3) % len(MERCHANTS[category])],
                   "category": category, **(_food, _beauty, _electronics)[index % 3](rng)}
        _apply_scenario(product, scenario, rng)
        # Metadata contains no examples of claims, so detectors cannot flag it.
        product["attributes"]["_simulation"] = deepcopy(provenance)
        validated = ProductCreate.model_validate(product).model_dump()
        products.append({"product": validated, "provenance": deepcopy(provenance)})
    return {"batch_id": request.batch_id, "seed": request.seed, "count": request.count,
            "source": "synthetic", "scenario": request.scenario,
            "generator_version": GENERATOR_VERSION, "products": products,
            "summary": {"categories": dict(Counter(x["product"]["category"] for x in products)),
                        "scenarios": dict(Counter(x["provenance"]["scenario"] for x in products)),
                        "merchant_count": len({x["product"]["merchant_name"] for x in products})}}


def deliver_batch(db, request: DeliveryRequest | dict, actor: str) -> dict:
    """Create versioned pending products and optionally a real rules queue job.

    Existing content is compared in full, including provenance. An edited product
    (even edited back to the original) is never silently reused or overwritten.
    Domain creation commits each product: a retry resumes an interrupted batch.
    """
    from sqlalchemy import select
    from db.catalog_models import ManagedProduct
    from services import catalog, jobs

    request = DeliveryRequest.model_validate(request)
    preview = generate_preview(request.model_dump(exclude={"enqueue_inspection"}))
    payloads = [entry["product"] for entry in preview["products"]]

    def check_existing(row, payload):
        if row.version != 1 or {field: getattr(row, field) for field in catalog.CONTENT_FIELDS} != payload:
            raise catalog.CatalogError(409, f"模拟批次冲突：{payload['product_id']} 已被修改或同一批次参数不同，请使用新的 batch_id。")

    # End authentication/old snapshot transactions before catalog writes.
    db.rollback()
    expected = {payload["product_id"]: payload for payload in payloads}
    existing = db.scalars(select(ManagedProduct).where(ManagedProduct.product_id.in_(expected))).all()
    try:
        for row in existing:
            check_existing(row, expected[row.product_id])
    finally:
        db.rollback()

    created_count = 0
    delivered = []
    for payload in payloads:
        row = db.scalar(select(ManagedProduct).where(ManagedProduct.product_id == payload["product_id"])
                        .execution_options(populate_existing=True))
        if row is not None:
            try:
                check_existing(row, payload)
                result = {"id": row.id, "product_id": row.product_id, "version": row.version}
            finally:
                db.rollback()
        else:
            db.rollback()
            try:
                result = catalog.create_product(db, ProductCreate.model_validate(payload), actor)
                # create_product commits before reading its returned detail.
                # An editor may change that row in between; never enqueue a
                # version different from the synthetic preview the user saw.
                if result["version"] != 1 or {field: result[field] for field in catalog.CONTENT_FIELDS} != payload:
                    raise catalog.CatalogError(409, f"模拟批次冲突：{payload['product_id']} 已被修改，请使用新的 batch_id。")
                created_count += 1
            except catalog.CatalogError as exc:
                if exc.status_code != 409:
                    raise
                # A concurrent identical request may have inserted this row.
                row = db.scalar(select(ManagedProduct).where(ManagedProduct.product_id == payload["product_id"])
                                .execution_options(populate_existing=True))
                try:
                    if row is None:
                        raise
                    check_existing(row, payload)
                    result = {"id": row.id, "product_id": row.product_id, "version": row.version}
                finally:
                    db.rollback()
            finally:
                # create_product's detail lookup opens a new read transaction.
                db.rollback()
        delivered.append({field: result[field] for field in ("id", "product_id", "version")})

    job_id = None
    if request.enqueue_inspection:
        items = [{"id": product["id"], "expected_version": product["version"]} for product in delivered]
        key = "simulation:" + _digest({"seed": request.seed, "batch_id": request.batch_id})
        try:
            job_id = jobs.enqueue(db, items, "rules", key, actor)["id"]
        except ValueError as exc:
            db.rollback()
            raise catalog.CatalogError(409, "模拟批次质检任务参数冲突，请使用新的 batch_id。") from exc
    return {"batch_id": request.batch_id, "created_count": created_count,
            "existing_count": len(delivered) - created_count, "job_id": job_id,
            "products": delivered, "source": "synthetic"}
