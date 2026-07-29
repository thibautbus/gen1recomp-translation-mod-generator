#!/usr/bin/env python3
"""Thin executable wrapper; use ``python scripts/pipeline.py ...`` from repo root."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
