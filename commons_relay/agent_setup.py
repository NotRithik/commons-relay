"""Explicit local agent creation from the owner UI; no model or funding.

The installer supplies a private runtime configuration. UI input supplies only
an agent name and a reviewed job id. Commands are argv lists, never shell text.
An uncertain launch retains its original job and directories.
"""
from __future__ import annotations
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import subprocess
import sys
import tempfile
import time
from .codec import Rejected, canonical
from .messaging import Contact

JOB = re.compile(r'^agent-[a-f0-9]{16}$')
CONFIG_FIELDS = {'schema', 'deploy_script', 'logosctl', 'modules_dir', 'wallet_binary',
                 'sodium_library', 'storage_preset', 'state_root', 'wallet_root',
                 'session_root', 'owner_transport_root', 'owner_contact',
                 'bootstrap_nodes', 'cluster_id', 'risc0_server_path', 'lbc_root_dir'}


def private_dir(path: Path):
    if path.is_symlink(): raise Rejected('SETUP_DIRECTORY_INVALID')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise Rejected('SETUP_DIRECTORY_NOT_PRIVATE')
    return path


def read_json(path: Path, maximum=64000):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > maximum:
            raise Rejected('SETUP_FILE_NOT_PRIVATE')
        return json.loads(stream.read(maximum + 1))


def save(path: Path, value):
    if path.is_symlink(): raise Rejected('SETUP_FILE_INVALID')
    fd, name = tempfile.mkstemp(prefix='.setup-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical(value)); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if temporary.exists(): temporary.unlink()


@contextmanager
def locked(path, wait=False):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        yield
    except BlockingIOError:
        raise Rejected('SETUP_ALREADY_IN_PROGRESS') from None
    finally:
        os.close(fd)


def configuration(root):
    return validate_configuration(read_json(root / '.setup-runtime.json'))


def validate_configuration(value):
    if not isinstance(value, dict) or set(value) not in (CONFIG_FIELDS,CONFIG_FIELDS | {'planner_runtime'}) or value['schema'] != 1:
        raise Rejected('SETUP_CONFIGURATION_INVALID')
    for key in ['deploy_script', 'logosctl', 'wallet_binary', 'sodium_library', 'storage_preset', 'risc0_server_path']:
        path = Path(value[key])
        if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o022:
            raise Rejected('SETUP_RUNTIME_UNAVAILABLE')
    for key in ['modules_dir', 'lbc_root_dir', 'owner_transport_root']:
        path = Path(value[key])
        if not path.is_absolute() or path.is_symlink() or not path.is_dir():
            raise Rejected('SETUP_RUNTIME_UNAVAILABLE')
    for key in ['state_root', 'wallet_root', 'session_root']:
        path = Path(value[key])
        if not path.is_absolute() or path.is_symlink() or not path.is_dir() or path.stat().st_mode & 0o077:
            raise Rejected('SETUP_DIRECTORY_NOT_PRIVATE')
    if 'planner_runtime' in value:
        planner=value['planner_runtime']
        if not isinstance(planner,dict) or set(planner)!={'node','runner','runner_sha256','budget_file','budget_micro_usd'}:
            raise Rejected('SETUP_PLANNER_RUNTIME_INVALID')
        for name in ('node','runner','budget_file'):
            p=Path(planner[name])
            if not p.is_absolute() or p.is_symlink() or not p.is_file():raise Rejected('SETUP_PLANNER_RUNTIME_INVALID')
        if not os.access(planner['node'],os.X_OK) or hashlib.sha256(Path(planner['runner']).read_bytes()).hexdigest()!=planner['runner_sha256']:
            raise Rejected('SETUP_PLANNER_RUNTIME_CHANGED')
        ledger=read_json(Path(planner['budget_file']),200000)
        if type(planner['budget_micro_usd']) is not int or not 1<=planner['budget_micro_usd']<=15000000 or ledger.get('maximum_micro_usd')!=planner['budget_micro_usd']:
            raise Rejected('SETUP_PLANNER_BUDGET_MISMATCH')
    Contact.from_public(value['owner_contact'])
    if type(value['cluster_id']) is not int or not 2 <= value['cluster_id'] <= 65535:
        raise Rejected('SETUP_CONFIGURATION_INVALID')
    nodes = value['bootstrap_nodes']
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 8 or any(
        not isinstance(node, str) or not re.fullmatch(r'/ip4/127[.]0[.]0[.]1/tcp/[0-9]{4,5}/p2p/[A-Za-z0-9]{20,100}', node) for node in nodes):
        raise Rejected('SETUP_LOCAL_NETWORK_REQUIRED')
    return value


def signature(config):
    data = dict(config)
    for key in ['deploy_script', 'logosctl', 'wallet_binary', 'sodium_library', 'storage_preset']:
        data[key + '_sha256'] = hashlib.sha256(Path(config[key]).read_bytes()).hexdigest()
    restart = Path(config['deploy_script']).with_name('restart-agent.py')
    if restart.is_symlink() or not restart.is_file(): raise Rejected('SETUP_RESTART_HELPER_MISSING')
    data['restart_sha256'] = hashlib.sha256(restart.read_bytes()).hexdigest()
    data['setup_helper_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return hashlib.sha256(canonical(data)).hexdigest()


def folder(root, job_id):
    if not isinstance(job_id, str) or not JOB.fullmatch(job_id): raise Rejected('SETUP_JOB_INVALID')
    return root / '.setup-jobs' / job_id


def public(job):
    return {'agent_setup': True, **{key: job.get(key) for key in
        ['id', 'name', 'state', 'message', 'review_hash', 'expires_at', 'profile_name', 'agent_address', 'resume_existing']},
        'location': 'This computer', 'funding': 'Starts with no funds',
        'model': 'Off until configured', 'public_listing': False,
        'network': 'Existing local Logos Messaging network',
        'owner_keys': 'Kept on this computer', 'no_automatic_payment': True}


def preview(root, name):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(c) < 32 for c in name):
        raise Rejected('INVALID_AGENT_NAME')
    config = configuration(root)
    jobs = private_dir(root / '.setup-jobs')
    with locked(jobs / '.review-lock'):
        candidates=[p for p in jobs.iterdir() if JOB.fullmatch(p.name) and p.is_dir() and not p.is_symlink()]
        existing=[read_json(p/'job.json') for p in candidates]
        same=[job for job in existing if job['name'].casefold()==name.strip().casefold()]
        # A failed real setup outranks unused review records. It is never replaced.
        prior=next((job for job in same if job['state']!='review'),same[0] if same else None)
        if prior and prior['state']!='review':return public(prior)
        now=int(time.time());runtime_hash=signature(config)
        if prior and prior['expires_at']>now and prior['runtime_hash']==runtime_hash:return public(prior)
        if len(candidates)>=32 and not prior:raise Rejected('SETUP_JOB_LIMIT')
        if prior:
            job_id=prior['id'];directory=jobs/job_id;port=prior['port']
        else:
            job_id='agent-'+secrets.token_hex(8);directory=private_dir(jobs/job_id)
            with socket.socket() as probe:
                probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        intent={'id':job_id,'name':name.strip(),'runtime_hash':runtime_hash,'port':port,'expires_at':now+1800}
        job={**intent,'review_hash':hashlib.sha256(canonical(intent)).hexdigest(),
             'state':'review','message':'Review before creating a new, unfunded local agent.',
             'profile_name':job_id,'agent_address':None}
        save(directory/'job.json',job);save(directory/'owner-contact.json',config['owner_contact'])
        return public(job)


def status(root, job_id):
    return public(read_json(folder(root, job_id) / 'job.json'))


def latest(root):
    directory=root / '.setup-jobs'
    if not directory.exists(): return {'agent_setup':True,'state':'not_started'}
    if directory.is_symlink(): raise Rejected('SETUP_DIRECTORY_INVALID')
    candidates=[p for p in directory.iterdir() if JOB.fullmatch(p.name) and p.is_dir() and not p.is_symlink()]
    if len(candidates)>32: raise Rejected('SETUP_JOB_LIMIT')
    candidates.sort(key=lambda p:(p/'job.json').stat().st_mtime,reverse=True)
    jobs=[read_json(p/'job.json') for p in candidates]
    selected=next((job for job in jobs if job['state'] in {'queued','creating','needs_attention'}),jobs[0] if jobs else None)
    return public(selected) if selected else {'agent_setup':True,'state':'not_started'}


def preview_recovery(root, job_id):
    directory = folder(root, job_id)
    with locked(directory / '.launch-lock'):
        job = read_json(directory / 'job.json')
        if job['state'] != 'needs_attention': raise Rejected('SETUP_RECOVERY_NOT_REQUIRED')
        config = configuration(root)
        receipt = read_json(root / job_id / 'deployment.json')
        expected = {'owner_root': root / job_id, 'agent_root': Path(config['state_root']) / job_id,
                    'wallet_root': Path(config['wallet_root']) / job_id,
                    'session': Path(config['session_root']) / job_id}
        if any(Path(receipt.get(k,'')) != value for k,value in expected.items()):
            raise Rejected('SETUP_RECOVERY_BINDING_CHANGED')
        job.update(state='review', resume_existing=True, runtime_hash=signature(config),
                   expires_at=int(time.time())+1800,
                   message='Resume this existing agent. Its identity, wallet and saved state will be kept.')
        intent={k:job[k] for k in ['id','name','runtime_hash','expires_at','resume_existing']}
        job['review_hash']=hashlib.sha256(canonical(intent)).hexdigest()
        save(directory / 'job.json',job)
    return public(job)


def start(root, job_id, reviewed_hash):
    directory = folder(root, job_id)
    with locked(directory / '.launch-lock'):
        job = read_json(directory / 'job.json')
        if reviewed_hash != job.get('review_hash'): raise Rejected('SETUP_REVIEW_CHANGED')
        if job['state'] != 'review': return public(job)
        config = configuration(root)
        if int(time.time()) >= job['expires_at']: raise Rejected('SETUP_REVIEW_EXPIRED')
        if signature(config) != job['runtime_hash']: raise Rejected('SETUP_RUNTIME_CHANGED_REVIEW_AGAIN')
        launcher = Path(__file__).resolve().parents[1] / 'commons_relay_agent_setup.py'
        if launcher.is_symlink() or not launcher.is_file(): raise Rejected('SETUP_HELPER_UNAVAILABLE')
        job.update(state='queued', message='Creating your agent. Closing this window will not create it twice.')
        save(directory / 'job.json', job)
        env = {'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin', 'HOME': str(root),
               'PYTHONNOUSERSITE': '1', 'LANG': 'en_US.UTF-8'}
        with (directory / 'private-setup.log').open('ab') as output:
            child = subprocess.Popen([str(Path(sys.executable).resolve()), '-I', str(launcher), str(root), job_id],
                cwd=directory, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                start_new_session=True)
        job['pid'] = child.pid
        save(directory / 'job.json', job)
    return public(job)


def handle(root, request):
    action = request.get('action')
    if request == {'action':'setup_latest'}: return latest(root)
    if action == 'setup_preview' and set(request) == {'action', 'name'}: return preview(root, request['name'])
    if action == 'setup_status' and set(request) == {'action', 'job_id'}: return status(root, request['job_id'])
    if action == 'setup_recovery' and set(request) == {'action', 'job_id'}: return preview_recovery(root, request['job_id'])
    if action == 'setup_start' and set(request) == {'action', 'job_id', 'review_hash'}:
        return start(root, request['job_id'], request['review_hash'])
    raise Rejected('INVALID_SETUP_REQUEST')


def run(root, job_id):
    root = root.resolve(strict=True); directory = folder(root, job_id)
    with locked(directory / '.launch-lock', wait=True):
        job = read_json(directory / 'job.json')
        if job['state'] != 'queued': return
        job.update(state='creating', message='Starting Logos Core and creating the separate agent identity.')
        save(directory / 'job.json', job)
    with locked(directory / '.worker-lock'):
        try:
            config = configuration(root)
            if signature(config) != job['runtime_hash']: raise Rejected('SETUP_RUNTIME_CHANGED')
            owner = root / job_id
            state = Path(config['state_root']) / job_id
            wallet = Path(config['wallet_root']) / job_id
            session = Path(config['session_root']) / job_id
            if job.get('resume_existing'):
                if not (owner / 'deployment.json').is_file(): raise Rejected('SETUP_RECOVERY_RECEIPT_MISSING')
                argv=[str(Path(sys.executable).resolve()),str(Path(config['deploy_script']).with_name('restart-agent.py')),
                      '--deployment',str(owner/'deployment.json'),'--repair-socket-path']
            else:
                if any(path.exists() for path in [owner, state, wallet, session]):
                    raise Rejected('SETUP_EXISTING_STATE_PRESERVED')
                argv = [str(Path(sys.executable).resolve()), config['deploy_script'], '--role', 'storage',
                        '--agent-root', str(state), '--owner-root', str(owner), '--wallet-root', str(wallet),
                        '--session', str(session), '--agent-name', job['name'], '--owner-contact', str(directory / 'owner-contact.json'),
                        '--delivery-port', str(job['port']), '--local-cluster-id', str(config['cluster_id'])]
                for field, flag in [('logosctl','--logosctl'),('modules_dir','--modules-dir'),('wallet_binary','--wallet-binary'),
                                    ('sodium_library','--sodium-library'),('storage_preset','--storage-preset')]:
                    argv.extend([flag, config[field]])
                for peer in config['bootstrap_nodes']: argv.extend(['--local-bootstrap', peer])
            env = os.environ.copy()
            env.update(RISC0_DEV_MODE='0', RISC0_PROVER='ipc', RISC0_EXECUTOR='ipc',
                       RISC0_SERVER_PATH=config['risc0_server_path'], LBC_ROOT_DIR=config['lbc_root_dir'])
            completed = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=240)
            (directory / 'private-deployment-output.log').write_bytes(completed.stdout + completed.stderr)
            if completed.returncode: raise Rejected('SETUP_DEPLOYMENT_FAILED_STATE_PRESERVED')
            receipt = read_json(owner / 'deployment.json')
            info = read_json(owner / 'agent.json')
            contact = Contact.from_public(read_json(owner / 'agent-contact.json'))
            if receipt.get('deployment_status') != 'running' or contact.address != receipt.get('agent_address') or info.get('agent_id') != contact.address:
                raise Rejected('SETUP_IDENTITY_BINDING_FAILED')
            if receipt.get('owner_messaging_address') != config['owner_contact']['address']:
                raise Rejected('SETUP_OWNER_CHANNEL_MISMATCH')
            if config.get('planner_runtime') and not (state/'planner.json').exists():
                runtime=config['planner_runtime']
                base={'schema_version':1,**runtime,'model':'configure-model-first','api_key_file':'',
                      'input_micro_per_million':0,'output_micro_per_million':0,'require_owner_setup':True}
                # The shared budget is referenced, not reset or enlarged. No
                # credentials are copied and start() rejects unconfigured inference.
                fd=os.open(state/'planner.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'wb') as stream:stream.write(canonical(base));stream.flush();os.fsync(stream.fileno())
            info['display_name'] = job['name']; save(owner / 'agent.json', info)
            contacts_path = Path(config['owner_transport_root']) / 'contacts.json'
            with locked(root / '.setup-contacts-lock'):
                before = contacts_path.read_bytes()
                contacts = read_json(contacts_path, 200000)
                if not isinstance(contacts, list) or len(contacts) >= 256: raise Rejected('SETUP_CONTACT_LIMIT')
                contact = Contact(contact.address, contact.signing_key, contact.box_key, job['name'])
                previous = next((item for item in contacts if item.get('address') == contact.address),None)
                if previous is not None and previous != contact.public(): raise Rejected('SETUP_CONTACT_BINDING_CHANGED')
                if contacts_path.read_bytes() != before: raise Rejected('SETUP_CONTACTS_CHANGED')
                if previous is None: save(contacts_path, [*contacts, contact.public()])
            job.update(state='ready', agent_address=contact.address,
                       message='Agent created. Connect to it below. It has no funds, no configured model and no public listing.')
        except Exception as error:
            code = str(error) if isinstance(error, Rejected) else type(error).__name__
            job.update(state='needs_attention', message='Setup stopped: ' + code + '. Existing state is preserved; do not create a replacement to retry.')
        save(directory / 'job.json', job)


def worker_main():
    if len(sys.argv) != 3: raise SystemExit('Expected owner root and setup job id')
    os.umask(0o077)
    run(Path(sys.argv[1]), sys.argv[2])
