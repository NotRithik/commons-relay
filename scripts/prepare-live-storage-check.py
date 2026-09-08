#!/usr/bin/env python3
"""Authorize a zero-spend synthetic storage goal, never an unrestricted coding agent."""
from pathlib import Path
import argparse,json,sys,time,secrets,hashlib,os
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from commons_relay.signing import Ed25519,sign_envelope,key_id
from commons_relay.codec import parse
from commons_relay.engine import GRANT_DOMAIN
from commons_relay.control import CoreClient

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--base',type=Path,required=True);args=p.parse_args();base=args.base.resolve(strict=True)
 root=base.parent;instructions=(root/'INSTRUCTIONS.md').read_text()
 if 'MAX_TEST_COST_USD=15' not in instructions:raise SystemExit('The explicit test-budget authorization is missing')
 os.umask(0o077)
 folder=base/'runtime/relay-model-tests';folder.mkdir(mode=0o700,exist_ok=True)
 run=folder/('storage-'+str(int(time.time())));run.mkdir(mode=0o700)
 owner=base/'runtime/relay-owner/headless';profile=base/'runtime/relay/agent-headless'
 settings=parse((profile/'settings.json').read_bytes());signer=Ed25519(owner)
 delegate=Ed25519(run);public=delegate.generate(run/'delegate.pem')
 client=CoreClient(base/'cache/logosctl-023/logosctl-aarch64-macos/bin/logosctl',base/'runtime/relay-headless-check')
 identity='luna-storage-'+secrets.token_hex(8);allowed=['storage.upload','storage.download','storage.list']
 filename='model-fixture-'+secrets.token_hex(8)+'.txt';outname='retrieved-'+filename
 sample=('Synthetic Commons Relay product test '+secrets.token_hex(32)+'\n').encode()
 (profile/'inputs').mkdir(mode=0o700,exist_ok=True);(profile/'inputs'/filename).write_bytes(sample)
 goal=f'Please store {filename} in my encrypted storage with the label Luna acceptance fixture. Then retrieve the stored file as {outname} and check that the file appears in my storage catalogue. Use the real results from the tools rather than inventing a content address.'
 body={'domain':GRANT_DOMAIN,'agent_id':settings['agent_id'],'grant_id':identity,'delegate_key_id':key_id(public),
       'goal':goal,'allowed_skills':allowed,'maximum_spend':'0','max_steps':8,'expires_at':int(time.time())+1800,'policy_version':settings['policy']['version']}
 accepted=client.request('grant',{'envelope':sign_envelope(body,owner/'owner-signing.pem',signer)})
 config={'modelId':'gpt-5.6-luna','maximumUsd':1,'syntheticOnly':True,'envFile':str(root/'.env'),
         'delegateKey':str(run/'delegate.pem'),'delegateKeyId':key_id(public),'agentId':settings['agent_id'],'grantId':identity,
         'goal':goal,'allowedSkills':allowed,'budgetPath':str(folder/'budget.json'),'reportPath':str(run/'report.json'),
         'core':{'python':'/opt/homebrew/bin/python3','logosctl':str(base/'cache/logosctl-023/logosctl-aarch64-macos/bin/logosctl'),'session':str(base/'runtime/relay-headless-check')},
         'expectedOutput':str(profile/'outputs'/outname),'inputSha256':hashlib.sha256(sample).hexdigest()}
 (run/'config.json').write_text(json.dumps(config,indent=2)+'\n');(folder/'latest-config-path.txt').write_text(str(run/'config.json')+'\n')
 print(json.dumps({'prepared':True,'config':str(run/'config.json'),'authorized_skills':allowed,'maximum_onchain_spend':0,'model_test_budget_usd':1,'key_printed':False}))
if __name__=='__main__':main()
