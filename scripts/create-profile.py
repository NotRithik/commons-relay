#!/usr/bin/env python3
"""Create separate local owner and agent profiles; never imports existing wallets."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from commons_relay.codec import canonical,b64,identifier
from commons_relay.signing import Ed25519,protected_directory
from commons_relay.engine import Policy

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--owner-dir',type=Path,required=True)
    p.add_argument('--agent-dir',type=Path,required=True)
    p.add_argument('--agent-id',required=True)
    args=p.parse_args();identifier(args.agent_id)
    if args.owner_dir.resolve()==args.agent_dir.resolve():p.error('Owner keys and agent state must be separate')
    if args.owner_dir.exists() or args.agent_dir.exists():p.error('Choose two new profile directories')
    os.umask(0o077);owner=protected_directory(args.owner_dir);agent=protected_directory(args.agent_dir)
    crypto=Ed25519(owner);public=crypto.generate(owner/'owner-signing.pem')
    config={'schema_version':1,'agent_id':args.agent_id,'owner_public_key':b64(public),'policy':asdict(Policy()),'network':'testnet'}
    (agent/'settings.json').write_bytes(canonical(config));(agent/'settings.json').chmod(0o600)
    (owner/'agent.json').write_bytes(canonical({'agent_id':args.agent_id,'agent_profile':str(agent),'owner_public_key':b64(public)}));(owner/'agent.json').chmod(0o600)
    print(json.dumps({'agent_id':args.agent_id,'created':True,'owner_key_copied_to_agent':False,'network_actions':0,'model_calls':0}))
if __name__=='__main__':main()
