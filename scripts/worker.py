"""Run checkpointed waves outside HTTP requests on a long-lived Python host.

Usage: python -m scripts.worker [--once] [--project PROJECT_ID]
The API and worker must use the same DATABASE_URL (or local SQLite path).
"""

import argparse
import logging
import time

from app.main import engine
from app.live import LiveRunner
from app.storage import StorageConflict
from app.workflow import WorkflowError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process one scan and exit")
    parser.add_argument("--project", help="Restrict the worker to a single project")
    args = parser.parse_args()
    runner = LiveRunner(engine)
    while True:
        projects = [engine.get(args.project)] if args.project else engine.list()
        for project in projects:
            if not project.live_run or project.status != "queued" or project.active_gate_id:
                continue
            try:
                result = runner.execute_wave(project.id)
                logging.info("Wave saved for %s: %s", result.id, result.status)
            except (WorkflowError, StorageConflict):
                pass  # another worker or browser claimed this project first
            except Exception as exc:
                logging.error("Wave stopped for %s (%s); inspect the run before retrying", project.id, type(exc).__name__)
        if args.once:
            return
        time.sleep(2)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        main()
    except KeyboardInterrupt:
        pass
