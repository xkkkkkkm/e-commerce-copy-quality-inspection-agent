"""Evaluate the independently annotated fixtures without requiring MySQL.

Both ``python evaluator/run_eval.py`` and ``python -m evaluator.run_eval`` work.
MySQL is imported only when --persist is requested; --api-url uses the API's
normal task/result/trace persistence independently of this optional gold store.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
import time
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.schemas import InspectionReport, ProductInput  # noqa: E402


DEFAULT_CASES = ROOT / "data/evaluation/cases.json"
DEFAULT_REPORT = ROOT / "evaluator/reports/latest.json"
RISK_LEVELS = {"pass", "low", "medium", "high"}
Inspector = Callable[[ProductInput, str], Any]


def product_payload(case: dict[str, Any]) -> dict[str, Any]:
    """Keep annotations out of the request even if ProductInput forbids extras."""
    source = case.get("product_json", case)
    return {name: source[name] for name in ProductInput.model_fields if name in source}


def load_cases(path: str | Path = DEFAULT_CASES) -> list[dict[str, Any]]:
    cases = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError("Evaluation fixture must be a JSON list")
    seen: set[str] = set()
    for case in cases:
        case_id = case.get("case_id", case.get("product_id"))
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError(f"Missing or duplicate case_id: {case_id!r}")
        seen.add(case_id)
        issues = case.get("expected_issues")
        if not isinstance(issues, list) or any(not isinstance(v, str) or not v for v in issues):
            raise ValueError(f"{case_id}: expected_issues must be a list of nonempty strings")
        if case.get("expected_risk_level") not in RISK_LEVELS:
            raise ValueError(f"{case_id}: expected_risk_level must be explicitly annotated")
        ProductInput.model_validate(product_payload(case))
    return cases


def _ratio(numerator: int, denominator: int) -> float | None:
    """An empty denominator is undefined, never an invented perfect score."""
    return round(numerator / denominator, 6) if denominator else None


def compute_metrics(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Micro-average exact issue-type sets per case, including failed calls.

    Repeated evidence for the same issue type counts once in its case. A failed
    call has no accepted predictions, so its expected issues remain false
    negatives. Risk accuracy includes failures in its denominator.
    """
    tp = fp = fn = high_tp = high_fp = high_fn = 0
    risk_correct = structured_count = successful_count = matched_count = 0
    for case in cases:
        expected = set(case["expected_issues"])
        predicted = set(case.get("predicted_issues", []))
        tp += len(expected & predicted)
        fp += len(predicted - expected)
        fn += len(expected - predicted)
        expected_high = case["expected_risk_level"] == "high"
        predicted_high = case.get("predicted_risk_level") == "high"
        high_tp += int(expected_high and predicted_high)
        high_fp += int(not expected_high and predicted_high)
        high_fn += int(expected_high and not predicted_high)
        risk_correct += int(case.get("predicted_risk_level") == case["expected_risk_level"])
        structured_count += int(case.get("structured_success", False))
        successful_count += int(case.get("execution_success", False))
        matched_count += int(not case.get("failed", True))
    count = len(cases)
    return {
        "case_count": count,
        "issue_tp": tp, "issue_fp": fp, "issue_fn": fn,
        "expected_issue_count": tp + fn, "predicted_issue_count": tp + fp,
        "issue_recall": _ratio(tp, tp + fn),
        "issue_precision": _ratio(tp, tp + fp),
        "false_discovery_rate": _ratio(fp, tp + fp),
        "high_risk_tp": high_tp, "high_risk_fp": high_fp, "high_risk_fn": high_fn,
        "high_risk_precision": _ratio(high_tp, high_tp + high_fp),
        "high_risk_recall": _ratio(high_tp, high_tp + high_fn),
        "risk_correct_count": risk_correct, "risk_accuracy": _ratio(risk_correct, count),
        "structured_success_count": structured_count,
        "structured_output_success_rate": _ratio(structured_count, count),
        "execution_success_count": successful_count,
        "execution_success_rate": _ratio(successful_count, count),
        "matched_case_count": matched_count, "strict_case_accuracy": _ratio(matched_count, count),
        "failed_case_count": count - matched_count,
        "error_count": sum(bool(case.get("error")) for case in cases),
        "degraded_case_count": sum(bool(case.get("degraded")) for case in cases),
        "model_used_case_count": sum(bool(case.get("model_used")) for case in cases),
        "average_latency_ms": round(sum(case.get("latency_ms", 0) for case in cases) / count, 3) if count else None,
    }


def run_evaluation(cases: list[dict[str, Any]], inspect_fn: Inspector, *,
                   mode: str = "rules", transport: str = "local") -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    run_id = "eval_" + uuid4().hex
    for index, case in enumerate(cases, 1):
        product_data = product_payload(case)
        record: dict[str, Any] = {
            "case_id": case.get("case_id", case.get("product_id")),
            "product_id": product_data.get("product_id"),
            "category": product_data.get("category") or "未识别",
            "fixture_group": case.get("fixture_group", "unspecified"),
            "expected_issues": sorted(set(case["expected_issues"])),
            "expected_risk_level": case["expected_risk_level"],
            "predicted_issues": [], "predicted_risk_level": None,
            "structured_success": False, "execution_success": False,
            "degraded": False, "model_used": False,
            "task_id": None, "error": None, "report": None,
        }
        started = time.perf_counter()
        try:
            product = ProductInput.model_validate(product_data)
            returned = inspect_fn(product, f"{run_id}_{index}")
            report = InspectionReport.model_validate(returned)
            record["structured_success"] = True
            record["task_id"] = report.task_id
            record["report"] = report.model_dump()
            record["degraded"] = bool(getattr(report, "degraded", False))
            record["model_used"] = bool(getattr(report, "model_used", False))
            if report.status != "success":
                record["error"] = f"Inspection report returned status={report.status}"
            else:
                record["execution_success"] = True
                record["predicted_issues"] = sorted({issue.issue_type for issue in report.issues})
                record["predicted_risk_level"] = report.risk_level
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"[:2000]
        record["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
        expected, predicted = set(record["expected_issues"]), set(record["predicted_issues"])
        record["missing_issues"] = sorted(expected - predicted)
        record["false_positive_issues"] = sorted(predicted - expected)
        record["risk_mismatch"] = record["predicted_risk_level"] != record["expected_risk_level"]
        record["failed"] = bool(record["missing_issues"] or record["false_positive_issues"]
                                or record["risk_mismatch"] or record["error"])
        records.append(record)
    categories = sorted({record["category"] for record in records})
    model_used_count = sum(record["model_used"] for record in records)
    model_status = "not_requested" if mode == "rules" else (
        "no_model_usage_reported" if not model_used_count else
        "model_used_all_cases" if model_used_count == len(records) else "partial_model_usage"
    )
    model_note = (
        "规则模式评测，未请求模型。" if mode == "rules" else
        "full模式未观察到成功模型使用；本次不能作为真实LLM评测。模型可能未配置或调用失败，请核对Trace。" if not model_used_count else
        f"{model_used_count}/{len(records)}条报告标记了成功模型使用；需结合degraded和Trace判断其余模型步骤是否成功。"
    )
    return {
        "schema_version": "1.0", "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "mode": mode, "transport": transport,
            "model_evaluation_status": model_status,
            "model_evaluation_note": model_note,
            "issue_matching": "exact issue_type set per case; micro average; no aliases",
            "undefined_metric": None,
            "false_discovery_rate_definition": "FP/(TP+FP); this is not FP/(FP+TN)",
            "fixture_status": "synthetic fixtures requiring domain expert review; not a production benchmark",
        },
        "summary": compute_metrics(records),
        "by_category": {category: compute_metrics([r for r in records if r["category"] == category])
                        for category in categories},
        "cases": records,
        "failures": [{key: record[key] for key in (
            "case_id", "category", "missing_issues", "false_positive_issues",
            "expected_risk_level", "predicted_risk_level", "risk_mismatch", "error",
        )} for record in records if record["failed"]],
    }


def local_inspector(mode: str) -> Inspector:
    # Importing the evaluator and loading fixtures must not connect to MySQL.
    from agent.orchestrator import inspect_product

    def inspect(product: ProductInput, task_id: str) -> Any:
        return inspect_product(product, task_id, mode=mode)

    return inspect


def api_inspector(base_url: str, mode: str, timeout: float = 180) -> Inspector:
    parts = urlsplit(base_url.rstrip("/"))
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("--api-url must be an absolute HTTP(S) URL")
    path = parts.path.rstrip("/")
    if not path.endswith("/api/products/inspect"):
        path += "/api/products/inspect"
    query = dict(parse_qsl(parts.query))
    query["mode"] = mode
    url = urlunsplit((parts.scheme, parts.netloc, path, urlencode(query), ""))

    def inspect(product: ProductInput, task_id: str) -> Any:
        request = Request(url, data=product.model_dump_json().encode("utf-8"),
                          headers={"Content-Type": "application/json", "Accept": "application/json"},
                          method="POST")
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)

    return inspect


def persist_results(fixtures: list[dict[str, Any]], report: dict[str, Any]) -> None:
    """Upsert only these case IDs; never delete tasks, results or other cases."""
    from sqlalchemy import select
    from db.models import EvaluationCase
    from db.session import Base, SessionLocal, engine

    Base.metadata.create_all(bind=engine)
    by_id = {case.get("case_id", case.get("product_id")): case for case in fixtures}
    with SessionLocal() as db:
        for result in report["cases"]:
            fixture = by_id[result["case_id"]]
            product = product_payload(fixture)
            product["expected_risk_level"] = fixture["expected_risk_level"]
            stored = db.scalar(select(EvaluationCase).where(EvaluationCase.case_id == result["case_id"]))
            if stored is None:
                stored = EvaluationCase(case_id=result["case_id"], product_json=product,
                                        expected_issues=fixture["expected_issues"])
                db.add(stored)
            stored.product_json = product
            stored.expected_issues = fixture["expected_issues"]
            stored.predicted_issues = result["predicted_issues"]
            stored.metrics = {
                **{key: value for key, value in result.items() if key != "report"},
                "evaluation_run_id": report["run_id"], "mode": report["config"]["mode"],
                "generated_at": report["generated_at"],
            }
        db.commit()


def _atomic_write(path: Path, content: str) -> None:
    """Readers see the previous complete file or the new complete file."""
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_report(report: dict[str, Any], output: str | Path) -> tuple[Path, Path]:
    output = Path(output).resolve()
    if output.suffix.lower() != ".json":
        raise ValueError("Report output path must end in .json")
    output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    markdown = output.with_suffix(".md")
    summary = report["summary"]
    lines = ["# 商品文案质检评测", "", f"运行：`{report['run_id']}`，模式：`{report['config']['mode']}`，传输：`{report['config']['transport']}`。",
             "", report["config"]["model_evaluation_note"],
             f"报告标记成功使用模型的样例：{summary['model_used_case_count']}；降级样例：{summary['degraded_case_count']}。",
             "", "数据为合成教学 fixture，需领域专家复核；指标不代表线上效果。空分母记为 N/A。", "",
             "| 范围 | 样例数 | 问题召回率 | 误报比例 FDR | high 精确率 | high 召回率 | 风险准确率 | 结构成功率 | 平均毫秒 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    def percent(value: float | None) -> str:
        return "N/A" if value is None else f"{value:.1%}"
    for category, metrics in [("总体", summary), *report["by_category"].items()]:
        rates = [percent(metrics[key]) for key in ("issue_recall", "false_discovery_rate", "high_risk_precision",
                 "high_risk_recall", "risk_accuracy", "structured_output_success_rate")]
        latency = "N/A" if metrics["average_latency_ms"] is None else str(metrics["average_latency_ms"])
        lines.append(f"| {category} | {metrics['case_count']} | " + " | ".join(rates) + f" | {latency} |")
    lines += ["", "召回率 = TP/(TP+FN)；误报比例 FDR = FP/(TP+FP)，不是 FP/(FP+TN)。",
              "问题按每例 issue_type 集合精确匹配，重复证据只计一次；失败调用保留在分母，预期问题计为漏报。", "",
              f"失败案例：{len(report['failures'])} / {summary['case_count']}。", ""]
    for failure in report["failures"]:
        detail = [f"漏报={failure['missing_issues']}", f"误报={failure['false_positive_issues']}"]
        if failure["risk_mismatch"]:
            detail.append(f"风险={failure['expected_risk_level']} → {failure['predicted_risk_level']}")
        if failure["error"]:
            detail.append(f"错误={failure['error']}")
        lines.append(f"- {failure['case_id']}（{failure['category']}）：" + "；".join(detail).replace("\n", " "))
    _atomic_write(markdown, "\n".join(lines) + "\n")
    return output, markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--mode", choices=("rules", "full"), default="rules")
    parser.add_argument("--api-url", help="POST every case to this API; the API writes normal task/result/trace rows")
    parser.add_argument("--timeout", type=float, default=180,
                        help="HTTP timeout per case in seconds (default: 180, allowing bounded full-mode model calls)")
    parser.add_argument("--persist", action="store_true", help="also upsert gold, predictions and per-case metrics to MySQL evaluation_cases")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        cases = load_cases(args.cases)
        inspector = api_inspector(args.api_url, args.mode, args.timeout) if args.api_url else local_inspector(args.mode)
        report = run_evaluation(cases, inspector, mode=args.mode, transport="http" if args.api_url else "local")
        report["config"].update({"cases_file": str(args.cases.resolve()), "persist_requested": args.persist,
                                 "category_distribution": dict(Counter(case["category"] for case in cases))})
        persistence_error = None
        if args.persist:
            try:
                persist_results(cases, report)
                report["config"]["persisted"] = True
            except Exception as exc:
                persistence_error = type(exc).__name__
                report["config"].update({"persisted": False, "persistence_error": persistence_error})
        json_path, md_path = write_report(report, args.output)
        print(json.dumps({"summary": report["summary"], "report_json": str(json_path),
                          "report_markdown": str(md_path), "persistence_error": persistence_error},
                         ensure_ascii=False, indent=2))
        # Quality misses are measured, not an arbitrary 100% gate. Operational errors fail the command.
        return 1 if persistence_error or report["summary"]["error_count"] else 0
    except Exception as exc:
        print(f"Evaluation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
