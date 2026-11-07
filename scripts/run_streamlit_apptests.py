"""Run Streamlit AppTests in a separate process with a hard deadline."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


SUPPORTED_PYTHON = (3, 12)
EXPECTED_APPTEST_COUNT = 6


def _collected_node_ids(output: str) -> list[str]:
    return [
        line.strip()
        for line in output.splitlines()
        if line.strip().startswith("tests/") and "::" in line
    ]


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
    test_arguments = [
        "-q", "-m", "streamlit",
        "--basetemp", str(Path(".pytest-tmp") / "streamlit-apptest"),
    ]
    collect_command = [
        sys.executable, "-m", "pytest", "--collect-only", *test_arguments,
    ]
    command = [
        sys.executable, "-m", "pytest", "-q", "-m", "streamlit",
        "--basetemp", str(Path(".pytest-tmp") / "streamlit-apptest"),
    ]
    environment = os.environ.copy()
    temp_root = (Path(".pytest-tmp") / "streamlit-os-temp").resolve()
    temp_root.mkdir(parents=True, exist_ok=True)
    environment["TEMP"] = str(temp_root)
    environment["TMP"] = str(temp_root)
    deadline = time.monotonic() + args.timeout_seconds
    try:
        collected = subprocess.run(
            collect_command,
            check=False,
            timeout=max(0.1, deadline - time.monotonic()),
            env=environment,
            capture_output=True,
            text=True,
        )
        if collected.stdout:
            print(collected.stdout, end="", flush=True)
        if collected.stderr:
            print(collected.stderr, end="", file=sys.stderr, flush=True)
        if collected.returncode != 0:
            print("Streamlit AppTest collection failed.", file=sys.stderr)
            return 2
        node_ids = _collected_node_ids(collected.stdout)
        if len(node_ids) != EXPECTED_APPTEST_COUNT or len(set(node_ids)) != EXPECTED_APPTEST_COUNT:
            print(
                "Streamlit AppTest collection count mismatch: "
                f"expected {EXPECTED_APPTEST_COUNT}, collected {len(node_ids)}.",
                file=sys.stderr,
            )
            return 2
        completed = subprocess.run(
            command,
            check=False,
            timeout=max(0.1, deadline - time.monotonic()),
            env=environment,
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
