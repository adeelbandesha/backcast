import importlib.metadata
import os
import subprocess
from functools import cache
from pathlib import Path


@cache
def code_version() -> str:
    """The package version, plus the commit when it's known: `BACKCAST_COMMIT`, or git in a source checkout."""
    version = importlib.metadata.version("backcast")
    commit = os.environ.get("BACKCAST_COMMIT", "").strip()
    checkout = Path(__file__).resolve().parents[2]
    if not commit and (checkout / ".git").exists():
        described = subprocess.run(
            ["git", "-C", str(checkout), "describe", "--always", "--dirty"], capture_output=True, text=True
        )
        commit = described.stdout.strip() if described.returncode == 0 else ""
    return f"{version}+{commit}" if commit else version
