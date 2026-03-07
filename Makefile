.PHONY: test test-rust test-python test-typescript test-go \
       lint format build clean dev

# --------------------------------------------------------------------------
# Testing
# --------------------------------------------------------------------------

test: test-rust test-python test-typescript test-go

test-rust:
	cargo test -p lsmsg-core -p lsmsg-ffi

test-python:
	cd sdks/python && uv run --with pytest --with pytest-asyncio --with httpx --with anyio \
		python -m pytest tests/ -q

test-typescript:
	cd sdks/typescript && npm test

test-go: build-ffi
	cd sdks/go && CGO_ENABLED=1 go test ./...

# --------------------------------------------------------------------------
# Lint & format
# --------------------------------------------------------------------------

lint:
	cargo fmt --check
	cargo clippy -p lsmsg-core -p lsmsg-ffi -- -D warnings
	cd sdks/python && uv run --with ruff ruff check src tests
	cd sdks/typescript && npm run build

format:
	cargo fmt
	cd sdks/python && uv run --with ruff ruff format src tests && uv run --with ruff ruff check --fix src tests

# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------

build-ffi:
	cargo build --release -p lsmsg-ffi

build-py:
	cd sdks/python && pip install maturin && maturin build --release

build-ts:
	cd sdks/typescript && npm run build

build: build-ffi build-py build-ts

# --------------------------------------------------------------------------
# Dev
# --------------------------------------------------------------------------

dev:
	uv run langgraph dev --no-browser

# --------------------------------------------------------------------------
# Clean
# --------------------------------------------------------------------------

clean:
	cargo clean
	rm -rf sdks/python/dist/ sdks/python/.venv/
	rm -rf sdks/typescript/dist/ sdks/typescript/node_modules/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
