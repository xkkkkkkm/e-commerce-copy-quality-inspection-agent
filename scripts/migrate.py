"""Run idempotent upgrades for an existing MySQL volume."""

from db.migrations import run_migrations


if __name__ == "__main__":
    changes = run_migrations()
    print("migrations applied: " + (", ".join(changes) if changes else "none"))
