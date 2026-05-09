"""CLI entry: `cua-loop --url <url> --task '<task>'`."""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from cua_loop.runner import run_with_retry


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Verified self-correcting CUA loop.")
    parser.add_argument("--task", required=True, help="What you want extracted, in plain English.")
    parser.add_argument("--url", default=None, help="Starting URL (optional).")
    parser.add_argument("--max-attempts", type=int, default=5)
    args = parser.parse_args()

    result = run_with_retry(task=args.task, url=args.url, max_attempts=args.max_attempts)
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
