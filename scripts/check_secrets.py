"""Conservative release check of Git-eligible files; never print matched values."""
import argparse
import os
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="Also inspect blobs reachable from all local Git refs")
    args = parser.parse_args()
    paths = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]).decode().split("\0")
    patterns = [re.compile(rb"sk-[A-Za-z0-9]{20,}"),
                re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}"),
                re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")]
    local_key = os.getenv("DEEPSEEK_API_KEY", "").encode()
    failed = []
    def inspect_data(label, data):
        if any(pattern.search(data) for pattern in patterns) or (len(local_key) > 20 and local_key in data):
            failed.append(label)

    for value in set(paths):
        path = Path(value)
        if not value or not path.is_file():
            continue
        if path.name == ".env" or value.startswith((".local/", "backups/")):
            failed.append(value)
            continue
        data = path.read_bytes()
        inspect_data(value, data)
    # Working files can differ from staged blobs. Inspect the actual index too.
    objects = {}
    for entry in subprocess.check_output(["git", "ls-files", "--stage", "-z"]).split(b"\0"):
        if entry:
            metadata, name = entry.split(b"\t", 1)
            objects[metadata.split()[1].decode()] = "index:" + os.fsdecode(name)
    if args.history:
        for line in subprocess.check_output(["git", "rev-list", "--objects", "--all"]).decode().splitlines():
            oid, _, name = line.partition(" ")
            if name:
                objects[oid] = "history:" + name
    for oid, label in objects.items():
        if subprocess.check_output(["git", "cat-file", "-t", oid]).strip() == b"blob":
            inspect_data(label, subprocess.check_output(["git", "cat-file", "blob", oid]))
    if failed:
        print("Secret check failed; review files (values omitted):", ", ".join(sorted(failed)))
        raise SystemExit(1)
    print("Secret check passed for Git-eligible files, staged blobs" + (" and reachable history" if args.history else ""))


if __name__ == "__main__":
    main()
