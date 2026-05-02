.PHONY: demo test lint install clean build

install:
	pip install -e ".[dev]"

demo:
	python examples/04_pause_and_fork.py

test:
	pytest tests/ --cov=agent_lens -v

test-unit:
	pytest tests/test_tracer.py tests/test_server.py tests/test_control.py -v

test-integration:
	pytest tests/integration/ -v

test-security:
	pytest tests/security/ -v

lint:
	ruff check agent_lens/ tests/

lint-fix:
	ruff check --fix agent_lens/ tests/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .coverage coverage.xml htmlcov dist build *.egg-info

build:
	python -m build

dashboard:
	agent-lens dashboard --port 7878

quickstart-openai:
	python examples/01_openai_quickstart.py

quickstart-anthropic:
	python examples/02_anthropic_tools.py

quickstart-langchain:
	python examples/03_langchain_chain.py
