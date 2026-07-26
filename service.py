"""
Poller service — the concrete implementation of SPEC.md §7's "workflow picks it up
and triggers in-process." Continuously claims PENDING_MODERATION listings and runs
them through the pipeline, and periodically sweeps stale PROCESSING claims (§2.1).

Run it:
    python3 service.py

Configurable via env vars (all optional, defaults shown):
    POLL_INTERVAL_SECONDS=5
    SWEEP_INTERVAL_SECONDS=60
    SWEEP_TIMEOUT_MINUTES=5
    POLL_BATCH_SIZE=10
"""

import logging
import os
import signal
import time

import db
from pipeline import poll_and_process

POLL_INTERVAL_SECONDS = float(os.environ.get("POLL_INTERVAL_SECONDS", "5"))
SWEEP_INTERVAL_SECONDS = float(os.environ.get("SWEEP_INTERVAL_SECONDS", "60"))
SWEEP_TIMEOUT_MINUTES = int(os.environ.get("SWEEP_TIMEOUT_MINUTES", "5"))
POLL_BATCH_SIZE = int(os.environ.get("POLL_BATCH_SIZE", "10"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("amm.service")


class PollerService:
    """Instance (not module-level) state so the loop is unit-testable without one
    test's shutdown flag leaking into the next."""

    def __init__(
        self,
        poll_interval: float | None = None,
        sweep_interval: float | None = None,
        sweep_timeout_minutes: int | None = None,
        batch_size: int | None = None,
    ):
        self.poll_interval = POLL_INTERVAL_SECONDS if poll_interval is None else poll_interval
        self.sweep_interval = SWEEP_INTERVAL_SECONDS if sweep_interval is None else sweep_interval
        self.sweep_timeout_minutes = SWEEP_TIMEOUT_MINUTES if sweep_timeout_minutes is None else sweep_timeout_minutes
        self.batch_size = POLL_BATCH_SIZE if batch_size is None else batch_size
        self._shutdown = False

    def _handle_signal(self, signum, _frame):
        logger.info("Received signal %s, shutting down after current cycle...", signum)
        self._shutdown = True

    def install_signal_handlers(self):
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def run(self, max_iterations: int | None = None):
        """Runs the poll/sweep loop. `max_iterations` bounds it for tests; real usage
        leaves it None and relies on a signal to stop. A shutdown signal is only
        checked between cycles, not mid-batch -- a batch of `batch_size` listings
        already claimed will finish processing before the loop exits."""
        logger.info(
            "Starting poller: poll_interval=%ss sweep_interval=%ss batch_size=%s",
            self.poll_interval,
            self.sweep_interval,
            self.batch_size,
        )

        iterations = 0
        last_sweep = 0.0
        while not self._shutdown:
            cycle_start = time.monotonic()

            try:
                n = poll_and_process(batch_size=self.batch_size)
                if n:
                    logger.info("Processed %d listing(s)", n)
            except Exception:
                logger.exception("Poll cycle failed")

            if cycle_start - last_sweep >= self.sweep_interval:
                try:
                    reset = db.sweep_stale_processing(timeout_minutes=self.sweep_timeout_minutes)
                    if reset:
                        logger.info("Swept %d stale PROCESSING listing(s) back to PENDING_REVIEW", reset)
                except Exception:
                    logger.exception("Sweep failed")
                last_sweep = cycle_start

            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                break

            elapsed = time.monotonic() - cycle_start
            if not self._shutdown:
                time.sleep(max(0.0, self.poll_interval - elapsed))

        logger.info("Stopped.")


def main():
    service = PollerService()
    service.install_signal_handlers()
    service.run()


if __name__ == "__main__":
    main()
