"""Container bootstrap: MySQL required, ES indexing best effort, then serve."""
import time

import uvicorn

from scripts.index_rules import index_rules
from scripts.seed_data import seed
from scripts.seed_catalog import seed_catalog


def main():
    from services.observability import configure_logging, event
    configure_logging()
    samples, rules = seed()
    event("mysql_seed_ready", count=samples + rules)
    count = seed_catalog()
    event("catalog_seed_ready", count=count)
    for attempt in range(10):
        try:
            count = index_rules()
            event("elasticsearch_index_ready", count=count)
            break
        except Exception as exc:
            if attempt == 9:
                event("elasticsearch_index_unavailable", error_code=type(exc).__name__)
            else:
                time.sleep(2)
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, log_config=None, access_log=False,
                limit_concurrency=128, timeout_keep_alive=5)


if __name__ == "__main__":
    main()
