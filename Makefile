.PHONY: install build dev test-watch test format lint clean

install:
	uv sync

build:
	uv build
	cd lsmsg-rs && maturin build --release

dev:
	uv run langgraph dev --no-browser

test-watch:
	uv run ptw -- -x -q --tb=short tests/ --ignore=tests/integration

test:
	uv run pytest tests/ -v

format:
	uv run ruff format src tests
	uv run ruff check --fix src tests
	cd lsmsg-rs && cargo fmt

lint:
	uv run ruff check src tests
	uv run ty check src
	cd lsmsg-rs && cargo clippy -- -D warnings

clean:
	rm -rf dist/ .venv/
	rm -rf lsmsg-rs/dist/ lsmsg-rs/.venv/ lsmsg-rs/target/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.so" -delete 2>/dev/null || true
	find . -name "*.dylib" -delete 2>/dev/null || true
	find . -name "*.dSYM" -exec rm -rf {} + 2>/dev/null || true
