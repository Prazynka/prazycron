PYTHON ?= python3

.PHONY: test compile smoke check deb release clean

test:
	$(PYTHON) -m unittest discover -s tests -v

compile:
	$(PYTHON) -m compileall -q prazycron

smoke:
	xvfb-run -a $(PYTHON) -m prazycron.main --gui --smoke-test

check:
	./scripts/check-release.sh

deb:
	./build-deb.sh

release:
	./scripts/make-release.sh

clean:
	rm -rf build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
