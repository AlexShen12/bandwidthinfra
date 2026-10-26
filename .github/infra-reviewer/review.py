"""Run after `uv sync`: uv run python .github/infra-reviewer/review.py OWNER/REPO PR --dry-run."""

from reviewer.infra.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
