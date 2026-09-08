#!/usr/bin/env python3
"""Entrypoint for the bundled owner-side helper, launched with Python -I."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from commons_relay.owner_ui import main

if __name__ == '__main__':
    main()
