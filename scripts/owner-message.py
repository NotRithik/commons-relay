#!/usr/bin/env python3
"""Wrap an already signed owner command for its encrypted Logos owner channel."""
import argparse,json,time,secrets,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from commons_relay.codec import parse,canonical,b64
from commons_relay.signing import Ed25519,sign_envelope
from commons_relay.controller import OWNER_DOMAIN,OWNER_METHODS

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--owner-dir',type=Path,required=True);p.add_argument('--command-file',type=Path,required=True);p.add_argument('--expires-in',type=int,default=7200);args=p.parse_args()
 if not 1<=args.expires_in<=86400:p.error('Invalid expiry')
 owner=args.owner_dir.resolve(strict=True);signer=Ed25519(owner);info=parse((owner/'agent.json').read_bytes());key=owner/'owner-signing.pem'
 if b64(signer.public(key))!=info['owner_public_key']:p.error('Owner key binding changed')
 if args.command_file.is_symlink() or args.command_file.stat().st_size>20000:p.error('Expected a small regular command file')
 command=parse(args.command_file.read_bytes())
 if not isinstance(command,dict) or set(command)!={'method','params'} or command['method'] not in OWNER_METHODS:p.error('Unsupported owner command')
 body={'domain':OWNER_DOMAIN,'agent_id':info['agent_id'],'request_id':'owner-'+secrets.token_hex(12),'command':command,'expires_at':int(time.time())+args.expires_in}
 print(canonical({'method':'owner.send','params':{'recipient':info['agent_id'],'envelope':sign_envelope(body,key,signer)}}).decode())
if __name__=='__main__':main()
