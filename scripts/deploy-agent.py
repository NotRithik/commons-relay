#!/usr/bin/env python3
"""Create and start one Commons Relay testnet agent with a single command.

The command is deliberately explicit about trusted binaries and directories. It
creates fresh owner and agent state, a fresh LEZ wallet, wallet-derived messaging
identity, owner channel, role/service configuration and a headless Logos Core
session. It does not request faucet funds or start model inference.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from commons_relay.codec import Rejected,canonical,b64,parse
from commons_relay.engine import Policy
from commons_relay.file_crypto import Sodium
from commons_relay.identity import create_from_wallet_export
from commons_relay.messaging import Contact
from commons_relay.signing import Ed25519,protected_directory
from commons_relay.vault import Vault

ROLES={
 'storage':{
   'name':'Storage Agent','description':'Commons Relay storage testnet service',
   'exports':{'storage.list':{'price':'0','description':'List the service public catalogue'},'meta.skills':{'price':'0','description':'Describe installed typed capabilities'}},
 },
 'messaging':{
   'name':'Messaging Agent','description':'Commons Relay messaging testnet service',
   'exports':{'meta.skills':{'price':'0','description':'Describe messaging capabilities'}},
 },
 'blockchain':{
   'name':'Blockchain Agent','description':'Commons Relay blockchain testnet service',
   'exports':{'program.query':{'price':'3','description':'Query a public LEZ account and verify its owning program'},'meta.skills':{'price':'0','description':'Describe blockchain capabilities'}},
 },
}

def owned_file(path:Path, *, executable=False)->Path:
    candidate=path.expanduser().absolute()
    if candidate.is_symlink() or not candidate.is_file():raise Rejected('TRUSTED_FILE_REQUIRED')
    resolved=candidate.resolve(strict=True)
    if executable and not os.access(resolved,os.X_OK):raise Rejected('EXECUTABLE_REQUIRED')
    return resolved

def owned_directory(path:Path)->Path:
    candidate=path.expanduser().absolute()
    if candidate.is_symlink() or not candidate.is_dir():raise Rejected('TRUSTED_DIRECTORY_REQUIRED')
    return candidate.resolve(strict=True)

def run(command:list[str], *, env:dict|None=None, timeout=120)->str:
    completed=subprocess.run(command,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=timeout)
    if completed.returncode:
        # Never reflect arbitrary subprocess stderr; it can contain local paths or
        # upstream diagnostics unrelated to the stable deployment API.
        raise Rejected('DEPLOY_SUBPROCESS_FAILED')
    return completed.stdout

def typed_wallet(binary:Path,mode:str,profile:Path,*args:str,env=None,timeout=180)->dict:
    output=run([str(binary),mode,str(profile),*map(str,args)],env=env,timeout=timeout)
    for line in reversed(output.splitlines()):
        if line.startswith('{"relay_wallet_result":'):
            value=json.loads(line)['relay_wallet_result']
            if not isinstance(value,dict):raise Rejected('WALLET_RESULT_INVALID')
            return value
    raise Rejected('WALLET_RESULT_MISSING')

def storage_snapshot(path:Path)->dict:
    value=parse(path.read_bytes())
    if not isinstance(value,dict) or value.get('schema')!=1 or value.get('name')!='logos.test' or not isinstance(value.get('records'),list) or not 1<=len(value['records'])<=16:raise Rejected('STORAGE_PRESET_INVALID')
    if any(not isinstance(x,str) or not re.fullmatch(r'spr:[A-Za-z0-9_-]{40,2000}',x) for x in value['records']):raise Rejected('STORAGE_PRESET_INVALID')
    return value

def make_owner_channel(owner:Path,agent_hint:str,sodium:Sodium)->Contact:
    incoming=protected_directory(owner/'messaging-inputs');outgoing=protected_directory(owner/'messaging-outputs')
    vault=Vault(owner/'messaging-vault',incoming,outgoing,sodium)
    try:
        suffix=__import__('hashlib').sha256(agent_hint.encode()).hexdigest()[:16]
        return Contact('owner-'+suffix,vault.signing_public,vault.box_public,'Commons Relay owner')
    finally:vault.close()

def create_agent_profile(agent:Path,owner:Path,wallet:Path,identity:dict,owner_contact:Contact,role:str,storage:dict,sodium:Sodium,delivery_port:int):
    protected_directory(agent);protected_directory(owner)
    auth=Ed25519(owner);owner_key=owner/'owner-signing.pem';public=auth.generate(owner_key)
    policy=Policy(per_transaction=5,per_period=30,hard_maximum=50,approval_ttl=7200)
    settings={'schema_version':1,'agent_id':identity['address'],'owner_public_key':b64(public),'policy':asdict(policy),'network':'testnet'}
    (agent/'settings.json').write_bytes(canonical(settings));(agent/'settings.json').chmod(0o600)
    for name in ['inputs','outputs']:protected_directory(agent/name)
    imported=create_from_wallet_export(agent/'vault',wallet/'messaging-identity.seeds',sodium)
    if imported['address']!=identity['address'] or imported['root_npk']!=identity['root_npk']:raise Rejected('WALLET_IDENTITY_IMPORT_MISMATCH')
    (agent/'contacts.json').write_text(json.dumps([owner_contact.public()],indent=2)+'\n');(agent/'contacts.json').chmod(0o600)
    (agent/'owner-channel.json').write_text(json.dumps({'address':owner_contact.address})+'\n');(agent/'owner-channel.json').chmod(0o600)
    (agent/'wallet.json').write_text(json.dumps({'profile':str(wallet.resolve())})+'\n');(agent/'wallet.json').chmod(0o600)
    (agent/'services.json').write_text(json.dumps({'name':ROLES[role]['name'],'description':ROLES[role]['description'],'exports':ROLES[role]['exports'],'discovery_topic':'commons'},indent=2)+'\n');(agent/'services.json').chmod(0o600)
    (agent/'storage.json').write_text(json.dumps({'mode':'official-testnet','listenPort':0,'bootstrapNodes':storage['records']},indent=2)+'\n');(agent/'storage.json').chmod(0o600)
    (agent/'delivery.json').write_text(json.dumps({'mode':'logos.dev','tcpPort':delivery_port},indent=2)+'\n');(agent/'delivery.json').chmod(0o600)
    (owner/'agent.json').write_bytes(canonical({'agent_id':identity['address'],'agent_profile':str(agent.resolve()),'owner_public_key':b64(public),'owner_messaging_address':owner_contact.address}));(owner/'agent.json').chmod(0o600)
    (owner/'agent-contact.json').write_text(json.dumps({'address':identity['address'],'signing_key':imported['signing_key'],'box_key':imported['box_key'],'label':ROLES[role]['name']},indent=2)+'\n');(owner/'agent-contact.json').chmod(0o600)
    return policy

def daemon_env(args,agent_root,wallet_root)->dict:
    env=os.environ.copy();env.update({
      'COMMONS_RELAY_ALLOWED_STATE_ROOT':str(agent_root.resolve()),'COMMONS_RELAY_WALLET_ROOT':str(wallet_root.resolve()),
      'COMMONS_RELAY_WALLET_EXECUTABLE':str(args.wallet_binary),'COMMONS_RELAY_PYTHON':str(Path(sys.executable).resolve()),
      'COMMONS_RELAY_SODIUM_LIBRARY':str(args.sodium_library),'COMMONS_ALLOW_PUBLIC_TESTNET':'1','RISC0_DEV_MODE':'0','RISC0_PROVER':'ipc','RISC0_EXECUTOR':'ipc',
    })
    for key in ['RISC0_SERVER_PATH','LBC_ROOT_DIR']:
        if os.environ.get(key):env[key]=os.environ[key]
    return env

def start_daemon(args,session:Path,env:dict):
    protected_directory(session)
    config=session/'requested-config.json';config.write_text(json.dumps({'insecure_tcp':False,'modules_dirs':[str(args.modules_dir)],'signature_policy':'warn','logging':{'max_file_size_mb':4,'max_files':3}},indent=2)+'\n');config.chmod(0o600)
    run([str(args.logosctl),'--config-dir',str(session),'daemon','config','set',str(config),'-y'],env=env,timeout=20)
    log=(session/'relay-daemon.log').open('a')
    child=subprocess.Popen([str(args.logosctl),'--config-dir',str(session),'daemon','start'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,text=True,start_new_session=True)
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        try:
            run([str(args.logosctl),'--config-dir',str(session),'module','list'],env=env,timeout=4);break
        except Exception:time.sleep(.25)
    else:raise Rejected('LOGOS_DAEMON_START_TIMEOUT')
    return child.pid

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--role',choices=sorted(ROLES),required=True)
    p.add_argument('--agent-root',type=Path,required=True,help='New parent directory that will contain the agent profile')
    p.add_argument('--owner-root',type=Path,required=True,help='New parent directory that will contain owner keys')
    p.add_argument('--wallet-root',type=Path,required=True,help='New parent directory that will contain the LEZ wallet')
    p.add_argument('--session',type=Path,required=True,help='New Logos Core session directory')
    p.add_argument('--logosctl',type=Path,required=True);p.add_argument('--modules-dir',type=Path,required=True)
    p.add_argument('--wallet-binary',type=Path,required=True);p.add_argument('--sodium-library',type=Path,required=True)
    p.add_argument('--storage-preset',type=Path,default=ROOT/'config/logos-storage-testnet.json')
    p.add_argument('--endpoint',default='https://testnet.lez.logos.co/')
    p.add_argument('--delivery-port',type=int,default=0,help='0 chooses a deterministic role port; otherwise 1024..65535')
    p.add_argument('--dry-run',action='store_true',help='Validate trusted inputs and print a redacted plan without creating state or contacting the network')
    args=p.parse_args()
    if args.endpoint!='https://testnet.lez.logos.co/':p.error('only the pinned LEZ testnet endpoint is supported')
    args.logosctl=owned_file(args.logosctl,executable=True);args.wallet_binary=owned_file(args.wallet_binary,executable=True);args.sodium_library=owned_file(args.sodium_library);args.modules_dir=owned_directory(args.modules_dir);args.storage_preset=owned_file(args.storage_preset)
    for required in ['commons_relay_module','commons_relay_wallet']:
        if not (args.modules_dir/required).is_dir():p.error('modules-dir is missing '+required)
    storage=storage_snapshot(args.storage_preset)
    port=args.delivery_port or {'storage':34363,'messaging':34364,'blockchain':34365}[args.role]
    if not 1024<=port<=65535:p.error('delivery port must be 1024..65535')
    targets=[args.agent_root,args.owner_root,args.wallet_root,args.session]
    if len({str(x.expanduser().absolute()) for x in targets})!=4:p.error('agent, owner, wallet and session roots must be distinct')
    if any(x.exists() for x in targets):p.error('choose four new deployment directories; existing state is never overwritten')
    plan={'role':args.role,'network':'LEZ testnet','storage_network':storage['name'],'agent_root':str(args.agent_root.expanduser().absolute()),'owner_root':str(args.owner_root.expanduser().absolute()),'wallet_root':str(args.wallet_root.expanduser().absolute()),'session':str(args.session.expanduser().absolute()),'faucet_requests':0,'model_calls':0,'risc0_dev_mode':'0'}
    if args.dry_run:print(json.dumps({'dry_run':True,**plan},indent=2));return
    os.umask(0o077);agent=args.agent_root.expanduser().absolute();owner=args.owner_root.expanduser().absolute();wallet=args.wallet_root.expanduser().absolute();session=args.session.expanduser().absolute()
    protected_directory(wallet)
    env=os.environ.copy();env.update({'COMMONS_ALLOW_PUBLIC_TESTNET':'1','RISC0_DEV_MODE':'0'})
    created=typed_wallet(args.wallet_binary,'init',wallet,args.endpoint,env=env,timeout=90)
    if created.get('created') is not True or created.get('network_transactions')!=0:raise Rejected('FRESH_WALLET_CREATION_FAILED')
    exported=typed_wallet(args.wallet_binary,'export-messaging-identity',wallet,env=env,timeout=90)
    if exported.get('exported') is not True or exported.get('seed_material_printed') is not False:raise Rejected('MESSAGING_IDENTITY_EXPORT_FAILED')
    sodium=Sodium(args.sodium_library);owner_contact=make_owner_channel(owner,exported['address'],sodium)
    policy=create_agent_profile(agent,owner,wallet,exported,owner_contact,args.role,storage,sodium,port)
    # Pin the Core local endpoint namespace to this deployment. Subsequent
    # logosctl calls can use the TMPDIR recorded in deployment.json instead of
    # depending on a caller's shell or sandbox TMPDIR.
    protected_directory(session);core_tmp=protected_directory(session/'tmp')
    env=daemon_env(args,agent.parent,wallet.parent);env['TMPDIR']=str(core_tmp)+'/'
    pid=start_daemon(args,session,env)
    run([str(args.logosctl),'--config-dir',str(session),'module','load','commons_relay_module'],env=env,timeout=30)
    run([str(args.logosctl),'--config-dir',str(session),'call','commons_relay_module','configure',str(agent.resolve())],env=env,timeout=30)
    control=[sys.executable,str(ROOT/'scripts/control.py'),'--logosctl',str(args.logosctl),'--session',str(session),'--method','agent.start']
    started=json.loads(run(control,env=env,timeout=120))
    if started.get('success') is not True or started.get('result',{}).get('started') is not True:raise Rejected('AGENT_START_FAILED')
    receipt={'schema':1,**plan,'agent_address':exported['address'],'root_npk':exported['root_npk'],'owner_messaging_address':owner_contact.address,'policy':asdict(policy),'daemon_pid':pid,'core_tmpdir':str(core_tmp)+'/', 'wallet_funded':False,'next_step':'Fund only with disposable testnet units before exercising paid skills.'}
    (owner/'deployment.json').write_text(json.dumps(receipt,indent=2)+'\n');(owner/'deployment.json').chmod(0o600)
    print(json.dumps(receipt,indent=2))

if __name__=='__main__':
    try:main()
    except Rejected as error:raise SystemExit('Deployment refused: '+str(error))
