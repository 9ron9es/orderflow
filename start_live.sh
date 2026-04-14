#!/bin/bash
cd /home/adem/orderflow
set -a
source .env
set +a
python run_live.py --config nautilus/config/profiles/live.yaml
