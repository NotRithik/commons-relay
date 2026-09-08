#!/usr/bin/env python3
"""Exercise real installed Relay and Storage modules over official local Core IPC."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from commons_relay.codec import b64,parse
from commons_relay.control import CoreClient
from commons_relay.engine import REQUEST_DOMAIN
from commons_relay.signing import Ed25519,sign_envelope

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--logosctl',type=Path,required=True);parser.add_argument('--session',type=Path,required=True)
    parser.add_argument('--owner-dir',type=Path,required=True);parser.add_argument('--profile',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    os.umask(0o077);profile=args.profile.resolve(strict=True);owner=args.owner_dir.resolve(strict=True)
    info=parse((owner/'agent.json').read_bytes());settings=parse((profile/'settings.json').read_bytes())
    if info['agent_id']!=settings['agent_id']:parser.error('The supplied owner profile belongs to a different agent')
    signer=Ed25519(owner);private=owner/'owner-signing.pem';public=signer.public(private)
    if b64(public)!=settings['owner_public_key']:parser.error('Wrong owner key')
    client=CoreClient(args.logosctl,args.session,timeout=120)
    sample=('Commons Relay CLI synthetic file '+secrets.token_hex(24)+'\n').encode()
    basename='smoke-'+secrets.token_hex(8)+'.txt'
    inputs=profile/'inputs';inputs.mkdir(mode=0o700,exist_ok=True)
    with (inputs/basename).open('xb') as f:f.write(sample)
    events=[]
    def task(skill,arguments):
        body={'domain':REQUEST_DOMAIN,'agent_id':settings['agent_id'],'request_id':'smoke-'+secrets.token_hex(12),
              'skill':skill,'arguments':arguments,'expires_at':int(time.time())+300}
        accepted=client.request('submit',{'envelope':sign_envelope(body,private,signer),'public_key':b64(public)})
        if accepted['state']!='submitted':raise RuntimeError('Unexpected authorization state')
        result=client.request('run',{'task_id':accepted['id']})
        if result['state']!='completed':raise RuntimeError('Task did not complete: '+result['state'])
        events.append({'task_id':result['id'],'skill':skill,'state':result['state'],'result':result['result']})
        return result['result']
    uploaded=task('storage.upload',{'path':basename,'label':'CLI synthetic round-trip'})
    downloaded=task('storage.download',{'address':uploaded['address'],'path':basename})
    returned=(profile/'outputs'/basename).read_bytes()
    if returned!=sample:raise RuntimeError('Downloaded plaintext differs')
    listing=task('storage.list',{})
    if uploaded['address'] not in [x['address'] for x in listing['files']]:raise RuntimeError('Stored reference absent from catalogue')
    report={'passed':True,'test':'actual headless Logos Core and Storage module round-trip',
            'input_kind':'synthetic fixture only','bytes':len(sample),'sha256':hashlib.sha256(sample).hexdigest(),
            'transport':'Logos local IPC','storage_network':'real local Storage node; no external replication claimed',
            'events':events,'inference_calls':0,'timestamp':int(time.time())}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
if __name__=='__main__':main()
