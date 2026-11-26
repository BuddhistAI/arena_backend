"""Challenge repo host acting as the BuddhistAI GitHub App.

The App is installed on the BuddhistAI org (installation ``183641402``) with
Administration write (create and archive repos), Contents write and Metadata
read. Repos are generated from the template repo, which must be marked as a
template repository on GitHub.

Configuration (see ``env.example``):

- ``GITHUB_APP_ID``: the App's id.
- ``GITHUB_APP_PRIVATE_KEY``: the App's private key (PEM). Newlines may be
  written as ``\\n``. Or ``GITHUB_APP_PRIVATE_KEY_PATH``: a file holding it.
- ``GITHUB_APP_INSTALLATION_ID``: the installation on the org (183641402).
- ``GITHUB_CHALLENGE_ORG``: owner of the Challenge Repos (``BuddhistAI``).
- ``GITHUB_CHALLENGE_TEMPLATE``: ``owner/name`` of the template
  (``BuddhistAI/Challenge-template``).

Not exercised by the test suite, which uses ``InMemoryRepoHost``.
"""

import base64
import os
import threading
import time
from datetime import datetime
from typing import Any, Mapping, Optional

import jwt
import requests

from ports.repo_host import FileContent, RepoAlreadyExists, RepoHostError, RepoNotFound

API_URL = "https://api.github.com"
API_VERSION = "2022-11-28"
DEFAULT_OWNER = "BuddhistAI"
DEFAULT_TEMPLATE = "BuddhistAI/Challenge-template"
REQUEST_TIMEOUT_SECONDS = 30
# A repo generated from a template gets its first commit asynchronously.
WAIT_FOR_NEW_REPO_SECONDS = 20
DESCRIPTION_MAX_LENGTH = 350


class GitHubAppRepoHost:
    def __init__(
        self,
        *,
        app_id: str,
        private_key: str,
        installation_id: str,
        owner: str = DEFAULT_OWNER,
        template: str = DEFAULT_TEMPLATE,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.app_id = str(app_id)
        self.private_key = private_key
        self.installation_id = str(installation_id)
        self.owner = owner
        self.template = template
        self.session = session or requests.Session()
        self._token: Optional[str] = None
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "GitHubAppRepoHost":
        app_id = os.getenv("GITHUB_APP_ID")
        installation_id = os.getenv("GITHUB_APP_INSTALLATION_ID")
        private_key = os.getenv("GITHUB_APP_PRIVATE_KEY")
        key_path = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH")
        if not private_key and key_path:
            with open(key_path, encoding="utf-8") as key_file:
                private_key = key_file.read()
        missing = [
            name
            for name, value in (
                ("GITHUB_APP_ID", app_id),
                ("GITHUB_APP_PRIVATE_KEY or GITHUB_APP_PRIVATE_KEY_PATH", private_key),
                ("GITHUB_APP_INSTALLATION_ID", installation_id),
            )
            if not value
        ]
        if missing:
            raise RuntimeError("The GitHub App is not configured: set " + ", ".join(missing))
        return cls(
            app_id=app_id,
            private_key=private_key.replace("\\n", "\n"),
            installation_id=installation_id,
            owner=os.getenv("GITHUB_CHALLENGE_ORG") or DEFAULT_OWNER,
            template=os.getenv("GITHUB_CHALLENGE_TEMPLATE") or DEFAULT_TEMPLATE,
        )

    # RepoHost

    def create_repo_from_template(self, name: str, description: str = "") -> None:
        response = self._call(
            "POST",
            f"/repos/{self.template}/generate",
            json={
                "owner": self.owner,
                "name": name,
                "description": description[:DESCRIPTION_MAX_LENGTH],
                "private": False,
                "include_all_branches": False,
            },
            ok=(201, 422),
        )
        if response.status_code == 422:
            if "already exists" in response.text:
                raise RepoAlreadyExists(name)
            raise _error("POST", f"/repos/{self.template}/generate", response)

    def replace_files(self, name: str, files: Mapping[str, FileContent], message: str) -> None:
        # The new tree has no base_tree, so it holds exactly ``files``: any
        # other file (dropped from the set, or added by hand) is removed.
        repo = f"/repos/{self.owner}/{name}"
        branch = self._call("GET", repo).json()["default_branch"]
        head_sha = self._head_sha(repo, branch)
        head_tree = self._call("GET", f"{repo}/git/commits/{head_sha}").json()["tree"]["sha"]
        entries = []
        for path, content in files.items():
            raw = content.encode("utf-8") if isinstance(content, str) else content
            blob = self._call(
                "POST",
                f"{repo}/git/blobs",
                json={"content": base64.b64encode(raw).decode("ascii"), "encoding": "base64"},
                ok=(201,),
            ).json()
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree = self._call("POST", f"{repo}/git/trees", json={"tree": entries}, ok=(201,)).json()
        if tree["sha"] == head_tree:
            return  # the repo already holds exactly these files
        commit = self._call(
            "POST",
            f"{repo}/git/commits",
            json={"message": message, "tree": tree["sha"], "parents": [head_sha]},
            ok=(201,),
        ).json()
        self._call("PATCH", f"{repo}/git/refs/heads/{branch}", json={"sha": commit["sha"]})

    def archive_repo(self, name: str) -> None:
        self._call("PATCH", f"/repos/{self.owner}/{name}", json={"archived": True})

    # Helpers

    def _head_sha(self, repo: str, branch: str) -> str:
        deadline = time.monotonic() + WAIT_FOR_NEW_REPO_SECONDS
        while True:
            response = self._call("GET", f"{repo}/git/ref/heads/{branch}", ok=(200, 404, 409))
            if response.status_code == 200:
                return response.json()["object"]["sha"]
            # 404/409: the repo is still being generated from the template.
            if time.monotonic() >= deadline:
                raise _error("GET", f"{repo}/git/ref/heads/{branch}", response)
            time.sleep(2)

    def _call(self, method: str, path: str, *, ok=(200,), json: Any = None) -> requests.Response:
        headers = {
            "Authorization": f"Bearer {self._installation_token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }
        try:
            response = self.session.request(
                method, API_URL + path, headers=headers, json=json, timeout=REQUEST_TIMEOUT_SECONDS
            )
        except requests.RequestException as exc:
            raise RepoHostError(f"{method} {path}: {exc}") from exc
        if response.status_code in ok:
            return response
        if response.status_code == 404:
            raise RepoNotFound(f"{method} {path}: not found")
        raise _error(method, path, response)

    def _installation_token(self) -> str:
        with self._token_lock:
            if self._token and time.time() < self._token_expires_at - 60:
                return self._token
            path = f"/app/installations/{self.installation_id}/access_tokens"
            try:
                response = self.session.post(
                    API_URL + path,
                    headers={
                        "Authorization": f"Bearer {self._app_jwt()}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": API_VERSION,
                    },
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            except requests.RequestException as exc:
                raise RepoHostError(f"POST {path}: {exc}") from exc
            if response.status_code != 201:
                raise _error("POST", path, response)
            body = response.json()
            self._token = body["token"]
            self._token_expires_at = _parse_time(body["expires_at"])
            return self._token

    def _app_jwt(self) -> str:
        now = int(time.time())
        # Backdated for clock drift; GitHub accepts at most 10 minutes.
        claims = {"iat": now - 60, "exp": now + 540, "iss": self.app_id}
        return jwt.encode(claims, self.private_key, algorithm="RS256")


def _parse_time(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _error(method: str, path: str, response: requests.Response) -> RepoHostError:
    try:
        message = response.json().get("message") or response.text
    except ValueError:
        message = response.text
    return RepoHostError(f"{method} {path}: HTTP {response.status_code}: {message[:500]}")
