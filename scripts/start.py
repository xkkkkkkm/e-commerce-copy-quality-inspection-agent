"""Container bootstrap: MySQL required, ES indexing best effort, then serve."""
import time

import uvicorn

from scripts.index_rules import index_rules
from scripts.seed_data import seed
from scripts.seed_catalog import seed_catalog


def main():
    samples, rules = seed()
    print(f"MySQL ready: inserted_samples={samples}, inserted_rules={rules}", flush=True)
    count = seed_catalog()
    print(f"Catalog ready: inserted_products={count}", flush=True)
    for attempt in range(10):
        try:
            count = index_rules()
            print(f"Elasticsearch ready: {count} rules", flush=True)
            break
        except Exception as exc:
            if attempt == 9:
                print(f"Elasticsearch indexing unavailable ({type(exc).__name__}); API will use MySQL rules. "
                      "Retry python -m scripts.index_rules after recovery.", flush=True)
            else:
                time.sleep(2)
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
