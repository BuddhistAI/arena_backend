"""Queue-based worker system for non-blocking submission processing.

Routes put a ``SubmissionTask`` on the ``SubmissionQueue`` they get from
``Depends(get_submission_queue)`` and return at once. A ``SubmissionWorker``
takes tasks off that queue, stores the predictions file through the object
storage port and runs the evaluation, recording the status on the Submission.

In production ``start_submission_workers`` runs workers on daemon threads.
Tests hand the API a fresh queue and call ``SubmissionWorker.drain``, which
processes every queued task on the calling thread until the queue is empty.
"""

import asyncio
import json
import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

import models
from CRUD.upload_file_to_s3 import validate_json_structure
from Evaluation.evaluation import trigger_automatic_evaluation
from models.submission import SubmissionStatus
from ports.object_storage import ObjectStorage, private_key
from submission_cache import CacheStatus, set_submission_progress

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

QUEUE_POLL_TIMEOUT_SECONDS = 5


class PredictionsFileError(ValueError):
    """The predictions file is not valid JSON in the expected structure."""


@dataclass
class SubmissionTask:
    """Data structure for submission processing tasks"""
    submission_id: str
    file_content: bytes
    filename: str
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


def predictions_key(task: SubmissionTask) -> str:
    """Private object storage key of a Submission's predictions file."""
    return private_key("submissions", task.submission_id, task.filename)


def _check_predictions(file_content: bytes) -> None:
    try:
        data = json.loads(file_content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PredictionsFileError(f"Invalid JSON format: {exc}") from exc
    is_valid, message = validate_json_structure(data)
    if not is_valid:
        raise PredictionsFileError(message)


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
            asyncio.run(self.process_submission(task))
        finally:
            self.submission_queue.mark_processed()

    async def process_submission(self, task: SubmissionTask) -> None:
        """Process a single submission task, recording failures on it."""
        db = self.session_factory()
        try:
            await self._process(db, task)
        except Exception as e:  # the worker must outlive any one Submission
            error_msg = f"Worker {self.worker_id} processing error: {str(e)}"
            logger.error(error_msg)
            db.rollback()
            self._fail(db, task.submission_id, "Unexpected processing error", error_msg)
        finally:
            db.close()

    async def _process(self, db: Session, task: SubmissionTask) -> None:
        submission_id = task.submission_id
        logger.info(f"Worker {self.worker_id} processing submission {submission_id}")
        set_submission_progress(
            submission_id, CacheStatus.PROCESSING,
            f"Worker {self.worker_id} started processing...",
            progress=10, step="Worker Started"
        )
        submission = db.query(models.Submission).filter(models.Submission.id == submission_id).first()
        if not submission:
            error_msg = f"Submission {submission_id} not found"
            logger.error(error_msg)
            set_submission_progress(
                submission_id, CacheStatus.FAILED,
                "Submission not found in database",
                progress=0, step="Error", error=error_msg
            )
            return

        submission.status = SubmissionStatus.PROCESSING
        submission.status_message = f"Processing by worker {self.worker_id}..."
        db.commit()

        if not self._store_predictions(db, submission, task):
            return
        await self._evaluate(db, submission)

    def _store_predictions(self, db: Session, submission: models.Submission, task: SubmissionTask) -> bool:
        set_submission_progress(
            task.submission_id, CacheStatus.UPLOADING,
            "Uploading and validating file...",
            progress=30, step="File Upload & Validation"
        )
        try:
            _check_predictions(task.file_content)
        except PredictionsFileError as exc:
            error_msg = f"File processing failed: {exc}"
            logger.error(f"Worker {self.worker_id}: {error_msg}")
            self._fail(db, task.submission_id, "File upload or validation failed", error_msg)
            return False

        key = predictions_key(task)
        self.storage.put(key, task.file_content, "application/json")
        set_submission_progress(
            task.submission_id, CacheStatus.PROCESSING,
            "File uploaded successfully. Starting evaluation...",
            progress=60, step="Starting Evaluation"
        )
        submission.dataset_url = key
        submission.status_message = "File uploaded successfully. Running evaluation..."
        db.commit()
        return True

    async def _evaluate(self, db: Session, submission: models.Submission) -> None:
        submission_id = str(submission.id)
        set_submission_progress(
            submission_id, CacheStatus.EVALUATING,
            "Running automatic evaluation...",
            progress=80, step="Evaluation in Progress"
        )
        if await trigger_automatic_evaluation(db, submission):
            set_submission_progress(
                submission_id, CacheStatus.COMPLETED,
                f"Evaluation completed successfully by worker {self.worker_id}",
                progress=100, step="Completed"
            )
            submission.status = SubmissionStatus.COMPLETED
            submission.status_message = "Evaluation completed successfully"
            logger.info(f"Worker {self.worker_id}: Completed submission {submission_id}")
            db.commit()
        else:
            logger.error(f"Worker {self.worker_id}: Evaluation failed for {submission_id}")
            self._fail(db, submission_id, "Evaluation failed", "Evaluation failed")

    def _fail(self, db: Session, submission_id: str, message: str, error: str) -> None:
        """Mark the Submission failed with ``error``; ``message`` is the
        short progress text shown while the cache entry lives."""
        set_submission_progress(
            submission_id, CacheStatus.FAILED, message, progress=0, step="Failed", error=error
        )
        submission = db.query(models.Submission).filter(models.Submission.id == submission_id).first()
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
    """Start worker threads that store predictions in S3. Does nothing (and
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
