.PHONY: setup test lint check dry-run oidc-inventory eval-prepare
setup:
	uv sync --frozen
test:
	uv run --no-sync pytest -q
lint:
	uv run --no-sync ruff check src tests scripts .github/infra-reviewer/review.py
check: lint test
dry-run:
	uv run --no-sync python .github/infra-reviewer/review.py $(REPO) $(PR) --dry-run
oidc-inventory:
	uv run --no-sync python scripts/check_oidc.py
eval-prepare:
	uv run --no-sync python -m reviewer.infra.evaluate prepare --output artifacts/evaluation
