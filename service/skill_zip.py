"""Skill zip checker: safety checks on a Skill's zip before it is stored.

    problems = check_skill_zip(zip_bytes)   # [] when the zip is safe

A Skill zip is later extracted and published to a public Challenge Repo, so
the checks make sure extracting it can never write outside its folder, never
publish a binary or a nested archive, and never blow up in size. They read
the archive in memory and never write it to disk. There are no structure
checks: any layout is accepted, SKILL.md is not required.

Each problem names the entry it concerns (when it concerns one) and the rule
it broke.
"""

from __future__ import annotations

import io
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import List, Optional, Tuple

MAX_ZIP_BYTES = 10 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 25 * 1024 * 1024
MAX_ENTRIES = 200
ALLOWED_EXTENSIONS: Tuple[str, ...] = (
    ".md", ".txt", ".py", ".sh", ".js", ".ts", ".json", ".jsonl", ".yaml", ".yml", ".csv",
)
MAX_REPORTED_PROBLEMS = 20

_READ_CHUNK = 64 * 1024
_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class ZipProblem:
    """Why a Skill zip is refused: the rule broken, and the entry if one is to blame."""

    rule: str
    entry: Optional[str] = None

    @property
    def message(self) -> str:
        """The problem as one sentence naming the entry first."""
        return f"'{self.entry}': {self.rule}" if self.entry is not None else self.rule


def check_skill_zip(data: bytes) -> List[ZipProblem]:
    """Check a Skill zip against every safety rule, without extracting it to disk.

    Args:
        data: The uploaded zip, as bytes.

    Returns:
        Every problem found (at most ``MAX_REPORTED_PROBLEMS`` entry problems),
        or an empty list if the zip is safe to store and extract.
    """
    if len(data) > MAX_ZIP_BYTES:
        return [ZipProblem(f"the zip is {_megabytes(len(data))}; the limit is {_megabytes(MAX_ZIP_BYTES)}")]
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return [ZipProblem("is not a valid zip file")]
    with archive:
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES:
            return [ZipProblem(f"the zip holds {len(entries)} entries; the limit is {MAX_ENTRIES}")]
        problems = [problem for info in entries for problem in _entry_problems(info)]
        if problems:
            return problems[:MAX_REPORTED_PROBLEMS]
        return _size_problems(archive, entries)


def _entry_problems(info: zipfile.ZipInfo) -> List[ZipProblem]:
    """What is unsafe about one entry's name and kind."""
    name = info.filename
    if _is_symlink(info):
        return [ZipProblem("symlinks are not allowed", name)]
    parts = re.split(r"[/\\]", name)
    if name.startswith(("/", "\\")) or _DRIVE_LETTER.match(name):
        return [ZipProblem("absolute paths are not allowed", name)]
    if ".." in parts:
        return [ZipProblem("'..' paths are not allowed", name)]
    if info.is_dir():
        return []
    extension = PurePosixPath(name.replace("\\", "/")).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        shown = f"'{extension}'" if extension else "with no extension"
        return [ZipProblem(f"file type {shown} is not allowed; allowed: {' '.join(ALLOWED_EXTENSIONS)}", name)]
    return []


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def _size_problems(archive: zipfile.ZipFile, entries: List[zipfile.ZipInfo]) -> List[ZipProblem]:
    """The total uncompressed size is within the limit, both as declared in the
    zip and as actually decompressed (headers can lie), and every entry reads."""
    declared = sum(info.file_size for info in entries)
    if declared > MAX_UNCOMPRESSED_BYTES:
        return [_too_big(declared)]
    total = 0
    for info in entries:
        try:
            total = _read_counting(archive, info, total)
        except zipfile.BadZipFile:
            return [ZipProblem("is corrupt and cannot be read", info.filename)]
        except RuntimeError:
            return [ZipProblem("is encrypted; encrypted zips are not allowed", info.filename)]
        except NotImplementedError:
            return [ZipProblem("uses an unsupported compression method", info.filename)]
        if total > MAX_UNCOMPRESSED_BYTES:
            return [_too_big(total, at_least=True)]
    return []


def _read_counting(archive: zipfile.ZipFile, info: zipfile.ZipInfo, total: int) -> int:
    """Decompress one entry in memory, adding its size to ``total``; stops
    early once ``total`` passes the limit."""
    with archive.open(info) as entry:
        while chunk := entry.read(_READ_CHUNK):
            total += len(chunk)
            if total > MAX_UNCOMPRESSED_BYTES:
                break
    return total


def _too_big(size: int, at_least: bool = False) -> ZipProblem:
    amount = f"more than {_megabytes(MAX_UNCOMPRESSED_BYTES)}" if at_least else _megabytes(size)
    return ZipProblem(f"the zip extracts to {amount}; the limit is {_megabytes(MAX_UNCOMPRESSED_BYTES)}")


def _megabytes(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} MB" if size % (1024 * 1024) else f"{size // (1024 * 1024)} MB"
