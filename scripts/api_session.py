"""CLI authentication shared by verification and evaluation tools."""
from contextlib import contextmanager
import os
from urllib.parse import urlsplit
import httpx
from dotenv import load_dotenv


@contextmanager
def authenticated_client(base_url, timeout=180):
    load_dotenv()
    parts = urlsplit(base_url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Use an HTTP(S) origin without embedded credentials")
    with httpx.Client(base_url=base_url, timeout=timeout, follow_redirects=False) as client:
        response = client.post("/api/admin/auth/login", params={"tenant": os.getenv("TENANT_ID", "default")},
                               json={"username": os.getenv("ADMIN_USERNAME", "admin"),
                                     "password": os.getenv("ADMIN_PASSWORD", "admin12345")})
        if response.status_code != 200:
            raise RuntimeError(f"CLI login failed (HTTP {response.status_code})")
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        try:
            yield client
        finally:
            try:
                client.post("/api/admin/auth/logout")
            except httpx.HTTPError:
                pass
