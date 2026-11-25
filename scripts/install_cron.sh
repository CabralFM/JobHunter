#!/usr/bin/env bash
# Agenda a execução diária no cron (Linux/macOS). Uso: scripts/install_cron.sh 07:15
set -euo pipefail
TIME="${1:-07:15}"
H="${TIME%%:*}"; M="${TIME##*:}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
LINE="$M $H * * * $DIR/scripts/run_daily.sh"
( crontab -l 2>/dev/null | grep -v "jobhunter\|run_daily.sh" ; echo "$LINE # jobhunter" ) | crontab -
echo "Agendado: $LINE"
echo "macOS: o cron não roda com o Mac dormindo. Programe o Mac para acordar alguns minutos antes, ex.:"
echo "  sudo pmset repeat wakeorpoweron MTWRFSU 07:10:00"
