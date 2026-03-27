#!/usr/bin/env bash
# run_all.sh — sequentially run all 4 AI trading agents
set -euo pipefail

echo "=========================================="
echo " OKX Agent Trade Kit — Running All Agents"
echo "=========================================="

echo ""
echo "[1/4] Agent E: Funding Rate Harvester"
echo "--------------------------------------"
python agents/agent_e_funding_harvester.py

echo ""
echo "[2/4] Agent F: Flash Crash Hunter"
echo "--------------------------------------"
python agents/agent_f_flash_crash_hunter.py

echo ""
echo "[3/4] Agent G: Momentum Rotation Engine"
echo "--------------------------------------"
python agents/agent_g_momentum_rotation.py

echo ""
echo "[4/4] Agent H: Options IV Hunter"
echo "--------------------------------------"
python agents/agent_h_options_iv_hunter.py

echo ""
echo "=========================================="
echo " All agents completed."
echo "=========================================="
