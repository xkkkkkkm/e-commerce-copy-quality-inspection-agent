"""Short-lived root init container copies private operator config for app UID.

The API and workers remain non-root; host files can remain 0600. No password is
printed, and the shared runtime volume is mounted read-only by serving services.
"""
import json
import os
from pathlib import Path
import pwd


def main():
    source = Path("/source/tenants.json")
    content = source.read_text() if source.exists() else "{}"
    if not isinstance(json.loads(content), dict):
        raise ValueError("Invalid tenant registry")
    user = pwd.getpwnam("app")
    directory = Path("/runtime")
    directory.mkdir(exist_ok=True)
    os.chown(directory, user.pw_uid, user.pw_gid)
    directory.chmod(0o700)
    temporary = directory / "tenants.tmp"
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(content)
    os.chown(temporary, user.pw_uid, user.pw_gid)
    temporary.replace(directory / "tenants.json")


if __name__ == "__main__":
    main()
