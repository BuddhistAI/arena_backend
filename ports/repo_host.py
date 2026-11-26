"""Challenge repo host port: create a repo from the template, write a set of
files in one commit, archive a repo.

The GitHub App implementation comes with the Sync Worker (ticket 06); until
then ``get_repo_host`` has nothing to return in production. Routes take the
port with ``Depends(get_repo_host)``; tests override that dependency with
``InMemoryRepoHost``.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Protocol, Union

FileContent = Union[str, bytes]

# Paths a repo generated from BuddhistAI/Challenge-template starts with.
TEMPLATE_PATHS = (
    "README.md",
    "challenge.json",
    "data/README.md",
    "models/README.md",
    "prompts/README.md",
    "skills/README.md",
)


class RepoHostError(Exception):
    """The repo host could not complete a call (outage, permissions, ...)."""


class RepoAlreadyExists(RepoHostError):
    pass


class RepoNotFound(RepoHostError):
    pass


class RepoHost(Protocol):
    def create_repo_from_template(self, name: str, description: str = "") -> None:
        """Create public repo ``name`` from the Challenge template. Raises
        ``RepoAlreadyExists`` if it is already there."""

    def write_files(self, name: str, files: Mapping[str, FileContent], message: str) -> None:
        """Add or overwrite ``files`` (path -> content) in a single commit."""

    def archive_repo(self, name: str) -> None:
        """Make the repo read-only."""


@dataclass
class Commit:
    message: str
    files: Dict[str, bytes]


@dataclass
class FakeRepo:
    name: str
    description: str
    files: Dict[str, bytes]
    commits: List[Commit] = field(default_factory=list)
    archived: bool = False


def _as_bytes(content: FileContent) -> bytes:
    return content.encode("utf-8") if isinstance(content, str) else content


class InMemoryRepoHost:
    """Fake for tests. ``repos`` maps each repo name to a ``FakeRepo`` holding
    its files, commits and archived flag. ``fail_next(n)`` makes the next n
    calls raise ``RepoHostError`` without changing anything."""

    def __init__(self) -> None:
        self.repos: Dict[str, FakeRepo] = {}
        self._failures_left = 0
        self._failure_message = ""

    def fail_next(self, n: int, message: str = "simulated repo host failure") -> None:
        self._failures_left = n
        self._failure_message = message

    def _maybe_fail(self) -> None:
        if self._failures_left > 0:
            self._failures_left -= 1
            raise RepoHostError(self._failure_message)

    def _repo(self, name: str) -> FakeRepo:
        try:
            return self.repos[name]
        except KeyError:
            raise RepoNotFound(name) from None

    def create_repo_from_template(self, name: str, description: str = "") -> None:
        self._maybe_fail()
        if name in self.repos:
            raise RepoAlreadyExists(name)
        files = {path: b"template\n" for path in TEMPLATE_PATHS}
        self.repos[name] = FakeRepo(name=name, description=description, files=files)

    def write_files(self, name: str, files: Mapping[str, FileContent], message: str) -> None:
        self._maybe_fail()
        repo = self._repo(name)
        written = {path: _as_bytes(content) for path, content in files.items()}
        repo.files.update(written)
        repo.commits.append(Commit(message=message, files=written))

    def archive_repo(self, name: str) -> None:
        self._maybe_fail()
        self._repo(name).archived = True


def get_repo_host() -> RepoHost:
    """FastAPI dependency for the challenge repo host port."""
    raise RuntimeError("No challenge repo host is configured (GitHub App lands in ticket 06)")
