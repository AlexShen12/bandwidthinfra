# Standalone entry point

Run from the repository root after `uv sync --frozen`:

```sh
uv run --no-sync python .github/infra-reviewer/review.py OWNER/REPO PR --dry-run
```

`review.py` delegates to `reviewer.infra`. `prompt.md` is the versioned system prompt. The model returns `infra-review/v1`; Python validates evidence, handles GitHub writes, and renders trusted currency. See [the canonical implementation document](../../infrastructureagent.md).
