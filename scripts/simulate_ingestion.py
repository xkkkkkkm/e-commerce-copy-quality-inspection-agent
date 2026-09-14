"""Generate synthetic merchant JSON offline, or deliver through the admin API."""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pydantic import ValidationError
from services.simulation import SimulationRequest, generate_preview


def _base_url(value: str) -> str:
    parts = urlsplit(value)
    if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password
            or parts.path not in {"", "/"} or parts.query or parts.fragment):
        raise argparse.ArgumentTypeError("base URL must be an http(s) origin without credentials, path or query")
    return value.rstrip("/")


def deliver_via_api(request: SimulationRequest, base_url: str, enqueue_inspection: bool) -> dict:
    import httpx
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    username, password = os.getenv("ADMIN_USERNAME"), os.getenv("ADMIN_PASSWORD")
    if not username or not password:
        raise RuntimeError("Set ADMIN_USERNAME and ADMIN_PASSWORD in the environment or project .env before --deliver.")
    # Never follow a redirect that could forward administrator credentials.
    with httpx.Client(base_url=base_url, timeout=120, follow_redirects=False) as client:
        response = client.post("/api/admin/auth/login", params={"tenant": os.getenv("TENANT_ID", "default")}, json={"username": username, "password": password})
        if response.status_code != 200:
            raise RuntimeError(f"Administrator login failed (HTTP {response.status_code}).")
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        try:
            response = client.post("/api/admin/simulation/deliver",
                                   json={**request.model_dump(), "enqueue_inspection": enqueue_inspection})
            if response.status_code != 200:
                # Avoid echoing a response that might contain credentials or HTML.
                raise RuntimeError(f"Simulation delivery failed (HTTP {response.status_code}); inspect the admin API and batch_id.")
            return response.json()
        finally:
            try:
                client.post("/api/admin/auth/logout")
            except httpx.HTTPError:
                pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=30, help="Number of submissions, 1–100 (default: 30)")
    parser.add_argument("--seed", type=int, default=42, help="Reproducible integer seed, 0–4294967295")
    parser.add_argument("--scenario", choices=("mixed", "clean", "risky", "missing", "conflict"), default="mixed")
    parser.add_argument("--batch-id", default="demo-42", help="1–64 letters, digits, underscores or hyphens; start with a letter or digit")
    parser.add_argument("--deliver", action="store_true", help="Log in and persist through the admin API; default prints an offline JSON preview")
    parser.add_argument("--base-url", type=_base_url, default="http://127.0.0.1:8000", help="Admin API origin for --deliver")
    parser.add_argument("--no-inspect", action="store_true", help="Deliver pending products without scheduling a rules inspection job")
    args = parser.parse_args(argv)
    try:
        request = SimulationRequest(count=args.count, seed=args.seed, scenario=args.scenario, batch_id=args.batch_id)
    except ValidationError as exc:
        parser.error("; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in exc.errors()))
    try:
        result = deliver_via_api(request, args.base_url, not args.no_inspect) if args.deliver else generate_preview(request)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        # No traceback or request dump: delivery uses administrator credentials.
        print("Simulation failed; check API reachability and administrator configuration.", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
