.PHONY: test coverage repo-check check

test:
	python3 -m pytest

coverage:
	python3 -m pytest --cov=vpnchain --cov-report=xml --cov-report=term

repo-check:
	bin/vpnchain repo-check .

check: test repo-check

