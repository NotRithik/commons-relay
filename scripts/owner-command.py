#!/usr/bin/env python3
"""Create a signed owner command without sending it or exposing the signing key."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from commons_relay.codec import canonical,parse,b64,identifier
from commons_relay.signing import Ed25519,sign_envelope,key_id
from commons_relay.engine import REQUEST_DOMAIN,APPROVAL_DOMAIN,GRANT_DOMAIN
from commons_relay.skills import default_registry

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--owner-dir',type=Path,required=True)
    p.add_argument('--expires-in',type=int,default=300)
    p.add_argument('--request-id',default=None)
    sub=p.add_subparsers(dest='command',required=True)
    task=sub.add_parser('task');task.add_argument('skill');task.add_argument('--arguments',required=True)
    approval=sub.add_parser('approve');approval.add_argument('--task-id',required=True);approval.add_argument('--intent-hash',required=True);approval.add_argument('--policy-version',type=int,default=1)
    grant=sub.add_parser('grant');grant.add_argument('--delegate-key-id',required=True);grant.add_argument('--goal',required=True);grant.add_argument('--skills',required=True);grant.add_argument('--budget',required=True);grant.add_argument('--steps',type=int,default=10)
    args=p.parse_args()
    if not 1<=args.expires_in<=600:p.error('expiry must be 1 to 600 seconds')
    owner=args.owner_dir.resolve(strict=True);signer=Ed25519(owner);private=owner/'owner-signing.pem';public=signer.public(private)
    config=parse((owner/'agent.json').read_bytes());agent=identifier(config['agent_id'])
    if config['owner_public_key']!=b64(public):p.error('Owner key does not match profile')
    identity=identifier(args.request_id or uuid.uuid4().hex);expiry=int(time.time())+args.expires_in
    if args.command=='task':
        arguments=parse(args.arguments.encode());skill=default_registry().get(args.skill);skill.validate(arguments)
        body={'domain':REQUEST_DOMAIN,'agent_id':agent,'request_id':identity,'skill':args.skill,'arguments':arguments,'expires_at':expiry}
        command={'method':'submit','params':{'envelope':sign_envelope(body,private,signer),'public_key':b64(public)}}
    elif args.command=='approve':
        if len(args.intent_hash)!=64 or any(c not in '0123456789abcdef' for c in args.intent_hash):p.error('Invalid intent digest')
        body={'domain':APPROVAL_DOMAIN,'agent_id':agent,'task_id':identifier(args.task_id),'intent_hash':args.intent_hash,'approval_id':identity,'decision':'approve','expires_at':expiry,'policy_version':args.policy_version}
        command={'method':'approve','params':{'envelope':sign_envelope(body,private,signer)}}
    else:
        skills=args.skills.split(',')
        for name in skills:default_registry().get(name)
        body={'domain':GRANT_DOMAIN,'agent_id':agent,'grant_id':identity,'delegate_key_id':args.delegate_key_id,'goal':args.goal,'allowed_skills':skills,'maximum_spend':args.budget,'max_steps':args.steps,'expires_at':expiry,'policy_version':1}
        command={'method':'grant','params':{'envelope':sign_envelope(body,private,signer)}}
    print(canonical(command).decode())
if __name__=='__main__':main()
