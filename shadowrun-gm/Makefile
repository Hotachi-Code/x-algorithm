.PHONY: help install install-all test lint demo clean

help:
	@echo "make install      core install (no dependencies)"
	@echo "make install-all  with ingest, embeddings and dev extras"
	@echo "make test         run the test suite"
	@echo "make demo         build a throwaway campaign in .demo/ and play 5 scenes"
	@echo "make clean        remove build artefacts and the demo"

install:
	pip install -e .

install-all:
	pip install -e '.[ingest,embed,dev]'

test:
	python -m pytest -q

demo:
	rm -rf .demo && mkdir -p .demo
	python -m shadowrun_gm.cli --root .demo init --campaign "Demo Run"
	python -m shadowrun_gm.cli --root .demo --llm-backend local --embed-backend hashing \
		-Q scene -n 5
	python -m shadowrun_gm.cli --root .demo graph

clean:
	rm -rf .demo build dist *.egg-info .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
