.PHONY: check fill test

# one-command smoke for a clean review machine (venv already installed)
test:
	python -m unittest discover -s tests -v

fill:
	python cc2002b.py samples/01_party_short.json outputs/01_party_short.pdf
	python cc2002b.py samples/02_relation_extended.json outputs/02_relation_extended.pdf
	python cc2002b.py samples/03_law_enforcement.json outputs/03_law_enforcement.pdf

check: test
	python cc2002b.py --check outputs/01_party_short.pdf samples/01_party_short.json
	python cc2002b.py --check outputs/02_relation_extended.pdf samples/02_relation_extended.json
	python cc2002b.py --check outputs/03_law_enforcement.pdf samples/03_law_enforcement.json
