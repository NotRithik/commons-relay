#!/usr/bin/env python3
"""Entry point under isolated Python (-I). All importable code is bundled here."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from commons_relay.service import main
if __name__=='__main__':main()
