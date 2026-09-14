"""Readiness + three reports + persisted Trace. Creates three inspection tasks."""
import argparse
import json
from pathlib import Path

import httpx
from scripts.api_session import authenticated_client


def main():
    parser = argparse.ArgumentParser(description="验证MySQL/API、三种风险结果和Trace；会写入3个演示任务")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    cases = json.loads((Path(__file__).parents[1] / "data/evaluation/cases.json").read_text())
    # Explicit known cases make this a repeatable workflow check, not an accuracy benchmark.
    selected = {"food_001": "high", "food_004": "medium", "3c_002": "pass"}
    with authenticated_client(args.base_url, timeout=180) as client:
        health = client.get("/health")
        health.raise_for_status()
        assert health.json()["database"] == "mysql"
        for case in cases:
            if case["product_id"] not in selected:
                continue
            payload = {key: case[key] for key in ("product_id", "category", "title", "description", "attributes")}
            response = client.post("/api/products/inspect?mode=rules", json=payload)
            response.raise_for_status()
            report = response.json()
            assert report["risk_level"] == selected[case["product_id"]], report
            task_id = report["task_id"]
            saved = client.get(f"/api/results/{task_id}")
            saved.raise_for_status()
            assert saved.json() == report
            task = client.get(f"/api/tasks/{task_id}")
            assert task.json()["status"] == "success"
            trace_response = client.get(f"/api/tasks/{task_id}/traces")
            trace_response.raise_for_status()
            events = trace_response.json()
            assert any(event["tool_name"] == "risk_score_calculator" for event in events)
            assert any(event["skill_name"] == "title_quality" for event in events)
            assert not any(event["status"] == "failure" for event in events)
            assert all(issue["rule_id"] for issue in report["issues"])
            exported = client.get(f"/api/results/{task_id}/export")
            exported.raise_for_status()
            assert exported.json()["report"]["task_id"] == task_id
            print(f"{case['product_id']}: {report['risk_level']}, traces={len(events)}, task={task_id}")
    print("MySQL/API/结果查询/Trace/导出验证通过。批量准确率请另运行evaluator。")


if __name__ == "__main__":
    main()
