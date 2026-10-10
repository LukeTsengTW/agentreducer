"""Toy failure depends only on the simultaneous presence of two files."""

from pathlib import Path

raise SystemExit(1 if Path('src/auth.py').exists() and Path('src/cache.py').exists() else 0)
