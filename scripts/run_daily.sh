#!/usr/bin/env bash
# Execução diária (Linux/macOS). Chamado pelo cron — ver install_cron.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/logs
if [ -f .env ]; then set -a; . ./.env; set +a; fi
if [ -d .venv ]; then . .venv/bin/activate; fi
python -m jobhunter run >> "data/logs/$(date +%F).log" 2>&1
