#!/usr/bin/env python3
"""Run a real Commons Relay wallet proof against a fresh standalone LEZ sequencer.

Run ``scripts/prepare-local.sh`` first. This script never connects to the public
LEZ testnet and never invokes a model. It creates a new disposable local wallet,
funds only that freshly generated public account in local genesis, produces a real
private shield proof with ``RISC0_DEV_MODE=0``, submits the exact prepared
transaction, and independently verifies the resulting private balance.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
PIN='47eba256479f6f785acbd138834340703cd03401'
PROTOCOL=[1334328888,3910590567,1244219104,3671232111,3138827701,405554639,4064616947,1864368340]
URL='http://127.0.0.1:34341'


def rpc(method:str):
    body=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':[]}).encode()
    req=urllib.request.Request(URL,data=body,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=5) as response:
        value=json.load(response)
    if 'error' in value:raise RuntimeError('local sequencer RPC failed: '+method)
    return value['result']


def stop(process):
    if process is None or process.poll() is not None:return
    try:
        os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=10)
    except ProcessLookupError:pass


def result(output:str)->dict:
    for line in reversed(output.splitlines()):
        if line.startswith('{"relay_wallet_result":'):
            value=json.loads(line)['relay_wallet_result']
            if not isinstance(value,dict):raise RuntimeError('invalid wallet result')
            return value
    raise RuntimeError('wallet produced no typed result')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,default=ROOT/'out/local')
    p.add_argument('--timeout-seconds',type=int,default=3*3600)
    p.add_argument('--amount',type=int,default=5)
    args=p.parse_args()
    out=args.out.resolve()
    if out==ROOT or not out.is_relative_to(ROOT):p.error('output must stay inside this checkout')
    if not 60<=args.timeout_seconds<=6*3600:p.error('timeout must be 60 seconds to 6 hours')
    if not 1<=args.amount<=100:p.error('local proof amount must be 1..100')
    paths=json.loads((out/'proof-deps/paths.json').read_text())
    node=out/'host/debug/sequencer_service';wallet=out/'wallet/debug/commons-relay-wallet';lez=out/'lez'
    for binary in [node,wallet,Path(paths['r0vm'])]:
        if not binary.is_file() or not os.access(binary,os.X_OK):raise SystemExit('missing executable prerequisite: '+str(binary))
    actual=subprocess.check_output(['git','-C',str(lez),'rev-parse','HEAD'],text=True).strip()
    if actual!=PIN:raise SystemExit('refusing mismatched LEZ source revision')
    with socket.socket() as check:
        check.settimeout(1)
        if check.connect_ex(('127.0.0.1',34341))==0:raise SystemExit('port 34341 is already in use')
    os.umask(0o077);run=out/('relay-local-'+time.strftime('%Y%m%d-%H%M%S',time.gmtime()));run.mkdir(mode=0o700)
    for name in ['home','tmp','wallet','node','public']:(run/name).mkdir(mode=0o700)
    env={k:os.environ[k] for k in ['PATH','LANG','SSL_CERT_FILE','DYLD_LIBRARY_PATH','LD_LIBRARY_PATH'] if k in os.environ}
    env.update({'HOME':str(run/'home'),'TMPDIR':str(run/'tmp')+'/','RISC0_DEV_MODE':'0','RISC0_PROVER':'ipc','RISC0_EXECUTOR':'ipc','RAYON_NUM_THREADS':os.environ.get('COMMONS_RELAY_PROOF_THREADS','3'),'SUPPRESS_VERBOSE_PRINTS':'1','LBC_ROOT_DIR':paths['lbc_root'],'RISC0_SERVER_PATH':paths['r0vm'],'CARGO_NET_OFFLINE':'true'})
    lib=paths['rapidsnark_lib']
    for key in ['DYLD_LIBRARY_PATH','LD_LIBRARY_PATH']:env[key]=lib+(':'+env[key] if env.get(key) else '')
    deadline=time.monotonic()+args.timeout_seconds;node_process=None;private_log=run/'private-wallet.log';report={'status':'failed','network':'local-standalone-only','lez_revision':PIN,'risc0_dev_mode':'0','prover':'local-ipc','amount':str(args.amount)}
    def call(*parts,timeout=None):
        remaining=max(1,int(deadline-time.monotonic()));limit=min(timeout or remaining,remaining)
        completed=subprocess.run([str(wallet),*map(str,parts)],cwd=ROOT,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=limit)
        with private_log.open('a') as log:log.write(completed.stderr)
        if completed.returncode:raise RuntimeError('wallet stage failed: '+str(parts[0]))
        return result(completed.stdout)
    try:
        created=call('init-local-offline',run/'wallet',timeout=30)
        if created.get('offline') is not True or created.get('network_transactions')!=0 or created.get('endpoint')!='http://127.0.0.1:34341/':raise RuntimeError('offline wallet preparation invariant failed')
        source=lez/'lez/sequencer/service/configs/debug/sequencer_config.json';config=json.loads(source.read_text());config['home']=str(run/'node');config['block_create_timeout']='1s'
        if 'gossip' in config:
            config['gossip']['listen_addr']='/ip4/127.0.0.1/udp/0/quic-v1';config['gossip']['bootstrap_peers']=[]
        config['metrics_address']=None
        config['genesis'].append({'supply_account':{'account_id':created['payer_base58'],'balance':100}})
        config_path=run/'node-config.json';config_path.write_text(json.dumps(config,indent=2)+'\n')
        with (run/'private-node.log').open('wb') as log:
            node_process=subprocess.Popen([str(node),str(config_path),'--listen-address','127.0.0.1','--port','34341'],cwd=run,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        for _ in range(120):
            if node_process.poll() is not None:raise RuntimeError('local sequencer exited before readiness')
            try:
                if rpc('getProgramIds').get('privacy_preserving_circuit')!=PROTOCOL:raise RuntimeError('unexpected private circuit fingerprint')
                break
            except (OSError,ValueError):time.sleep(1)
        else:raise TimeoutError('local sequencer readiness timeout')
        intent=run/'wallet/shield-intent.json';intent.write_text(json.dumps({'kind':'shield','arguments':{'amount':str(args.amount)},'expires_at':int(time.time())+min(7200,args.timeout_seconds)})+'\n');intent.chmod(0o600)
        started=time.monotonic();prepared=call('prepare',run/'wallet','local-real-shield',intent)
        if prepared.get('state')!='prepared' or prepared.get('private') is not True:raise RuntimeError('real private transaction was not prepared')
        submitted=call('broadcast',run/'wallet','local-real-shield',timeout=120)
        for _ in range(120):
            if submitted.get('state')=='confirmed':break
            time.sleep(1);submitted=call('reconcile',run/'wallet','local-real-shield',timeout=30)
        if submitted.get('state')!='confirmed':raise RuntimeError('local private transaction was not confirmed')
        balance=call('balance',run/'wallet',timeout=60)
        if balance.get('balance')!=str(args.amount):raise RuntimeError('independent private balance verification failed')
        report.update({'status':'passed','wallet_account':created['private_account'],'tx_hash':submitted['tx_hash'],'block_id':submitted['block_id'],'proof_millis':prepared.get('proof_millis'),'wall_millis':round((time.monotonic()-started)*1000),'verified_private_balance':balance['balance']})
    finally:
        stop(node_process);(run/'public/report.json').write_text(json.dumps(report,indent=2)+'\n');(out/'latest-local-report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2),flush=True)
    if report['status']!='passed':raise SystemExit(1)

if __name__=='__main__':main()
