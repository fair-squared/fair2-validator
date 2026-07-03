.PHONY: install test validate update-spec

install:
	pip install -e .

test:
	pytest -q

# Validate the canonical golden example (full: SHACL + mlcroissant)
validate:
	fair2-validate fair2-spec/examples/example-1/borja2025.json

# Test-gated spec bump: advance the pinned fair2-spec only if the golden
# example still conforms + loads and the test suite passes.
update-spec:
	git submodule update --remote fair2-spec
	@echo "fair2-spec now at: $$(git -C fair2-spec describe --tags --always)"
	$(MAKE) test
	$(MAKE) validate
	git add fair2-spec
	@echo "Spec updated and verified. Commit the submodule bump when ready:"
	@echo "  git commit -m 'Bump fair2-spec to '$$(git -C fair2-spec describe --tags --always)"
