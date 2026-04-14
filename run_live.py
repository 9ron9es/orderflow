#!/usr/bin/env python3
"""
Simple wrapper to run the live trader - avoids module path issues.

Usage:
    python run_live.py --config nautilus/config/profiles/live.yaml
"""

import sys
from pathlib import Path

# Add current directory to path so nautilus can be imported
sys.path.insert(0, str(Path(__file__).parent))

from nautilus.runners.live import run_live, main

if __name__ == "__main__":
    main()
