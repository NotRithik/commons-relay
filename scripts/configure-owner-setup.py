#!/usr/bin/env python3
"""Install a reviewed local Create Agent runtime configuration; never create an agent.

The input is a private JSON object matching the documented setup configuration.
It contains public contact information and explicit runtime paths, never API keys.
The default action validates and previews. --apply REVIEW_HASH installs that exact
configuration only if both it and the previous configuration remain unchanged.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from commons_relay.agent_setup import read_json, validate_configuration, signature, save, locked
from commons_relay.codec import Rejected, canonical


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner-root', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--apply', metavar='REVIEW_HASH')
    args = parser.parse_args()
    root = args.owner_root.expanduser().absolute()
    if root.is_symlink() or not root.is_dir(): raise Rejected('OWNER_ROOT_REQUIRED')
    info = root.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise Rejected('OWNER_ROOT_NOT_PRIVATE')
    root = root.resolve(strict=True)
    candidate = validate_configuration(read_json(args.config.expanduser().absolute()))
    destination = root / '.setup-runtime.json'
    with locked(root / '.setup-configuration-lock'):
        if destination.is_symlink(): raise Rejected('SETUP_FILE_INVALID')
        previous = hashlib.sha256(destination.read_bytes()).hexdigest() if destination.exists() else None
        intent = {'owner_root': str(root), 'runtime_hash': signature(candidate),
                  'configuration_sha256': hashlib.sha256(canonical(candidate)).hexdigest(),
                  'previous_sha256': previous}
        digest = hashlib.sha256(canonical(intent)).hexdigest()
        report = {'review_hash': digest, 'installed': False, 'destination': str(destination),
                  'creates_agent': False, 'requests_funds': False, 'model_calls': 0,
                  'local_cluster_id': candidate['cluster_id'],
                  'planner_available': 'planner_runtime' in candidate}
        if args.apply is not None:
            if args.apply != digest: raise Rejected('SETUP_CONFIGURATION_CHANGED_REVIEW_AGAIN')
            save(destination, candidate)
            report['installed'] = True
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    try: main()
    except (Rejected, OSError, ValueError) as error:
        code = str(error) if isinstance(error, Rejected) else type(error).__name__
        raise SystemExit('Setup configuration refused: ' + code)
