.PHONY: install test validate sync-spec build clean update-spec

install:
	pip install -e .

test:
	pytest -q

# Validate the canonical golden example (full: SHACL + mlcroissant)
validate:
	fair2-validate fair2-spec/examples/example-1/borja2025.json

# Vendor the pinned fair2-spec into the package (src/fair2_validator/_spec_data)
# so a plain `pip install` is self-contained. Commit the result.
sync-spec:
	python scripts/sync_spec.py

# Build sdist + wheel for PyPI. Always re-vendors the spec first so the
# artifacts can never ship a stale copy.
build: sync-spec
	python -m pip install --upgrade build twine
	rm -rf dist
	python -m build
	python -m twine check dist/*
	@echo "Verifying the spec data is inside the wheel:"
	@python -c "import zipfile,glob; w=glob.glob('dist/*.whl')[0]; n=[x for x in zipfile.ZipFile(w).namelist() if '_spec_data' in x]; print(chr(10).join(n)); assert n, 'no _spec_data in wheel!'"

clean:
	rm -rf dist build src/*.egg-info

# Test-gated spec bump: advance the pinned fair2-spec only if the golden
# example still conforms + loads and the test suite passes, then re-vendor.
update-spec:
	git submodule update --remote fair2-spec
	@echo "fair2-spec now at: $$(git -C fair2-spec describe --tags --always)"
	$(MAKE) test
	$(MAKE) validate
	$(MAKE) sync-spec
	git add fair2-spec src/fair2_validator/_spec_data
	@echo "Spec updated, vendored, and verified. Commit the bump when ready:"
	@echo "  git commit -m 'Bump fair2-spec to '$$(git -C fair2-spec describe --tags --always)"
