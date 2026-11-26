"""Queue-based worker system for non-blocking submission processing.

Submission intake stores a Submission (``pending``) with its Predictions File
and puts a ``SubmissionTask`` on the ``SubmissionQueue`` that routes get from
``Depends(get_submission_queue)``. A ``SubmissionWorker`` takes tasks off that
queue and scores the Submission with its Challenge's locked Scorer, through
the Scorer catalogue: ``processing``, then ``completed`` with the unrounded
Headline Metric and details, or ``failed`` with the error.

In production ``start_submission_workers`` runs workers on daemon threads.
Tests hand the API a fresh queue and call ``SubmissionWorker.drain``, which
processes every queued task on the calling thread until the queue is empty.
"""

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

import models
from models.submission import SubmissionStatus
from ports.object_storage import ObjectStorage
from service import scorer_catalogue

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

QUEUE_POLL_TIMEOUT_SECONDS = 5


@dataclass
class SubmissionTask:
    """A stored Submission waiting to be scored."""
    submission_id: str
    priority: int = 0  # Lower numbers = higher priority

    def __lt__(self, other):
        """For priority queue ordering"""
        return self.priority < other.priority


class SubmissionQueue:
    """The tasks waiting for a worker, with counters for monitoring."""

    def __init__(self) -> None:
        self.tasks: "queue.Queue[Optional[SubmissionTask]]" = queue.Queue()
        self._stats = {"total_queued": 0, "total_processed": 0, "queue_size": 0}

    def add_task(self, task: SubmissionTask) -> bool:
        """Queue ``task`` for a worker.

        Args:
            task: The submission to process.

        Returns:
            True once the task is queued.
        """
        self.tasks.put(task)
        self._stats["total_queued"] += 1
        self._stats["queue_size"] = self.tasks.qsize()
        logger.info(f"Task queued: {task.submission_id} (queue size: {self._stats['queue_size']})")
        return True

    def mark_processed(self) -> None:
        """Count a task a worker has finished with."""
        self._stats["total_processed"] += 1
        self.tasks.task_done()

    def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics"""
        self._stats["queue_size"] = self.tasks.qsize()
        return self._stats.copy()


class SubmissionWorker:
    """Processes tasks from a ``SubmissionQueue``, one at a time."""

    def __init__(
        self,
        *,
        submission_queue: SubmissionQueue,
        session_factory: Callable[[], Session],
        storage: ObjectStorage,
        worker_id: int = 1,
    ) -> None:
        self.submission_queue = submission_queue
        self.session_factory = session_factory
        self.storage = storage
        self.worker_id = worker_id
        self.is_running = False

    def drain(self) -> int:
        """Process queued tasks on the calling thread until none is left.

        Returns:
            How many tasks were processed.
        """
        processed = 0
        while True:
            try:
                task = self.submission_queue.tasks.get_nowait()
            except queue.Empty:
                return processed
            self._process_and_mark(task)
            processed += 1

    def run(self) -> None:
        """Main worker loop - runs in separate thread"""
        self.is_running = True
        logger.info(f"Worker {self.worker_id} started")
        while self.is_running:
            try:
                task = self.submission_queue.tasks.get(timeout=QUEUE_POLL_TIMEOUT_SECONDS)
            except queue.Empty:
                continue
            if task is None:  # Shutdown signal
                self.submission_queue.tasks.task_done()
                break
            self._process_and_mark(task)
        logger.info(f"Worker {self.worker_id} stopped")

    def stop(self) -> None:
        """Stop the worker"""
        self.is_running = False

    def _process_and_mark(self, task: SubmissionTask) -> None:
        try:
            self.process_submission(task)
        finally:
            self.submission_queue.mark_processed()

    def process_submission(self, task: SubmissionTask) -> None:
        """Score one Submission, recording the outcome on it."""
        db = self.session_factory()
        try:
            self._process(db, task.submission_id)
        except Exception as e:  # the worker must outlive any one Submission
            logger.exception("Worker %s: unexpected error scoring %s", self.worker_id, task.submission_id)
            db.rollback()
            self._fail(db, task.submission_id, f"Unexpected processing error: {e}")
        finally:
            db.close()

    def _process(self, db: Session, submission_id: str) -> None:
        submission = db.get(models.Submission, submission_id)
        if submission is None:
            logger.error("Worker %s: Submission %s not found", self.worker_id, submission_id)
            return
        if not submission.is_live:
            logger.info("Worker %s: Submission %s was deleted before scoring", self.worker_id, submission_id)
            return
        submission.status = SubmissionStatus.PROCESSING
        submission.status_message = "Scoring..."
        db.commit()

        challenge = db.get(models.Challenge, submission.challenge_id)
        try:
            result = self._score(challenge, submission)
        except scorer_catalogue.ScorerError as exc:
            logger.info("Worker %s: Scorer error on %s: %s", self.worker_id, submission_id, exc)
            self._fail(db, submission_id, f"The Scorer {challenge.scorer_name!r} failed: {exc}")
            return
        submission.headline_metric = result.headline_metric
        submission.score_details = result.details
        submission.status = SubmissionStatus.COMPLETED
        submission.status_message = None
        challenge.queue_sync()
        db.commit()
        logger.info("Worker %s: scored %s", self.worker_id, submission_id)

    def _score(self, challenge: models.Challenge, submission: models.Submission) -> scorer_catalogue.ScoreResult:
        """Score with the Scorer name and parameters stored on the Challenge."""
        return scorer_catalogue.score(
            challenge.scorer_name,
            inputs=scorer_catalogue.load_inputs(self.storage.get(challenge.inputs_key)),
            references=scorer_catalogue.load_ground_truth(self.storage.get(challenge.ground_truth_key)),
            predictions=scorer_catalogue.load_predictions(self.storage.get(submission.predictions_url)),
            params=challenge.scorer_params,
        )

    def _fail(self, db: Session, submission_id: str, error: str) -> None:
        """Mark the Submission failed with ``error``."""
        submission = db.get(models.Submission, submission_id)
        if submission:
            submission.status = SubmissionStatus.FAILED
            submission.status_message = error
            db.commit()


# Global queue instance
submission_queue = SubmissionQueue()
_workers: List[SubmissionWorker] = []
_worker_threads: List[threading.Thread] = []


def get_submission_queue() -> SubmissionQueue:
    """FastAPI dependency for the queue that routes put submissions on."""
    return submission_queue


def start_submission_workers(num_workers: int = 2) -> None:
    """Start worker threads that score Submissions from S3. Does nothing (and
    logs why) when object storage is not configured, so the arena still
    starts without S3 credentials."""
    import database
    from ports.object_storage import get_object_storage

    try:
        storage = get_object_storage()
    except RuntimeError as exc:
        logger.warning("Submission workers not started: %s", exc)
        return
    for i in range(num_workers):
        worker = SubmissionWorker(
            submission_queue=submission_queue,
            session_factory=database.SessionLocal,
            storage=storage,
            worker_id=i + 1,
        )
        thread = threading.Thread(target=worker.run, daemon=True, name=f"SubmissionWorker-{i + 1}")
        _workers.append(worker)
        _worker_threads.append(thread)
        thread.start()
    logger.info(f"Started {num_workers} submission workers")


def get_queue_stats() -> Dict[str, Any]:
    """Get submission queue statistics"""
    stats = submission_queue.get_stats()
    stats["active_workers"] = len([w for w in _workers if w.is_running])
    return stats


def stop_submission_workers() -> None:
    """Stop all submission workers"""
    logger.info("Stopping submission workers...")
    for worker in _workers:
        submission_queue.tasks.put(None)
        worker.stop()
    for thread in _worker_threads:
        thread.join(timeout=10)
    logger.info("All submission workers stopped")
