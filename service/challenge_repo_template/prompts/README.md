# Prompt submissions

One folder per submission, named `<slug>-<submission id>`, written by the sync
worker:

- `prompt.md`: the prompt. A typed prompt and an uploaded file are both saved
  under this name. The prompt receives each input through the placeholder
  recorded in `input_placeholder` (for example `{input}`).
- `submission.json`: metadata, including the exact model id and generation
  settings the prompt was run with, so others can reproduce the score.

```json
{
  "schema_version": 1,
  "submission_id": "...",
  "type": "prompt",
  "name": "...",
  "description": "...",
  "submitter": "...",
  "headline_metric": 0.123,
  "preferred_model": "exact-model-id",
  "generation_settings": { "temperature": 0, "max_tokens": 1024 },
  "input_placeholder": "{input}",
  "license": "CC-BY-4.0",
  "created_at": "..."
}
```

Scores are self-reported in v1: the predictions are scored, but nobody has
rerun the prompt to confirm they came from it.
