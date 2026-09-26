"""
Command-line entry point for scheduled data collection.

Usage:
    python scripts\collect_data.py
    python scripts\collect_data.py --force
    python scripts\collect_data.py --only capital,snapshot

Works from any current directory, so Task Scheduler can run it directly with
the virtual environment's python.exe (no activation needed).
"""

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.collector import EXIT_FATAL, TASKS, run_collection  # noqa: E402


def setup_logging():
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")

    file_handler = RotatingFileHandler(
        log_dir / "collector.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    console = logging.StreamHandler()
    console.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main():
    parser = argparse.ArgumentParser(description="Collect Clash of Clans data into data/clash.db")
    parser.add_argument("--force", action="store_true",
                        help="save a member snapshot even if a recent one exists")
    parser.add_argument("--snapshot-interval", type=int, default=60, metavar="MINUTES",
                        help="skip the snapshot if the newest one is younger than this (default 60)")
    parser.add_argument("--only", default="",
                        help="comma-separated subset of: " + ", ".join(TASKS))
    args = parser.parse_args()

    tasks = TASKS
    if args.only:
        tasks = tuple(t.strip() for t in args.only.split(",") if t.strip())
        unknown = [t for t in tasks if t not in TASKS]
        if unknown:
            parser.error(f"unknown task(s): {', '.join(unknown)}. Choose from: {', '.join(TASKS)}")

    setup_logging()
    log = logging.getLogger("collector")
    log.info("Collector started (tasks: %s)", ", ".join(tasks))
    try:
        code = run_collection(
            tasks=tasks, force=args.force, snapshot_interval_minutes=args.snapshot_interval
        )
    except Exception as exc:
        log.error("Collector crashed: %s: %s", type(exc).__name__, exc, exc_info=True)
        code = EXIT_FATAL
    log.info("Collector finished with exit code %s", code)
    return code


if __name__ == "__main__":
    sys.exit(main())