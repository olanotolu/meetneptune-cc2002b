.PHONY: calibrate check fill lint test types sign-check

PY = uv run python

# one-command smoke for a clean review machine: `uv sync` first
test:
	$(PY) -m unittest discover -s tests -v

lint:
	uv run ruff check .

types:
	uv run mypy

# Re-measure what the renderers produce. Run this after a pymupdf/pypdfium2
# bump, then read the margin diff before committing it — that diff is the
# review artifact for the bump.
calibrate:
	$(PY) tools/calibrate_raster.py

fill:
	$(PY) cc2002b.py samples/01_party_short.json outputs/01_party_short.pdf
	$(PY) cc2002b.py samples/02_relation_extended.json outputs/02_relation_extended.pdf
	$(PY) cc2002b.py samples/03_law_enforcement.json outputs/03_law_enforcement.pdf

check: test
	$(PY) cc2002b.py --check outputs/01_party_short.pdf samples/01_party_short.json
	$(PY) cc2002b.py --check outputs/02_relation_extended.pdf samples/02_relation_extended.json
	$(PY) cc2002b.py --check outputs/03_law_enforcement.pdf samples/03_law_enforcement.json

# End to end on an ephemeral key: sign a filing, then verify it the way a
# third party would — pinned public key, no payload, no blank, no repo.
sign-check:
	@tmp=$$(mktemp -d); \
	key=$$($(PY) cc2002b.py --keygen 2>/dev/null); \
	export CC2002B_SIGNING_KEY=$$key; \
	pub=$$($(PY) cc2002b.py --pubkey); \
	$(PY) cc2002b.py samples/01_party_short.json $$tmp/signed.pdf; \
	unset CC2002B_SIGNING_KEY; \
	$(PY) cc2002b.py --verify $$tmp/signed.pdf $$pub; \
	rm -rf $$tmp
