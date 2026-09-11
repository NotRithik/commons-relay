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

def validate_native_modules(directory:Path):
    """Reject incomplete native installs before creating a wallet or profile."""
    import platform
    system={'Darwin':'darwin','Linux':'linux'}.get(platform.system())
    machine={'arm64':'arm64','aarch64':'arm64','x86_64':'amd64','AMD64':'amd64'}.get(platform.machine())
    if not system or not machine:raise Rejected('UNSUPPORTED_NATIVE_PLATFORM')
    variant=system+'-'+machine
    for name in ['commons_relay_module','commons_relay_wallet','storage_module','delivery_module']:
        module=directory/name;manifest=module/'manifest.json'
        if module.is_symlink() or not module.is_dir() or manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size>100000:
            raise Rejected('MODULE_MANIFEST_REQUIRED_RUN_NATIVE_INSTALL')
        value=parse(manifest.read_bytes());main=value.get('main') if isinstance(value,dict) else None
        entry=main.get(variant) if isinstance(main,dict) else None
        if value.get('name')!=name or not isinstance(entry,str) or not entry or Path(entry).is_absolute() or '..' in Path(entry).parts:
            raise Rejected('MODULE_NATIVE_VARIANT_MISSING')
        binary=module/entry
        if binary.is_symlink() or not binary.is_file() or not binary.resolve().is_relative_to(module.resolve()):
            raise Rejected('MODULE_NATIVE_BINARY_MISSING')
    return variant


def configure_runtime(args,session:Path,agent:Path,env:dict):
    """One configure, followed only by read-only readiness polling."""
    loaded=json.loads(run([str(args.logosctl),'--config-dir',str(session),'module','load','commons_relay_module'],env=env,timeout=30))
    if loaded.get('status')!='ok':raise Rejected('AGENT_MODULE_LOAD_FAILED_USE_RESTART')
    configure_existing_worker(args.logosctl,session,agent.resolve(),env)
    from commons_relay.control import CoreClient
    client=CoreClient(args.logosctl,session,timeout=3,environment=env)
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        try:
            result=client.request('status',{})
            if not isinstance(result,dict):raise Rejected('AGENT_STARTUP_STATUS_INVALID')
            native=json.loads(run([str(args.logosctl),'--config-dir',str(session),'call','commons_relay_module','moduleProbe'],env=env,timeout=5))
            attached=json.loads(native.get('result','{}'))
            if all(attached.get(name,{}).get('ipc_connected') is True for name in ('delivery_module','storage_module')):return
            time.sleep(.25)
        except Rejected as error:
            if str(error) not in {'RUNTIME_NOT_CONFIGURED','CORE_REQUEST_NOT_ACCEPTED','CORE_CALL_REJECTED','CORE_OPERATION_TIMEOUT','RUNTIME_STOPPED'}:raise
            time.sleep(.25)
    raise Rejected('AGENT_STARTUP_TIMEOUT_USE_RESTART')


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
    # This process has not configured an agent or accepted a task yet. Track it
    # before waiting so a failed first startup cannot become an invisible orphan.
    startup=session/'startup-process.json'
    save_deployment_receipt(startup,{'pid':child.pid,'state':'starting','created_at':int(time.time()),'tmpdir':env.get('TMPDIR')})
    deadline=time.monotonic()+90
    try:
        while time.monotonic()<deadline:
            if child.poll() is not None:raise Rejected('LOGOS_DAEMON_EXITED_BEFORE_READY')
            try:
                run([str(args.logosctl),'--config-dir',str(session),'module','list'],env=env,timeout=4)
                save_deployment_receipt(startup,{'pid':child.pid,'state':'ready','tmpdir':env.get('TMPDIR')})
                break
            except (Rejected,subprocess.TimeoutExpired):time.sleep(.25)
        else:raise Rejected('LOGOS_DAEMON_START_TIMEOUT')
    except BaseException:
        if child.poll() is None:
            child.terminate()
            try:child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                save_deployment_receipt(startup,{'pid':child.pid,'state':'stop-unconfirmed','tmpdir':env.get('TMPDIR')})
                raise Rejected('LOGOS_DAEMON_STARTUP_NEEDS_OPERATOR_REVIEW') from None
        save_deployment_receipt(startup,{'pid':child.pid,'state':'stopped-before-configuration','exit_code':child.returncode,'tmpdir':env.get('TMPDIR')})
        raise
    finally:
        log.close()
    return child.pid

def configure_existing_worker(logosctl:Path,session:Path,agent:Path,env:dict):
    """Wait for the loaded Qt remote object, then configure the same profile.

    Only this idempotent setup operation is retried. Task submission, payments,
    and proofs are never retried by the deployment helper.
    """
    deadline=time.monotonic()+60
    base=[str(logosctl),'--config-dir',str(session),'--json','call','commons_relay_module']
    while time.monotonic()<deadline:
        try:
            probe=subprocess.run([*base,'runtimeState'],env=env,cwd=ROOT,capture_output=True,text=True,timeout=8)
            value=json.loads(probe.stdout)
            if probe.returncode or value.get('status')!='ok':
                time.sleep(.25);continue
            state=json.loads(value['result'])
            if not isinstance(state,dict):raise ValueError('invalid runtime state')
            configured=subprocess.run([*base,'configure',str(agent)],env=env,cwd=ROOT,capture_output=True,text=True,timeout=8)
            response=json.loads(configured.stdout)
            if configured.returncode or response.get('status')!='ok':
                time.sleep(.25);continue
            outcome=response.get('result')
            if outcome in ('STARTING_LOCAL_RUNTIME','RUNTIME_ALREADY_CONFIGURED'):
                return outcome
            if isinstance(outcome,str) and re.fullmatch('[A-Z_0-9]{1,100}',outcome):raise Rejected(outcome)
            raise Rejected('AGENT_CONFIGURATION_NOT_ACCEPTED')
        except (subprocess.TimeoutExpired,ValueError,KeyError,TypeError):
            time.sleep(.25)
    raise Rejected('AGENT_CONFIGURATION_TIMEOUT_USE_RESTART_RECEIPT')

def save_deployment_receipt(path:Path,value:dict):
    """Persist restart inputs atomically; receipts contain no private key material."""
    import tempfile
    if path.is_symlink() or (path.exists() and not path.is_file()):raise Rejected('DEPLOYMENT_RECEIPT_INVALID')
    fd,name=tempfile.mkstemp(prefix='.deployment-',suffix='.tmp',dir=path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(fd,'wb') as output:
            output.write(canonical(value));output.flush();os.fsync(output.fileno())
        os.replace(temporary,path)
        directory=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if temporary.exists():temporary.unlink()

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
    p.add_argument('--service-manifest',type=Path,help='Install a reviewed third-party service during deployment')
    p.add_argument('--service-price',default='0',help='LEZ testnet units charged per remote task')
    p.add_argument('--provider-name',default='My service',help='Public name for the custom service provider')
    p.add_argument('--agent-name',help='Friendly name for this agent and its saved owner connection')
    p.add_argument('--owner-contact',type=Path,help='Reviewed public contact of an existing owner Messaging instance')
    p.add_argument('--discovery-topic',default='commons')
    p.add_argument('--public-service',action='store_true',help='Allow unknown agents to discover and request the custom service')
    p.add_argument('--credential',action='append',default=[],metavar='NAME=FILE',help='Explicit private credential file for the service, never the key value')
    p.add_argument('--local-bootstrap',action='append',default=[],help='Optional localhost mesh peer for a reproducible multi-agent development demo')
    p.add_argument('--local-cluster-id',type=int,default=42)
    p.add_argument('--lan-address',help='Explicit private IPv4 address to advertise for a two-machine LAN mesh')
    p.add_argument('--lan-peer',action='append',default=[],help='Private IPv4 Messaging peer multiaddress for the explicit LAN mesh')
    p.add_argument('--dry-run',action='store_true',help='Validate trusted inputs and print a redacted plan without creating state or contacting the network')
    args=p.parse_args()
    owner_contact_input=None
    if args.agent_name is not None and (not 1<=len(args.agent_name.strip())<=80 or any(ord(c)<32 for c in args.agent_name)):
        p.error('agent name must be 1 to 80 ordinary characters')
    if args.owner_contact:
        contact_path=owned_file(args.owner_contact)
        if contact_path.stat().st_size>4096:p.error('owner contact is too large')
        owner_contact_input=Contact.from_public(parse(contact_path.read_bytes()))
    if args.endpoint!='https://testnet.lez.logos.co/':p.error('only the pinned LEZ testnet endpoint is supported')
    args.logosctl=owned_file(args.logosctl,executable=True);args.wallet_binary=owned_file(args.wallet_binary,executable=True);args.sodium_library=owned_file(args.sodium_library);args.modules_dir=owned_directory(args.modules_dir);args.storage_preset=owned_file(args.storage_preset)
    validate_native_modules(args.modules_dir)
    storage=storage_snapshot(args.storage_preset)
    from commons_relay.skill_install import inspect_package,install_skill
    from commons_relay.codec import amount
    credentials={}
    for item in args.credential:
        name,separator,path=item.partition('=')
        if not separator or not name or not path or name in credentials:p.error('Use each --credential NAME=FILE once')
        credentials[name]=Path(path)
    if args.public_service and not args.service_manifest:p.error('--public-service requires --service-manifest')
    if not 1<=len(args.provider_name)<=100 or not 1<=len(args.discovery_topic)<=128:p.error('Provider name or discovery topic is too long or empty')
    if amount(args.service_price)>50:p.error('Service price exceeds the initial 50-unit hard limit')
    service_spec=None
    if args.service_manifest:
        _,service_spec,_=inspect_package(args.service_manifest)
        if args.public_service and not service_spec.public:p.error('The package is not marked for public service use')
        if set(service_spec.credentials)!=set(credentials):p.error('Provide exactly the credentials declared by this new service package')
        install_skill(args.agent_root,args.service_manifest,credentials,dry_run=True)
    elif credentials:p.error('--credential requires --service-manifest')
    if len(args.local_bootstrap)>8 or not 2<=args.local_cluster_id<=65535 or any(not re.fullmatch(r'/ip4/127[.]0[.]0[.]1/tcp/[0-9]{4,5}/p2p/[A-Za-z0-9]{20,100}',node) for node in args.local_bootstrap):p.error('Invalid local development mesh bootstrap')

    port=args.delivery_port or {'storage':34363,'messaging':34364,'blockchain':34365}[args.role]
    if not 1024<=port<=65535:p.error('delivery port must be 1024..65535')
    import ipaddress
    private_ranges=[ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')]
    def private_ip(raw):
        try:value=ipaddress.ip_address(raw)
        except ValueError:return False
        return value.version==4 and any(value in network for network in private_ranges)
    if args.lan_peer and not args.lan_address:p.error('--lan-peer requires --lan-address')
    if args.lan_address:
        if args.local_bootstrap or not private_ip(args.lan_address) or len(args.lan_peer)>8:
            p.error('Use a private LAN address and at most 8 LAN peers, without --local-bootstrap')
        for peer in args.lan_peer:
            match=re.fullmatch(r'/ip4/([0-9.]+)/tcp/([0-9]{1,5})/p2p/[A-Za-z0-9]{20,100}',peer)
            if not match or not private_ip(match[1]) or not 1024<=int(match[2])<=65535:
                p.error('LAN peers must be private IPv4 Messaging multiaddresses with ports 1024..65535')
    targets=[args.agent_root,args.owner_root,args.wallet_root,args.session]
    resolved=[x.expanduser().resolve(strict=False) for x in targets]
    if any(a==b or a in b.parents or b in a.parents for i,a in enumerate(resolved) for b in resolved[i+1:]):
        p.error('agent, owner, wallet and session roots must be separate non-overlapping directories')
    if any(x.exists() for x in targets):p.error('choose four new deployment directories; existing state is never overwritten')
    plan={'role':args.role,'network':'LEZ testnet','storage_network':storage['name'],'agent_root':str(args.agent_root.expanduser().absolute()),'owner_root':str(args.owner_root.expanduser().absolute()),'wallet_root':str(args.wallet_root.expanduser().absolute()),'session':str(args.session.expanduser().absolute()),'faucet_requests':0,'model_calls':0,'risc0_dev_mode':'0'}
    if service_spec:plan['service']={'id':service_spec.id,'price':args.service_price,'public':args.public_service,'topic':args.discovery_topic}
    plan['messaging_network']='explicit private LAN mesh' if args.lan_address else 'local development mesh' if args.local_bootstrap else 'logos.dev'
    if args.dry_run:print(json.dumps({'dry_run':True,**plan},indent=2));return
    os.umask(0o077);agent=args.agent_root.expanduser().absolute();owner=args.owner_root.expanduser().absolute();wallet=args.wallet_root.expanduser().absolute();session=args.session.expanduser().absolute()
    protected_directory(wallet)
    env=os.environ.copy();env.update({'COMMONS_ALLOW_PUBLIC_TESTNET':'1','RISC0_DEV_MODE':'0'})
    created=typed_wallet(args.wallet_binary,'init',wallet,args.endpoint,env=env,timeout=90)
    if created.get('created') is not True or created.get('network_transactions')!=0:raise Rejected('FRESH_WALLET_CREATION_FAILED')
    exported=typed_wallet(args.wallet_binary,'export-messaging-identity',wallet,env=env,timeout=90)
    if exported.get('exported') is not True or exported.get('seed_material_printed') is not False:raise Rejected('MESSAGING_IDENTITY_EXPORT_FAILED')
    sodium=Sodium(args.sodium_library);owner_contact=owner_contact_input or make_owner_channel(owner,exported['address'],sodium)
    policy=create_agent_profile(agent,owner,wallet,exported,owner_contact,args.role,storage,sodium,port)
    if service_spec:
        install_skill(agent,args.service_manifest,credentials)
        service_config={'name':args.provider_name,'description':service_spec.description,
            'exports':{service_spec.id:{'price':args.service_price,'description':service_spec.description}},
            'discovery_topic':args.discovery_topic,'public':args.public_service}
        (agent/'services.json').write_bytes(canonical(service_config));(agent/'services.json').chmod(0o600)
    if args.local_bootstrap:
        (agent/'delivery.json').write_bytes(canonical({'mode':'local','tcpPort':port,'clusterId':args.local_cluster_id,'entryNodes':args.local_bootstrap}));(agent/'delivery.json').chmod(0o600)

    if args.lan_address:
        (agent/'delivery.json').write_bytes(canonical({'mode':'lan','tcpPort':port,'clusterId':args.local_cluster_id,
            'entryNodes':args.lan_peer,'advertiseAddress':args.lan_address}));(agent/'delivery.json').chmod(0o600)

    # Pin the Core local endpoint namespace to this deployment. Subsequent
    # logosctl calls can use the TMPDIR recorded in deployment.json instead of
    # depending on a caller's shell or sandbox TMPDIR.
    from commons_relay.core_paths import socket_directory
    protected_directory(session);core_tmp=socket_directory(session)
    env=daemon_env(args,agent.parent,wallet.parent);env['TMPDIR']=str(core_tmp)+'/'
    runtime={'logosctl':str(args.logosctl),'modules_dir':str(args.modules_dir),'wallet_binary':str(args.wallet_binary),
             'sodium_library':str(args.sodium_library),'python':str(Path(sys.executable).resolve())}
    for key in ['RISC0_SERVER_PATH','LBC_ROOT_DIR']:
        if env.get(key):runtime[key.lower()]=env[key]
    receipt={'schema':1,**plan,'agent_address':exported['address'],'root_npk':exported['root_npk'],
             'owner_messaging_address':owner_contact.address,'policy':asdict(policy),'daemon_pid':None,
             'core_tmpdir':str(core_tmp)+'/','runtime':runtime,'wallet_funded':False,
             'deployment_status':'profile-created','next_step':'Fund only with disposable testnet units before exercising paid skills.'}
    # A failed Core/module start can be resumed without creating another wallet.
    save_deployment_receipt(owner/'deployment.json',receipt)
    pid=start_daemon(args,session,env)
    receipt.update(daemon_pid=pid,deployment_status='core-started')
    save_deployment_receipt(owner/'deployment.json',receipt)
    configure_runtime(args,session,agent,env)
    control=[sys.executable,str(ROOT/'scripts/control.py'),'--logosctl',str(args.logosctl),'--session',str(session),'--method','agent.start']
    started=json.loads(run(control,env=env,timeout=120))
    if started.get('success') is not True or started.get('result',{}).get('started') is not True:raise Rejected('AGENT_START_FAILED')
    receipt['deployment_status']='running';save_deployment_receipt(owner/'deployment.json',receipt)
    print(json.dumps(receipt,indent=2))

if __name__=='__main__':
    try:main()
    except Rejected as error:raise SystemExit('Deployment refused: '+str(error))
