.PHONY: dev install test freeze clean

# Run FastAPI dev server with hot-reload on port 8000
dev:
	.venv/bin/uvicorn main:app --reload --port 8000

# Install/update dependencies into local venv
install:
	.venv/bin/pip install -r requirements.txt

# Run pytest suite
test:
	.venv/bin/pytest

# Snapshot current venv packages to requirements.txt
freeze:
	.venv/bin/pip freeze > requirements.txt

# Clear bytecode caches
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
