.PHONY: install lint test clean

install:
	pip install -r requirements.txt
	pip install flake8 pytest

lint:
	@echo "Checking syntax..."
	@python -c "import ast; ast.parse(open('app.py').read())" && echo "  app.py: OK"
	@echo "Running flake8..."
	@flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
	@flake8 . --count --exit-zero --max-line-length=120 --statistics

test:
	pytest tests/ -v

check: lint test

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
