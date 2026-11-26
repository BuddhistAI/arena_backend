"""Sync Worker: publishes pending Challenges to their Challenge Repos.

Sync state lives on the Challenge row, so pending work survives a restart:

- ``sync_status`` is ``pending`` until the repo matches the arena, then
  ``synced``; after ``MAX_ATTEMPTS`` failed attempts in a row it is
  ``failed`` and the worker leaves it alone until an admin asks for a Resync.
- ``sync_attempts`` counts the failed attempts since the last success.
- ``last_sync_error`` holds the error of the last failed attempt.
- ``updated_at`` is set to the time of each failed attempt; the next attempt
  is due ``backoff * 2 ** (sync_attempts - 1)`` after it.

A deleted Challenge (``deleted_at`` set) is queued the same way; its attempt
archives the repo instead of publishing it.

Submissions ride on their Challenge's sync: scoring a Submission queues its
Challenge, and every attempt publishes the folder of each scored Submission
(``service.submission_publisher``). A successful attempt marks each of them
``synced`` with its ``repo_path``; when the Challenge's attempts run out, the
scored Submissions still ``pending`` become ``failed`` until a Resync.

Sync is one way: nothing is read back from the repo host. Each attempt
creates the repo from the template if it is missing and replaces the repo's
whole tree with the publisher's file set in one commit, so a retry or a
Resync rebuilds the repo from the arena's data and drops any other file.

In production ``start_sync_worker`` runs ``run_once`` on a daemon thread.
Tests call ``drain``, which retries without waiting until nothing is pending.
"""

import logging
import threading
from datetime import datetime, timedelta
from typing import Callable, List, Optional, Tuple

from sqlalchemy.orm import Session, object_session

from models.challenge import Challenge, SyncStatus
from models.submission import Submission, SubmissionStatus
from models.user import User
from ports.clock import Clock, utc_now
from ports.object_storage import ObjectStorage
from ports.repo_host import RepoAlreadyExists, RepoHost, RepoNotFound
from service.challenge_publisher import MAX_COMMITTED_INPUTS_BYTES, challenge_files
from service.submission_publisher import PublishedSubmission, is_publishable, submission_files

logger = logging.getLogger(__name__)

COMMIT_MESSAGE = "Sync challenge from the BuddhistAI arena"
MAX_ATTEMPTS = 5
DEFAULT_BACKOFF = timedelta(seconds=30)
DEFAULT_POLL_INTERVAL_SECONDS = 10.0


class SyncWorker:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        storage: ObjectStorage,
        repo_host: RepoHost,
        max_committed_inputs_bytes: int = MAX_COMMITTED_INPUTS_BYTES,
        backoff: timedelta = DEFAULT_BACKOFF,
        clock: Clock = utc_now,
    ) -> None:
        self.session_factory = session_factory
        self.storage = storage
        self.repo_host = repo_host
        self.max_committed_inputs_bytes = max_committed_inputs_bytes
        self.backoff = backoff
        self.clock = clock

    def run_once(self) -> int:
        """Attempt every pending Challenge whose backoff has elapsed, once.
        Returns how many were attempted."""
        return self._pass(respect_backoff=True)

    def drain(self) -> None:
        """Attempt pending Challenges, without waiting between retries, until
        none is pending (each ends synced or failed)."""
        while self._pass(respect_backoff=False):
            pass

    def retry_delay(self, failed_attempts: int) -> timedelta:
        """How long after failed attempt number ``failed_attempts`` the next
        one is due: ``backoff``, then twice that, four times, ..."""
        return self.backoff * (2 ** max(failed_attempts - 1, 0))

    def _pass(self, *, respect_backoff: bool) -> int:
        db = self.session_factory()
        try:
            attempted = 0
            for challenge_id in self._pending_ids(db, respect_backoff):
                # Claim the row for the attempt, so that two workers never
                # sync one Challenge at once and an edit made meanwhile waits
                # for the attempt and leaves the Challenge pending again.
                challenge = (
                    db.query(Challenge)
                    .filter(Challenge.id == challenge_id, Challenge.sync_status == SyncStatus.PENDING)
                    .with_for_update(skip_locked=True)
                    .one_or_none()
                )
                if challenge is None:
                    db.rollback()
                    continue
                self._attempt(challenge)
                db.commit()
                attempted += 1
            return attempted
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _pending_ids(self, db: Session, respect_backoff: bool) -> List:
        rows = (
            db.query(Challenge.id, Challenge.sync_attempts, Challenge.updated_at)
            .filter(Challenge.sync_status == SyncStatus.PENDING)
            .order_by(Challenge.updated_at)
            .all()
        )
        db.rollback()  # end the read transaction before claiming rows
        now = self.clock()
        return [
            challenge_id
            for challenge_id, attempts, last_attempt_at in rows
            if not respect_backoff or self._is_due(attempts, last_attempt_at, now)
        ]

    def _is_due(self, failed_attempts: int, last_attempt_at: Optional[datetime], now: datetime) -> bool:
        if failed_attempts == 0 or last_attempt_at is None:
            return True
        return now >= last_attempt_at + self.retry_delay(failed_attempts)

    def _attempt(self, challenge: Challenge) -> None:
        try:
            if challenge.deleted_at is not None:
                self._archive(challenge)
            else:
                for submission, published in self._publish(challenge):
                    submission.mark_synced(published.folder)
        except Exception as exc:
            challenge.record_failed_sync(_describe(exc), at=self.clock(), max_attempts=MAX_ATTEMPTS)
            if challenge.sync_status == SyncStatus.FAILED:
                self._fail_waiting_submissions(challenge)
            logger.warning(
                "Sync of Challenge %s (%s) failed, attempt %d of %d: %s",
                challenge.id, challenge.repo_name, challenge.sync_attempts, MAX_ATTEMPTS,
                challenge.last_sync_error,
            )
        else:
            challenge.mark_synced()
            action = "Archived the repo of" if challenge.deleted_at is not None else "Synced"
            logger.info("%s Challenge %s (%s)", action, challenge.id, challenge.repo_name)

    def _fail_waiting_submissions(self, challenge: Challenge) -> None:
        """Mark ``failed`` every scored Submission still waiting to be published."""
        waiting = object_session(challenge).query(Submission).filter(
            Submission.challenge_id == challenge.id,
            Submission.status == SubmissionStatus.COMPLETED,
            Submission.sync_status == SyncStatus.PENDING,
        )
        for submission in waiting:
            submission.record_failed_sync()

    def _archive(self, challenge: Challenge) -> None:
        try:
            self.repo_host.archive_repo(challenge.repo_name)
        except RepoNotFound:
            pass  # never published, so there is nothing to archive

    def _publish(self, challenge: Challenge) -> List[Tuple[Submission, PublishedSubmission]]:
        """Replace the repo's tree with the Challenge's files and every scored
        Submission's folder. Returns the Submissions it published."""
        files = challenge_files(
            challenge, self.storage, max_committed_inputs_bytes=self.max_committed_inputs_bytes
        )
        submissions = self._published_submissions(challenge)
        for _, published in submissions:
            files.update(published.files)
        try:
            self.repo_host.create_repo_from_template(challenge.repo_name, challenge.title)
        except RepoAlreadyExists:
            pass
        self.repo_host.replace_files(challenge.repo_name, files, COMMIT_MESSAGE)
        return submissions

    def _published_submissions(self, challenge: Challenge) -> List[Tuple[Submission, PublishedSubmission]]:
        """The Challenge's scored Submissions of a publishable type, oldest
        first, each with its folder."""
        rows = (
            object_session(challenge)
            .query(Submission, User.username)
            .join(User, User.id == Submission.user_id)
            .filter(Submission.challenge_id == challenge.id, Submission.status == SubmissionStatus.COMPLETED)
            .order_by(Submission.created_at, Submission.id)
            .all()
        )
        return [
            (submission, submission_files(
                submission, submitter=username, scorer_name=challenge.scorer_name, storage=self.storage
            ))
            for submission, username in rows
            if is_publishable(submission.type)
        ]


def _describe(exc: Exception) -> str:
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


_worker_thread: Optional[threading.Thread] = None
_stop = threading.Event()


def start_sync_worker(poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS) -> None:
    """Run the Sync Worker on a daemon thread, publishing to the GitHub App
    repo host and S3. Does nothing (and logs why) when they are not
    configured, so the arena still starts without GitHub credentials."""
    global _worker_thread
    if _worker_thread is not None and _worker_thread.is_alive():
        return
    import database
    from ports.object_storage import get_object_storage
    from ports.repo_host import get_repo_host

    try:
        worker = SyncWorker(
            session_factory=database.SessionLocal,
            storage=get_object_storage(),
            repo_host=get_repo_host(),
        )
    except Exception as exc:
        logger.warning("Sync Worker not started: %s", _describe(exc))
        return

    def loop() -> None:
        while not _stop.is_set():
            try:
                worker.run_once()
            except Exception:
                logger.exception("Sync Worker pass failed")
            _stop.wait(poll_interval_seconds)

    _stop.clear()
    _worker_thread = threading.Thread(target=loop, name="challenge-sync-worker", daemon=True)
    _worker_thread.start()
    logger.info("Sync Worker started")


def stop_sync_worker() -> None:
    _stop.set()
