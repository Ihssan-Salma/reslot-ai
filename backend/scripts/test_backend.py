"""Run tests, optionally loading local real-database credentials without printing them."""

import argparse
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", action="store_true", help="Run real PostgreSQL tests")
    parser.add_argument("--fail-fast", action="store_true", help="Stop at the first failure")
    parser.add_argument("paths", nargs="*", default=["tests"], help="Optional test files")
    args = parser.parse_args()
    if args.database:
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        for target, source in (
            ("TEST_DATABASE_URL", "DATABASE_URL"),
            ("TEST_ADMIN_DATABASE_URL", "MIGRATION_DATABASE_URL"),
        ):
            value = os.environ.get(target) or os.environ.get(source)
            if not value:
                parser.error(f"Set {target} or {source}")
            os.environ[target] = value
    return int(
        pytest.main(
            args.paths
            + ["-q", "-p", "no:cacheprovider", "--tb=short"]
            + (["-x"] if args.fail_fast else [])
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
