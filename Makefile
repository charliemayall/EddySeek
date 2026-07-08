ifneq (,$(wildcard .env))
include .env
export
endif

.PHONY: test clean format deploy menuconfig


clean:
	rm -rf .pytest_cache .ruff_cache

format:
	uv run ruff format
	uv run ruff check --fix

test: 
	uv run ty check
	uv run pytest

menuconfig:
	@echo "I think you are in the wrong directory :)"

# Helper to deploy to a klipper host using .env vars.
deploy: 
	@echo "Deploying to $(KLIPPER_H)..."

	rsync -avz --delete src/ $(KLIPPER_H):~/EddySeek/src/
	ssh $(KLIPPER_H) 'cd ~/EddySeek && ./install.sh && printf "%s\n" "$(KLIPPER_P)" | sudo -S systemctl restart klipper'
