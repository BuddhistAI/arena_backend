# Model submissions

One folder per submission, named `<slug>-<submission id>`, written by the sync
worker. Each holds only `submission.json`; the model itself is at the link in
its `model_link` field (for example a Hugging Face model).

```json
{
  "schema_version": 1,
  "submission_id": "...",
  "type": "model",
  "name": "...",
  "description": "...",
  "submitter": "...",
  "headline_metric": 0.123,
  "model_link": "https://huggingface.co/...",
  "created_at": "..."
}
```
