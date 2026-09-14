import argparse
import json
import sys

from agent.orchestrator import inspect_product
from app.schemas import ProductInput


def main() -> None:
    parser = argparse.ArgumentParser(description="运行商品质检；默认离线规则模式，不写数据库")
    parser.add_argument("input", nargs="?", help="JSON file, or stdin when omitted")
    parser.add_argument("--mode", choices=("rules", "full"), default="rules")
    parser.add_argument("--trace", action="store_true", help="把执行Trace输出到stderr")
    args = parser.parse_args()
    raw = open(args.input, encoding="utf-8").read() if args.input else sys.stdin.read()
    product = ProductInput.model_validate(json.loads(raw))
    traces = []
    report = inspect_product(product, "cli_preview", traces.append, mode=args.mode)
    print(json.dumps(report.model_dump(), ensure_ascii=False, indent=2))
    if args.trace:
        print(json.dumps(traces, ensure_ascii=False, indent=2), file=sys.stderr)


if __name__ == "__main__":
    main()
