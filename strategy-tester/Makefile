VENDORED_DIR := app/_vendored

.PHONY: check-vendored

check-vendored:
	@cd $(VENDORED_DIR) && sha256sum -c CHECKSUMS
