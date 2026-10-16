"""Run Streamlit AppTests in a separate process with a hard deadline."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


SUPPORTED_PYTHON = (3, 12)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()
    if args.timeout_seconds < 1:
        parser.error("--timeout-seconds must be positive")
    if sys.version_info[:2] != SUPPORTED_PYTHON:
        print(
            "Streamlit AppTest is supported only on Python 3.12; "
            f"current runtime is {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}.",
            file=sys.stderr,
        )
        return 2
    command = [
        sys.executable, "-m", "pytest", "-q", "-m", "streamlit",
        "--basetemp", str(Path(".pytest-tmp") / "streamlit-apptest"),
    ]
    environment = os.environ.copy()
    temp_root = (Path(".pytest-tmp") / "streamlit-os-temp").resolve()
    temp_root.mkdir(parents=True, exist_ok=True)
    environment["TEMP"] = str(temp_root)
    environment["TMP"] = str(temp_root)
    try:
        completed = subprocess.run(
            command, check=False, timeout=args.timeout_seconds, env=environment,
        )
    except subprocess.TimeoutExpired:
        print(
            f"Streamlit AppTest exceeded hard timeout of {args.timeout_seconds} seconds.",
            file=sys.stderr,
        )
        return 124
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
