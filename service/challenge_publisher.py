"""Challenge publisher: the public file set of a Challenge Repo.

``challenge_files`` is a function of the Challenge row and the public objects
in storage only, so a sync and a Resync produce the same files. It never
reads the Ground Truth. The set is the repo's whole tree (the Sync Worker
replaces the tree with it), so it includes the template's own files.
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from models.challenge import Challenge
from ports.object_storage import ObjectStorage
from service import scorer_catalogue
from service.challenge_data import sample_predictions_file

SCHEMA_VERSION = 1
CHALLENGE_JSON = "challenge.json"
README_PATH = "README.md"
INPUTS_PATH = "data/inputs.jsonl"
SAMPLE_SUBMISSION_PATH = "data/sample_submission.jsonl"

# Files of BuddhistAI/Challenge-template (branch rfc-challenge-repo-layout)
# that the publisher keeps as they are: repo path -> copy in this package.
# The .gitignore is stored without its dot so it does not apply here.
TEMPLATE_DIR = Path(__file__).parent / "challenge_repo_template"
TEMPLATE_FILES = {
    ".gitignore": "gitignore",
    "data/README.md": "data/README.md",
    "models/README.md": "models/README.md",
    "prompts/README.md": "prompts/README.md",
    "skills/README.md": "skills/README.md",
}

# Larger Inputs are linked from challenge.json instead of committed (GitHub
# refuses files over 100 MB and warns from 50 MB).
MAX_COMMITTED_INPUTS_BYTES = 50 * 1024**2


def challenge_files(
    challenge: Challenge,
    storage: ObjectStorage,
    *,
    max_committed_inputs_bytes: int = MAX_COMMITTED_INPUTS_BYTES,
) -> Dict[str, bytes]:
    """Path -> content of every file in the repo: the template's own files,
    then the Challenge's. Inputs larger than ``max_committed_inputs_bytes``
    are not among them; ``challenge.json`` links them instead."""
    inputs = storage.get(challenge.inputs_key)
    input_ids = scorer_catalogue.load_inputs(inputs).keys()
    commit_inputs = len(inputs) <= max_committed_inputs_bytes
    inputs_url = None if commit_inputs else storage.public_url(challenge.inputs_key)
    media_url = storage.public_url(challenge.media_archive_key) if challenge.media_archive_key else None
    definition = challenge_json(challenge, inputs_url=inputs_url, media_url=media_url)
    files = template_files()
    files.update({
        CHALLENGE_JSON: _json_bytes(definition),
        README_PATH: readme(definition).encode("utf-8"),
        SAMPLE_SUBMISSION_PATH: sample_predictions_file(challenge.scorer_name, input_ids),
    })
    if commit_inputs:
        files[INPUTS_PATH] = inputs
    return files


def template_files() -> Dict[str, bytes]:
    """The template files every Challenge Repo keeps unchanged."""
    return {path: (TEMPLATE_DIR / source).read_bytes() for path, source in TEMPLATE_FILES.items()}


def challenge_json(
    challenge: Challenge, *, inputs_url: Optional[str] = None, media_url: Optional[str] = None
) -> Dict[str, Any]:
    """The Challenge definition. ``inputs_url`` is set only when the Inputs
    are too large to commit (then ``data.inputs`` is null); ``media_url`` only
    when the Challenge has a Media Archive."""
    return {
        "schema_version": SCHEMA_VERSION,
        "id": str(challenge.id),
        "title": challenge.title,
        "description": challenge.description,
        "category": challenge.category.name if challenge.category is not None else None,
        "scorer": {
            "name": challenge.scorer_name,
            "label": challenge.scorer_label,
            "direction": challenge.direction,
            "params": challenge.scorer_params,
            "package_version": challenge.package_version,
        },
        "data": {
            "id_field": "id",
            "inputs": INPUTS_PATH if inputs_url is None else None,
            "inputs_url": inputs_url,
            "media_url": media_url,
            "sample_submission": SAMPLE_SUBMISSION_PATH,
        },
        "submission_format": {"file_type": "jsonl", "fields": ["id", "prediction"]},
    }


DIRECTION_PHRASES = {
    "lower_is_better": "lower is better",
    "higher_is_better": "higher is better",
}


def readme(definition: Dict[str, Any]) -> str:
    """The repo README, generated from ``challenge.json`` alone."""
    scorer = definition["scorer"]
    data = definition["data"]
    direction = DIRECTION_PHRASES.get(scorer["direction"], scorer["direction"])
    params = json.dumps(scorer["params"], ensure_ascii=False, sort_keys=True)

    if data["inputs_url"]:
        inputs_step = (
            f"Download the Inputs from {data['inputs_url']} (too large to commit to "
            "this repo; the link is also `data.inputs_url` in `challenge.json`)."
        )
        inputs_lines = []
    else:
        inputs_step = f"Download `{data['inputs']}`: one JSON object per line, `{{\"id\", \"input\"}}`."
        inputs_lines = [f"{data['inputs']:<34}the Inputs: what you run inference on"]
    media_step = ""
    if data["media_url"]:
        media_step = (
            f" Also download the Media Archive from {data['media_url']}; each Input's "
            "`media` field is a path inside it."
        )

    lines = [f"# {definition['title']}", ""]
    if definition.get("description"):
        lines += [definition["description"], ""]
    lines += [
        f"- Category: {definition.get('category') or '-'}",
        f"- Scorer: {scorer['label']} (`{scorer['name']}`), {direction}",
        f"- Scorer parameters: `{params}`",
        f"- Scored with buddhist-eval {scorer['package_version']}",
        "",
        "The Scorer and its parameters are fixed in `challenge.json`. Scorers live in",
        "[buddhist-eval](https://github.com/BuddhistAI/buddhist-eval).",
        "",
        "## How to take part",
        "",
        f"1. {inputs_step}{media_step}",
        "2. Produce one prediction per Input id, in the format of",
        f"   `{data['sample_submission']}` (one `{{\"id\", \"prediction\"}}` object per line,",
        "   exactly the Input ids, no more and no fewer).",
        "3. Submit on the BuddhistAI arena as a Model, Prompt or Skill. Scores and the",
        "   leaderboard are on the arena; this repo holds the public material.",
        "",
        "## Layout",
        "",
        "```",
        f"{'challenge.json':<34}the Challenge definition",
        f"{'README.md':<34}this file, generated from challenge.json",
        *inputs_lines,
        f"{data['sample_submission']:<34}the expected Predictions File format",
        f"{'models/, prompts/, skills/':<34}Submissions (filled by the arena)",
        "```",
        "",
        "## What is never in this repo",
        "",
        "- The Ground Truth. It stays in private storage and is read only by the",
        "  scoring backend.",
        "- Media files. When the Challenge has them, they are in one archive linked",
        "  at `data.media_url` in `challenge.json`.",
        "",
        "This repo is written by the BuddhistAI arena's Sync Worker. Sync is one way:",
        "edits made here directly are not read back and may be overwritten.",
        "",
    ]
    return "\n".join(lines)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
