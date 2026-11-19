PY = .venv/bin/python

install:
	python3 -m venv .venv
	$(PY) -m pip install -r requirements.txt

test:
	$(PY) -m pytest -v

eval:
	$(PY) eval.py

.PHONY: install test eval
