"""
Auto-bump version.txt using CalVer: YYYY.MM.patch

Logic:
- If year.month matches current date: increment patch.
- If year.month is outdated: reset to currentYear.currentMonth.0

Only bumps if there are uncommitted changes in the repo (staged or unstaged),
indicating work was done this session.
"""

import subprocess
import sys
from datetime import datetime, timezone

VERSION_FILE = "version.txt"


def has_changes():
    """Check if there are any staged or unstaged changes (excluding version.txt itself)."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True
    )
    lines = result.stdout.strip().splitlines()
    # Filter out version.txt changes to avoid self-triggering loops
    changes = [l for l in lines if VERSION_FILE not in l]
    return len(changes) > 0


def read_version():
    with open(VERSION_FILE, "r") as f:
        return f.read().strip()


def write_version(version):
    with open(VERSION_FILE, "w") as f:
        f.write(version + "\n")


def bump():
    if not has_changes():
        # No meaningful changes this session — don't bump
        return

    current = read_version()

    # Strip _dev suffix if present (CI appends it for dev builds)
    clean = current.replace("_dev", "")

    now = datetime.now(timezone.utc)
    current_year = now.year
    current_month = now.month

    try:
        parts = clean.split(".")
        ver_year = int(parts[0])
        ver_month = int(parts[1])
        ver_patch = int(parts[2])
    except (ValueError, IndexError):
        # Can't parse — reset to current date
        new_version = f"{current_year}.{current_month:02d}.0"
        write_version(new_version)
        print(f"Version reset (unparseable): {current} -> {new_version}")
        return

    if ver_year == current_year and ver_month == current_month:
        # Same month — increment patch
        new_version = f"{current_year}.{current_month:02d}.{ver_patch + 1}"
    else:
        # New month — reset patch
        new_version = f"{current_year}.{current_month:02d}.0"

    write_version(new_version)
    print(f"Version bumped: {current} -> {new_version}")


if __name__ == "__main__":
    bump()
