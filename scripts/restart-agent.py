#!/usr/bin/env python3
"""Restart an existing Commons Relay deployment from its deployment receipt.

The command reuses the exact profile, Core session and trusted binaries recorded
by deploy-agent.py. It does not create a wallet, request faucet funds or start
model inference.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from commons_relay.codec import Rejected,parse
from commons_relay.core_paths import socket_directory

spec=importlib.util.spec_from_file_location('commons_relay_deploy_agent',ROOT/'scripts'/'deploy-agent.py')
if spec is None or spec.loader is None:raise SystemExit('Restart refused: DEPLOY_HELPER_MISSING')
deploy=importlib.util.module_from_spec(spec);spec.loader.exec_module(deploy)

RUNTIME_FIELDS={'logosctl','modules_dir','wallet_binary','sodium_library','python','risc0_server_path','lbc_root_dir'}

def read_receipt(path:Path)->dict:
    candidate=path.expanduser().absolute()
    if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size>20000:
        raise Rejected('DEPLOYMENT_RECEIPT_INVALID')
    value=parse(candidate.read_bytes())
    required={'schema','role','network','agent_root','owner_root','wallet_root','session','core_tmpdir','runtime'}
    if not isinstance(value,dict) or value.get('schema')!=1 or value.get('network')!='LEZ testnet' or not required<=set(value):
        raise Rejected('DEPLOYMENT_RECEIPT_INVALID')
    if value.get('role') not in {'storage','messaging','blockchain'}:
        raise Rejected('DEPLOYMENT_RECEIPT_INVALID')
    runtime=value['runtime']
    if not isinstance(runtime,dict) or set(runtime)-RUNTIME_FIELDS or not {'logosctl','modules_dir','wallet_binary','sodium_library','python'}<=set(runtime):
        raise Rejected('DEPLOYMENT_RUNTIME_INVALID')
    return value

def existing_dir(raw,code):
    path=Path(raw).expanduser().absolute()
    if path.is_symlink() or not path.is_dir():raise Rejected(code)
    return path.resolve(strict=True)

def validate(value):
    runtime=value['runtime']
    args=SimpleNamespace(
        logosctl=deploy.owned_file(Path(runtime['logosctl']),executable=True),
        modules_dir=deploy.owned_directory(Path(runtime['modules_dir'])),
        wallet_binary=deploy.owned_file(Path(runtime['wallet_binary']),executable=True),
        sodium_library=deploy.owned_file(Path(runtime['sodium_library'])),
    )
    python=deploy.owned_file(Path(runtime['python']),executable=True)
    agent=existing_dir(value['agent_root'],'AGENT_PROFILE_MISSING')
    owner=existing_dir(value['owner_root'],'OWNER_PROFILE_MISSING')
    wallet=existing_dir(value['wallet_root'],'WALLET_PROFILE_MISSING')
    session=existing_dir(value['session'],'CORE_SESSION_MISSING')
    tmp=Path(value['core_tmpdir']).expanduser().absolute()
    if not (agent/'settings.json').is_file() or not (owner/'deployment.json').is_file():raise Rejected('DEPLOYMENT_STATE_INCOMPLETE')
    roots=[agent,owner,wallet,session]
    if any(a==b or a in b.parents or b in a.parents for i,a in enumerate(roots) for b in roots[i+1:]):
        raise Rejected('DEPLOYMENT_ROOTS_OVERLAP')
    expected=socket_directory(session,create=False)
    if tmp not in {session/'tmp',expected}:raise Rejected('CORE_TMPDIR_MISMATCH')
    if tmp==session/'tmp':tmp=existing_dir(str(tmp),'CORE_TMPDIR_MISSING')
    settings=parse((agent/'settings.json').read_bytes())
    owner_link=parse((owner/'agent.json').read_bytes())
    public_identity=parse((agent/'vault/identity-public.json').read_bytes())
    if settings.get('agent_id')!=value.get('agent_address') or settings.get('agent_id')!=owner_link.get('agent_id') or settings.get('agent_id')!=public_identity.get('address'):
        raise Rejected('DEPLOYMENT_IDENTITY_MISMATCH')
    if settings.get('owner_public_key')!=owner_link.get('owner_public_key'):
        raise Rejected('DEPLOYMENT_OWNER_MISMATCH')
    deploy.validate_native_modules(args.modules_dir)
    settings=parse((agent/'settings.json').read_bytes());binding=parse((owner/'agent.json').read_bytes())
    if settings.get('agent_id')!=value.get('agent_address') or binding.get('agent_id')!=settings.get('agent_id') or binding.get('owner_public_key')!=settings.get('owner_public_key'):
        raise Rejected('DEPLOYMENT_IDENTITY_MISMATCH')
    return args,python,agent,owner,wallet,session,tmp

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment',type=Path,required=True,help='owner deployment.json created by deploy-agent.py')
    parser.add_argument('--check-only',action='store_true',help='validate recorded runtime/state without starting anything')
    parser.add_argument('--repair-socket-path',action='store_true',help='repair a failed first startup without creating another identity')
    options=parser.parse_args();value=read_receipt(options.deployment)
    args,python,agent,owner,wallet,session,tmp=validate(value)
    plan={'role':value['role'],'agent_address':value.get('agent_address'),'session':str(session),
          'starts_wallet':False,'faucet_requests':0,'model_calls':0,'risc0_dev_mode':'0'}
    if options.check_only:
        print(json.dumps({'check_only':True,**plan},indent=2));return
    if tmp==socket_directory(session,create=False):tmp=socket_directory(session)
    env=deploy.daemon_env(args,agent.parent,wallet.parent)
    env['COMMONS_RELAY_PYTHON']=str(python);env['TMPDIR']=str(tmp)+'/'
    runtime=value['runtime']
    if runtime.get('risc0_server_path'):
        env['RISC0_SERVER_PATH']=str(deploy.owned_file(Path(runtime['risc0_server_path']),executable=True))
    if runtime.get('lbc_root_dir'):
        env['LBC_ROOT_DIR']=str(deploy.owned_directory(Path(runtime['lbc_root_dir'])))
    probe=__import__('subprocess').run([str(args.logosctl),'--config-dir',str(session),'--json','daemon','status'],env=env,capture_output=True,text=True,timeout=20)
    try:daemon=json.loads(probe.stdout).get('daemon',{})
    except ValueError:raise Rejected('DAEMON_STATUS_INVALID') from None
    if daemon.get('status') not in ['running','not_running']:raise Rejected('DAEMON_STATUS_INVALID')
    running=daemon['status']=='running'
    if probe.returncode not in ([0] if running else [0,1]):raise Rejected('DAEMON_STATUS_INVALID')
    if running and (type(daemon.get('pid'))is not int or daemon['pid']<=0):raise Rejected('DAEMON_STATUS_INVALID')
    if options.repair_socket_path and len(os.fsencode(str(tmp)))+55>=104:
        if value.get('deployment_status') not in {'profile-created','core-started'}:
            raise Rejected('SOCKET_REPAIR_ONLY_FOR_INCOMPLETE_STARTUP')
        if running:
            deploy.run([str(args.logosctl),'--config-dir',str(session),'daemon','stop'],env=env,timeout=25)
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                check=subprocess.run([str(args.logosctl),'--config-dir',str(session),'--json','daemon','status'],env=env,capture_output=True,text=True,timeout=5)
                try:stopped=json.loads(check.stdout).get('daemon',{}).get('status')=='not_running'
                except ValueError:stopped=False
                if stopped:break
                time.sleep(.5)
            else:raise Rejected('CORE_STOP_NOT_CONFIRMED_NO_DUPLICATE_STARTED')
        tmp=socket_directory(session)
        value['previous_core_tmpdir']=value['core_tmpdir'];value['core_tmpdir']=str(tmp)+'/'
        value.update(deployment_status='profile-created',daemon_pid=None)
        deploy.save_deployment_receipt(options.deployment.expanduser().absolute(),value)
        env['TMPDIR']=str(tmp)+'/';running=False
    pid=daemon['pid'] if running else deploy.start_daemon(args,session,env)
    deploy.configure_runtime(args,session,agent,env)
    control=[str(python),str(ROOT/'scripts'/'control.py'),'--logosctl',str(args.logosctl),'--session',str(session),'--method','agent.start']
    started=json.loads(deploy.run(control,env=env,timeout=120))
    if started.get('success') is not True or started.get('result',{}).get('started') is not True:raise Rejected('AGENT_RESTART_FAILED')
    result=started['result']
    value.update(deployment_status='running',daemon_pid=pid)
    deploy.save_deployment_receipt(options.deployment.expanduser().absolute(),value)
    print(json.dumps({'restarted':True,**plan,'daemon_was_running':running,'daemon_pid':pid,'recovery':result.get('recovery',{})},indent=2))

if __name__=='__main__':
    try:main()
    except Rejected as error:raise SystemExit('Restart refused: '+str(error))
