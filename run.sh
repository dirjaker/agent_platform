#!/bin/bash
cd "$(dirname "$0")"
source activate agent_platform 2>/dev/null || conda activate agent_platform 2>/dev/null
python api.py
