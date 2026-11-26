# Skill submissions

One folder per submission, named `<slug>-<submission id>`, written by the sync
worker. The uploaded zip is extracted here so the code can be read on GitHub
before anyone runs it:

```
<slug>-<id>/
  SKILL.md            required, at the folder root
  scripts/...         any scripts the skill ships
  submission.json     metadata, preferred model, generation settings
```

```json
{
  "schema_version": 1,
  "submission_id": "...",
  "type": "skill",
  "name": "...",
  "description": "...",
  "submitter": "...",
  "headline_metric": 0.123,
  "preferred_model": "exact-model-id",
  "generation_settings": { "temperature": 0, "max_tokens": 1024 },
  "license": "CC-BY-4.0",
  "created_at": "..."
}
```

Read a skill's scripts before running them. Scores are self-reported in v1.
