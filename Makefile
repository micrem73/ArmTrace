# One command for "done": lint, then the full suite. test-fast is for the edit loop.
# The golden tests need the sources in In/ (bash In/download_185.sh In In/manifest.tsv).
.PHONY: check lint test-fast test

check: lint test

lint:
	python3 -m ruff check .

test-fast:
	python3 -m pytest -q tests/test_traps.py tests/test_recheck.py tests/test_table_list.py

test:
	python3 -m pytest -q tests/
