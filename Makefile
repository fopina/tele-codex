lint:
	uv run ruff format
	uv run ruff check --fix

lint-check:
	uv run ruff format --diff
	uv run ruff check

test:
	if [ -n "$(GITHUB_RUN_ID)" ]; then \
		uv run pytest --cov --cov-report=xml --junitxml=junit.xml -o junit_family=legacy; \
	else \
		uv run python -m pytest --cov; \
	fi

testpub:
	rm -fr dist
	uv run pyproject-build
	uv run twine upload --repository testpypi dist/*

dev:
	find telecodex -name '*.py' | entr -r uv run telecodex
