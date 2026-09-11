#!/usr/bin/env python3
"""Install a reviewed skill package into a stopped Relay agent.

Example:
  python3 scripts/install-skill.py --profile /path/to/agent \
    --manifest examples/services/text-statistics.json

API-backed packages accept --credential NAME=/path/to/an/owner-only/key-file.
Credential values are never command-line arguments or printed output.
"""
from pathlib import Path
import argparse
import json
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from commons_relay.codec import Rejected
from commons_relay.skill_install import install_skill


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--credential', action='append', default=[], metavar='NAME=FILE')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    credentials = {}
    for value in args.credential:
        name, separator, path = value.partition('=')
        if not separator or not name or not path or name in credentials:
            parser.error('Use each --credential NAME=/path/to/key-file once.')
        credentials[name] = Path(path)
    print(json.dumps(install_skill(args.profile, args.manifest, credentials, dry_run=args.dry_run), indent=2))


if __name__ == '__main__':
    try: main()
    except (Rejected, OSError) as error:
        code = str(error) if isinstance(error, Rejected) else 'FILE_ACCESS_FAILED'
        raise SystemExit('Skill installation stopped: ' + code)
