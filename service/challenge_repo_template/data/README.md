# Data

Written by the sync worker when the challenge is created.

- `inputs.jsonl`: one JSON object per line,
  `{"id": "...", "input": <string or object>, "media": "<path>"}`. `media` is
  present only when the input is an image or audio file; it is a path inside
  the media archive linked at `data.media_url` in `challenge.json`. When the
  inputs are larger than 50 MB they are not committed; `challenge.json` then
  gives their download link in `data.inputs_url`.
- `sample_submission.jsonl`: one line per input id,
  `{"id": "...", "prediction": <value>}`, with a placeholder prediction in the
  shape the challenge's scorer expects (a string for CER, a list of spans for
  span F1, and so on). A predictions file must have exactly these ids, no more
  and no fewer.

The ground truth has the same ids but is never published.
