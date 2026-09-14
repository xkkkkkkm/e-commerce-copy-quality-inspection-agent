"""Conservative release check of Git-eligible files; never print matched values."""
import os
from pathlib import Path
import re
import subprocess


def main():
    paths = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]).decode().split("\0")
    patterns = [re.compile(rb"sk-[A-Za-z0-9]{20,}"),
                re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}"),
                re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")]
    local_key = os.getenv("DEEPSEEK_API_KEY", "").encode()
    failed = []
    for value in set(paths):
        path = Path(value)
        if not value or not path.is_file():
            continue
        if path.name == ".env" or value.startswith((".local/", "backups/")):
            failed.append(value)
            continue
        data = path.read_bytes()
        if any(pattern.search(data) for pattern in patterns) or (len(local_key) > 20 and local_key in data):
            failed.append(value)
    if failed:
        print("Secret check failed; review files (values omitted):", ", ".join(sorted(failed)))
        raise SystemExit(1)
    print("Secret check passed for Git-eligible files")


if __name__ == "__main__":
    main()
