ifneq (,$(wildcard .env))
include .env
export
endif

.PHONY: test clean format deploy 


clean:
	rm -rf .pytest_cache .ruff_cache

# Run formatting.
format:
	uv run ruff format .

# Run tests.
test: clean
	uv run ruff check .
	uv run pytest

# Helper to deploy to a klipper host using .env vars.
deploy: 
	@echo "Deploying to $(KLIPPER_H)..."

	rsync -avz --delete src/ $(KLIPPER_H):~/EddySeek/src/
	ssh $(KLIPPER_H) 'cd ~/EddySeek && ./install.sh && printf "%s\n" "$(KLIPPER_P)" | sudo -S systemctl restart klipper'
