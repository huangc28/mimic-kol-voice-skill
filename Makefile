.PHONY: chrome ingest setup

# Launch Chrome with CDP remote debugging enabled
chrome:
	@echo "🚀 Launching Chrome with remote debugging on port 9222..."
	@/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
		--remote-debugging-port=9222 \
		--remote-allow-origins=* &

# Run the ingest script (requires Chrome running via 'make chrome')
# Usage: make ingest HANDLE=marclou LIMIT=200
HANDLE ?=
LIMIT ?= 200
DELAY ?= 2.0
PORT ?= 9222
OUT ?= artifacts/kol/$(HANDLE)

ingest:
ifndef HANDLE
	@echo "❌ HANDLE is required. Usage: make ingest HANDLE=marclou"
	@exit 1
endif
	@source .venv/bin/activate && python skills/ingest/scripts/ingest.py \
		--handle $(HANDLE) \
		--limit $(LIMIT) \
		--delay $(DELAY) \
		--port $(PORT) \
		--out $(OUT)

# First-time setup: create venv and install dependencies
setup:
	python3 -m venv .venv
	source .venv/bin/activate && pip install websocket-client
	@echo "✅ Setup complete. Run 'make chrome' then 'make ingest HANDLE=marclou'"
