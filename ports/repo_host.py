"""Challenge repo host port: create a repo from the template, replace a repo's
files with a given set in one commit, archive a repo.

Only the Sync Worker uses it: in production ``start_sync_worker`` gets the
GitHub App implementation (``ports.github_app_repo_host``), configured from
the environment, from ``get_repo_host``. Tests hand the worker an
``InMemoryRepoHost`` instead.
"""

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Mapping, Protocol, Union

FileContent = Union[str, bytes]

# Paths a repo generated from BuddhistAI/Challenge-template starts with.
TEMPLATE_PATHS = (
    ".gitignore",
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

    def replace_files(self, name: str, files: Mapping[str, FileContent], message: str) -> None:
        """Make ``files`` (path -> content) the repo's whole tree in a single
        commit: every other file, including any added by hand, is removed."""

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

    def replace_files(self, name: str, files: Mapping[str, FileContent], message: str) -> None:
        self._maybe_fail()
        repo = self._repo(name)
        written = {path: _as_bytes(content) for path, content in files.items()}
        repo.files = dict(written)
        repo.commits.append(Commit(message=message, files=written))

    def archive_repo(self, name: str) -> None:
        self._maybe_fail()
        self._repo(name).archived = True


DEFAULT_CHALLENGE_ORG = "BuddhistAI"


def challenge_repo_url(repo_name: str) -> str:
    """Public GitHub URL of the Challenge Repo ``repo_name``, under the org in
    ``GITHUB_CHALLENGE_ORG`` (``BuddhistAI`` by default)."""
    owner = os.getenv("GITHUB_CHALLENGE_ORG") or DEFAULT_CHALLENGE_ORG
    return f"https://github.com/{owner}/{repo_name}"


def challenge_repo_folder_url(repo_name: str, folder: str) -> str:
    """Public GitHub URL of a folder in a Challenge Repo, on its default branch.

    Args:
        repo_name: The Challenge Repo's name.
        folder: The folder's path in the repo.

    Returns:
        The folder's URL on GitHub.
    """
    return f"{challenge_repo_url(repo_name)}/tree/HEAD/{folder}"


@lru_cache()
def _github_app_repo_host() -> RepoHost:
    from ports.github_app_repo_host import GitHubAppRepoHost

    return GitHubAppRepoHost.from_env()


def get_repo_host() -> RepoHost:
    """FastAPI dependency for the challenge repo host port: the BuddhistAI
    GitHub App. Raises ``RuntimeError`` when the App is not configured."""
    return _github_app_repo_host()
