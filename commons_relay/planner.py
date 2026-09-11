"""Optional, owner-granted Pi conversation runner inside the Core service.

The local planner process receives a delegate key, never an owner key.
The language-model request receives neither key. Every tool request still
passes through Engine.submit and the durable controller. Starting a conversation
is explicit; restarting the service never repeats a paid model request.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import stat
import subprocess
import threading
import time
from .codec import Rejected, canonical, parse, identifier, unb64, amount
from .signing import Ed25519, key_id, protected_directory, verify_envelope
from .engine import GRANT_DOMAIN, DELEGATED_DOMAIN
from .result_plain import describe_tasks, result_plain, service_result_text

READ_SKILLS = frozenset({'storage.list', 'wallet.balance', 'wallet.history',
    'program.query', 'agent.card', 'agent.discover', 'agent.ping', 'meta.skills', 'meta.status'})
MAX_TURN_SECONDS = 600
MAX_FRAME = 60000


def private_json(path: Path, limit=32000) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise Rejected('PLANNER_CONFIGURATION_UNAVAILABLE')
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise Rejected('PLANNER_CONFIGURATION_NOT_PRIVATE')
    value = parse(path.read_bytes())
    if not isinstance(value, dict):
        raise Rejected('INVALID_PLANNER_CONFIGURATION')
    return value


@dataclass(frozen=True)
class Configuration:
    node: str
    runner: str
    model: str
    api_key_file: str
    budget_file: str
    budget_micro_usd: int
    input_micro_per_million: int
    output_micro_per_million: int
    endpoint: str = "https://api.openai.com/v1"
    api: str = "responses"
    max_output_tokens: int = 1536
    credential_mode: str = "keep"
    configuration_hash: str = ""
    credential_sha256: str = ""
    runner_sha256: str = ""

    @classmethod
    def load(cls, path: Path, *, owner_key=None, crypto=None, agent_id=""):
        raw = private_json(path)
        required = {'schema_version', 'node', 'runner', 'runner_sha256', 'model',
                    'api_key_file', 'budget_file', 'budget_micro_usd',
                    'input_micro_per_million', 'output_micro_per_million'}
        if set(raw) not in (required, required | {'require_owner_setup'}) or raw['schema_version'] != 1:
            raise Rejected('INVALID_PLANNER_CONFIGURATION')
        if type(raw.get('require_owner_setup', False)) is not bool: raise Rejected('INVALID_PLANNER_CONFIGURATION')
        raw.pop('require_owner_setup',None)
        from .inference_settings import effective, MODEL
        raw = effective(path, raw, owner_key=owner_key, crypto=crypto, agent_id=agent_id)
        if not isinstance(raw.get('model'), str) or not MODEL.fullmatch(raw['model']):
            raise Rejected('INVALID_PLANNER_MODEL')
        for name in ['node', 'runner', 'budget_file'] + (['api_key_file'] if raw['api_key_file'] else []):
            if not isinstance(raw[name], str): raise Rejected('INVALID_PLANNER_PATH')
            candidate = Path(raw[name])
            if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_file():
                raise Rejected('INVALID_PLANNER_PATH')
        if not os.access(raw['node'], os.X_OK):
            raise Rejected('PLANNER_NODE_UNAVAILABLE')
        if hashlib.sha256(Path(raw['runner']).read_bytes()).hexdigest() != raw['runner_sha256']:
            raise Rejected('PLANNER_RUNNER_CHANGED')
        for name in ['budget_file'] + (['api_key_file'] if raw['api_key_file'] else []):
            if stat.S_IMODE(Path(raw[name]).stat().st_mode) & 0o077:
                raise Rejected('PLANNER_SECRET_PERMISSIONS')
        for name in ['budget_micro_usd', 'input_micro_per_million', 'output_micro_per_million']:
            if type(raw[name]) is not int or not (1 if name == 'budget_micro_usd' else 0) <= raw[name] <= 1000000000:
                raise Rejected('INVALID_PLANNER_BUDGET')
        if raw['budget_micro_usd'] > 15000000:
            raise Rejected('INVALID_PLANNER_BUDGET')
        ledger = private_json(Path(raw['budget_file']), 200000)
        if ledger.get('maximum_micro_usd') != raw['budget_micro_usd']:
            raise Rejected('PLANNER_BUDGET_MISMATCH')
        fields = {key: raw[key] for key in cls.__dataclass_fields__ if key != 'configuration_hash'}
        fields['configuration_hash'] = hashlib.sha256(canonical(fields)).hexdigest()
        return cls(**fields)

    def public_budget(self) -> dict:
        ledger = private_json(Path(self.budget_file), 200000)
        spent = ledger.get('settled_micro_usd')
        reservations = ledger.get('reservations', {})
        if type(spent) is not int or spent < 0 or not isinstance(reservations, dict):
            raise Rejected('INVALID_PLANNER_BUDGET')
        if any(type(value) is not int or value < 0 for value in reservations.values()):
            raise Rejected('INVALID_PLANNER_BUDGET')
        return {'limit_micro_usd': self.budget_micro_usd, 'estimated_spent_micro_usd': spent,
                'reserved_micro_usd': sum(reservations.values()),
                'remaining_micro_usd': max(0, self.budget_micro_usd-spent-sum(reservations.values()))}


class Planner:
    def __init__(self, service, *, clock=time.time):
        self.service = service
        self.engine = service.engine
        self.clock = clock
        self.lock = threading.RLock()
        self.root = protected_directory(service.root / 'planner')
        self.crypto = Ed25519(self.root / 'crypto')
        self.key_path = self.crypto.scratch / 'delegate.pem'
        self.public = self.crypto.public(self.key_path) if self.key_path.exists() else self.crypto.generate(self.key_path)
        self.delegate_id = key_id(self.public)
        database = self.root / 'conversations.sqlite'
        if database.is_symlink():
            raise Rejected('SYMLINK_PLANNER_STATE')
        if not database.exists():
            fd = os.open(database, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
            os.close(fd)
        if stat.S_IMODE(database.stat().st_mode) & 0o077:
            raise Rejected('PLANNER_STATE_NOT_PRIVATE')
        self.db = sqlite3.connect(database, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, grant_hash TEXT NOT NULL, prompt TEXT NOT NULL,
            state TEXT NOT NULL, reply TEXT NOT NULL DEFAULT '', error TEXT,
            created INTEGER NOT NULL, updated INTEGER NOT NULL,
            mode TEXT NOT NULL, task_ids TEXT NOT NULL DEFAULT '[]')''')
        self.db.execute("UPDATE conversations SET state='interrupted', error='PLANNER_RESTARTED', updated=? WHERE state IN ('queued','thinking','working')", (int(clock()),))
        self.db.execute('CREATE TABLE IF NOT EXISTS conversation_updates(goal_id TEXT NOT NULL,sequence INTEGER NOT NULL,text TEXT NOT NULL,created INTEGER NOT NULL,PRIMARY KEY(goal_id,sequence))')
        self.thread = None
        self.process = None
        self.active = None
        self.cancel_event = threading.Event()
        self.closing = False
        from .conversation_permissions import ConversationPermissions
        self.permissions = ConversationPermissions(self)

    def configuration(self):
        path = self.service.root / 'planner.json'
        return Configuration.load(path, owner_key=self.engine.owner_key, crypto=self.engine.crypto, agent_id=self.engine.agent) if path.exists() else None

    def requires_configuration(self):
        path=self.service.root/'planner.json'
        return path.is_file() and private_json(path).get('require_owner_setup') is True and not (self.service.root/'inference.json').is_file()

    def status(self) -> dict:
        try:
            config = self.configuration()
            error = None
        except Rejected as failure:
            config = None; error = str(failure)
        registered = [row['id'] for row in self.engine.registry.describe()]
        read = [name for name in registered if name in READ_SKILLS]
        actions = [name for name in registered if name != 'meta.configure']
        result = {'planner_status': True, 'agent_id': self.engine.agent,
            'enabled': config is not None and not (self.requires_configuration()),
            'model': config.model if config and not (self.requires_configuration()) else None,
            'provider': ('OpenAI' if config.endpoint == 'https://api.openai.com/v1' else __import__('urllib.parse', fromlist=['urlsplit']).urlsplit(config.endpoint).netloc) if config else None, 'delegate_key_id': self.delegate_id,
            'read_skills': read, 'action_skills': actions, 'active_goal': self.active,
            'maximum_steps': 8, 'error': error,
            'privacy': 'Messages, relevant conversation history and requested tool results go to the selected inference endpoint. Private signing keys are not supplied.'}
        if config:
            if config.endpoint == 'https://api.openai.com/v1' and not config.api_key_file and not self.requires_configuration():
                result.update(enabled=False,error='INFERENCE_CREDENTIAL_REQUIRED')
            result.update(endpoint=config.endpoint, api=config.api, configuration_hash=config.configuration_hash,
                max_output_tokens=config.max_output_tokens, credential_configured=bool(config.api_key_file),
                credential_digest=config.credential_sha256, input_micro_per_million=config.input_micro_per_million,
                output_micro_per_million=config.output_micro_per_million)
            try:
                from .inference_settings import InferenceStore
                from .codec import b64
                result['configuration_box_key'] = b64(InferenceStore(self).box_pair()[1])
            except (Rejected, OSError):
                result['configuration_box_key'] = None
            try: result['budget'] = config.public_budget()
            except Rejected as failure: result.update(enabled=False, error=str(failure))
        return result

    def _row(self, goal_id):
        identifier(goal_id)
        with self.lock:
            row = self.db.execute('SELECT * FROM conversations WHERE id=?', (goal_id,)).fetchone()
        if row is None:
            raise Rejected('PLANNER_GOAL_NOT_FOUND')
        return row

    def view(self, goal_id) -> dict:
        self.permissions.reconcile_existing(goal_id)
        row = self._row(goal_id)
        goal = {'id': row['id'], 'prompt': row['prompt'], 'reply': row['reply'],
            'state': row['state'], 'error': row['error'], 'mode': row['mode'],
            'created': row['created'], 'updated': row['updated'],
            'task_ids': json.loads(row['task_ids']), 'permission': self.permissions.view(goal_id)}
        with self.lock:
            updates=list(self.db.execute('SELECT text,created FROM conversation_updates WHERE goal_id=? ORDER BY sequence LIMIT 8',(goal_id,)))
        if updates:goal['updates']=[{'text':item['text'],'created':item['created']} for item in updates]
        permission = goal['permission']
        if permission and permission['decision'] == 'approve' and permission.get('task_id'):
            task = self.engine.get(permission['task_id'])
            request = permission['request']
            if (task['skill'] != request['skill'] or task.get('arguments') != request['arguments']
                    or task['maximum_spend'] != request['maximum_spend'] or task['asset'] != request['asset']):
                raise Rejected('PERMISSION_TASK_BINDING_MISMATCH')
            permission['task_state'] = task['state']
            goal['updated'] = max(goal['updated'], task['updated'])
            # Display a recorded tool outcome, not a new model answer or a retry.
            # This derived view also works after a restart without modifying history.
            if task['state'] == 'completed':
                summary = result_plain(task.get('skill'), task.get('result'), state='completed')
                content=service_result_text(task.get('result')) if task.get('skill')=='agent.task' else ''
                reply=summary or 'The action you approved finished. The recorded result is attached.'
                if content:reply+='\n\nReturned by the service:\n'+content
                goal.update(state='completed', error=None, reply=reply, outcome_from_receipts=True)
            elif task['state'] in ('failed', 'rejected', 'canceled'):
                goal.update(state='cancelled' if task['state'] == 'canceled' else 'failed', error=task['error'],
                    reply=result_plain(task.get('skill'), task.get('result'), state=task['state'], error=task.get('error'))
                    or 'The action you approved did not finish. Open its details for the recorded reason.')
        elif goal['state']=='waiting' and goal['task_ids'] and not (permission and permission['decision']=='pending'):
            if len(goal['task_ids'])>16:raise Rejected('PLANNER_TASK_HISTORY_LIMIT')
            receipts=[self.engine.get(task_id) for task_id in goal['task_ids']]
            terminal={'completed','failed','rejected','canceled'}
            if all(task['state'] in terminal for task in receipts):
                failed=[task for task in receipts if task['state']!='completed']
                goal['updated']=max([goal['updated']]+[task['updated'] for task in receipts])
                goal['state']='failed' if failed else 'completed'
                goal['error']=failed[0].get('error') if failed else None
                summary = describe_tasks(receipts)
                if failed:
                    goal['reply'] = summary or 'One or more linked tasks did not finish. Open their details for the recorded reason.'
                elif summary:
                    goal['reply'] = summary
                elif len(receipts) == 1:
                    goal['reply'] = 'The requested action finished. Open its details if you want the raw record.'
                else:
                    goal['reply'] = f'All {len(receipts)} linked actions finished.'
                goal['outcome_from_receipts']=True
        elif (goal['task_ids'] and goal['state'] in ('completed', 'failed', 'cancelled')
              and not (permission and permission['decision'] == 'approve')):
            reply = goal.get('reply') or ''
            generic = (not reply) or ('Open Activity' in reply) or reply.startswith('The agent finished this turn')
            if generic:
                if len(goal['task_ids']) > 16:
                    raise Rejected('PLANNER_TASK_HISTORY_LIMIT')
                summary = describe_tasks([self.engine.get(task_id) for task_id in goal['task_ids']])
                if summary:
                    goal['reply'] = summary
                    goal['outcome_from_receipts'] = True
        response={'planner_goal': True, 'agent_id': self.engine.agent, 'goal': goal}
        if len(canonical(response))>12500:goal.pop('updates',None)
        if len(canonical(response))>13200 and permission:
            request=permission['request']
            goal['permission']={**permission,'request':{key:request[key] for key in ['skill','maximum_spend','asset','intent_hash']}}
            goal['permission']['request']['reason']=request['reason'][:200]
            goal['permission_preview']=True
            # The complete request remains available through the dedicated
            # signed permission-review route, never approved from this preview.
        return response

    def permission_review(self, goal_id):
        self._row(goal_id)
        return {'permission_review': True, 'agent_id': self.engine.agent,
                'id': goal_id, 'permission': self.permissions.view(goal_id)}

    def history(self, offset=0) -> dict:
        if type(offset) is not int or not 0 <= offset <= 100000:
            raise Rejected('INVALID_OWNER_PAGE')
        with self.lock:
            rows = self.db.execute('SELECT id FROM conversations ORDER BY created DESC,rowid DESC LIMIT 8 OFFSET ?', (offset,)).fetchall()
            total = self.db.execute('SELECT COUNT(*) FROM conversations').fetchone()[0]
        goals = []
        for row in rows:
            item = self.view(row['id'])['goal']
            # Each single goal is available intact through planner.goal.
            compact = {**item, 'prompt': item['prompt'][:1000], 'reply': item['reply'][:1500],
                       'preview': len(item['prompt'])>1000 or len(item['reply'])>1500}
            if compact.get('permission'):
                permission=compact['permission']; request=permission['request']
                compact['permission']={**permission, 'request': {key: request[key] for key in ['skill','maximum_spend','asset','intent_hash']}}
                compact['permission']['request']['reason']=request['reason'][:200]
                compact['permission_preview']=True
            if len(canonical({'goals': [*goals, compact]})) > 10500:
                break
            goals.append(compact)
        return {'planner_history': True, 'agent_id': self.engine.agent, 'goals': goals,
                'offset': offset, 'next_offset': offset+len(goals), 'has_more': offset+len(goals)<total}

    def validate_grant(self, envelope):
        body = verify_envelope(envelope, self.engine.owner_key, self.engine.crypto)
        required = {'domain','agent_id','grant_id','delegate_key_id','goal','allowed_skills',
                    'maximum_spend','max_steps','expires_at','policy_version','inference_hash'}
        if set(body) != required or body['domain'] != GRANT_DOMAIN or body['agent_id'] != self.engine.agent:
            raise Rejected('INVALID_GOAL_GRANT')
        if 'inference_hash' in body and not re.fullmatch('[a-f0-9]{64}', str(body['inference_hash'])):
            raise Rejected('INVALID_INFERENCE_REVIEW')
        if body['delegate_key_id'] != self.delegate_id:
            raise Rejected('PLANNER_DELEGATE_CHANGED')
        identifier(body['grant_id'])
        if not isinstance(body['goal'], str) or not 1 <= len(body['goal'].strip()) <= 4000:
            raise Rejected('INVALID_PLANNER_PROMPT')
        if len(canonical(body['goal'])) > 6000: raise Rejected('INVALID_PLANNER_PROMPT')
        if type(body['max_steps']) is not int or not 1 <= body['max_steps'] <= 8:
            raise Rejected('INVALID_PLANNER_STEP_LIMIT')
        skills = body['allowed_skills']
        if not isinstance(skills, list) or not skills or len(skills)>32 or len(set(skills))!=len(skills):
            raise Rejected('INVALID_GRANT_SKILLS')
        for name in skills:
            self.engine.registry.get(name)
            if name == 'meta.configure': raise Rejected('PLANNER_CANNOT_CONFIGURE_OWNER_POLICY')
        maximum = amount(body['maximum_spend'])
        if maximum > self.engine.policy.hard_maximum: raise Rejected('HARD_LIMIT_EXCEEDED')
        mode = 'read' if set(skills).issubset(READ_SKILLS) else 'actions'
        if mode == 'read' and maximum != 0: raise Rejected('READ_ONLY_GRANT_MUST_NOT_SPEND')
        now = int(self.clock())
        if type(body['expires_at']) is not int or not now < body['expires_at'] <= now+min(86400,self.engine.policy.approval_ttl):
            raise Rejected('REQUEST_EXPIRED')
        if body['policy_version'] != self.engine.policy.version: raise Rejected('INVALID_GOAL_GRANT')
        return body, mode

    def start(self, envelope) -> dict:
        body, mode = self.validate_grant(envelope)
        digest = hashlib.sha256(canonical(body)).hexdigest()
        goal_id = body['grant_id']
        with self.lock:
            prior = self.db.execute('SELECT grant_hash FROM conversations WHERE id=?', (goal_id,)).fetchone()
            if prior:
                if prior['grant_hash'] != digest: raise Rejected('GRANT_ID_REUSED')
                return self.view(goal_id)  # Never charge again on transport retries.
            from .liveness import is_status_question, status_reply
            if not self.closing and is_status_question(body['goal']):
                if self.db.execute('SELECT COUNT(*) FROM conversations').fetchone()[0]>=1000:raise Rejected('PLANNER_HISTORY_LIMIT')
                now=int(self.clock())
                reply=status_reply(self.service)
                self.db.execute('INSERT INTO conversations(id,grant_hash,prompt,state,reply,created,updated,mode) VALUES (?,?,?,?,?,?,?,?)',
                                (goal_id,digest,body['goal'],'completed',reply,now,now,'read'))
                return self.view(goal_id)
            if self.closing or (self.thread and self.thread.is_alive()): raise Rejected('PLANNER_BUSY')
            if self.db.execute('SELECT COUNT(*) FROM conversations').fetchone()[0] >= 1000:
                raise Rejected('PLANNER_HISTORY_LIMIT')
            config = self.configuration()
            if config is None: raise Rejected('PLANNER_NOT_CONFIGURED')
            if self.requires_configuration(): raise Rejected('MODEL_SETTINGS_REQUIRED')
            if config.endpoint == 'https://api.openai.com/v1' and not config.api_key_file: raise Rejected('INFERENCE_CREDENTIAL_REQUIRED')
            if body.get('inference_hash') != config.configuration_hash:
                raise Rejected('INFERENCE_SETTINGS_CHANGED_REVIEW_AGAIN')
            if config.public_budget()['remaining_micro_usd'] <= 0: raise Rejected('TEST_BUDGET_EXHAUSTED')
            self.engine.register_grant(envelope)
            now = int(self.clock())
            self.db.execute('INSERT INTO conversations(id,grant_hash,prompt,state,created,updated,mode) VALUES (?,?,?,?,?,?,?)',
                            (goal_id,digest,body['goal'],'queued',now,now,mode))
            self.cancel_event.clear();self.active=goal_id
            self.thread=threading.Thread(target=self._run,args=(body,config),name='relay-pi-planner',daemon=True)
            self.thread.start()
        return self.view(goal_id)

    def _set(self, goal_id, **values):
        allowed={'state','reply','error','task_ids'}
        if set(values)-allowed:raise Rejected('INVALID_PLANNER_UPDATE')
        values['updated']=int(self.clock())
        with self.lock:
            self.db.execute('UPDATE conversations SET '+','.join(key+'=?' for key in values)+' WHERE id=?', (*values.values(),goal_id))

    def _authorized_task(self, goal_id, task_id):
        identifier(task_id)
        with self.engine.tx() as db:
            row=db.execute('SELECT grant_id FROM grant_tasks WHERE task_id=?',(task_id,)).fetchone()
        if row is None or row['grant_id']!=goal_id:raise Rejected('PLANNER_TASK_OUTSIDE_GRANT')
        return self.engine.get(task_id)

    def request(self, grant: dict, command: dict) -> dict:
        if self.cancel_event.is_set():raise Rejected('PLANNER_CANCELLED')
        if not isinstance(command,dict) or set(command)!={'method','params'} or not isinstance(command['params'],dict):
            raise Rejected('INVALID_PLANNER_REQUEST')
        method=command['method'];params=command['params'];goal_id=grant['grant_id']
        if method=='request_permission': return self.permissions.propose(grant,params)
        if method=='submit' and set(params)=={'envelope','public_key'}:
            public=unb64(params['public_key'],32)
            if public!=self.public:raise Rejected('PLANNER_DELEGATE_CHANGED')
            body=verify_envelope(params['envelope'],public,self.engine.crypto)
            if body.get('domain')!=DELEGATED_DOMAIN or body.get('grant_id')!=goal_id:
                raise Rejected('PLANNER_TASK_OUTSIDE_GRANT')
            result=self.engine.submit(params['envelope'],public)
            with self.lock:
                row=self._row(goal_id);ids=json.loads(row['task_ids'])
                if result['id'] not in ids:ids.append(result['id'])
                self._set(goal_id,task_ids=json.dumps(ids))
            return result
        if method in ('run','task') and set(params)=={'task_id'}:
            task=self._authorized_task(goal_id,params['task_id'])
            if method=='run' and task['state']=='submitted':
                controller=self.service.get_controller();controller.start();controller.schedule(task['id'])
                end=time.monotonic()+15
                while time.monotonic()<end and not self.cancel_event.wait(.2):
                    task=self._authorized_task(goal_id,task['id'])
                    if task['state'] not in ('submitted','working'):break
            return task
        raise Rejected('PLANNER_METHOD_NOT_ALLOWED')

    def cancel(self, goal_id, envelope):
        self._row(goal_id)
        body=verify_envelope(envelope,self.engine.owner_key,self.engine.crypto)
        if body.get('grant_id')!=goal_id:raise Rejected('PLANNER_GOAL_BINDING_MISMATCH')
        self.engine.revoke_grant(envelope)
        with self.lock:
            if self.active==goal_id:
                self.cancel_event.set()
                if self.process and self.process.poll() is None:self.process.terminate()
            self._set(goal_id,state='cancelled',error='OWNER_CANCELLED')
        return self.view(goal_id)

    def _run(self, grant, config):
        goal_id=grant['grant_id'];child=None;timer=None
        try:
            self._set(goal_id,state='thinking')
            # The child gets this immutable per-goal snapshot, never the mutable
            # operator configuration. It independently checks any credential digest.
            from .inference_settings import private_write
            snapshot = asdict(config)
            if config.api_key_file:
                from .inference_settings import credential_bytes
                if hashlib.sha256(credential_bytes(config.api_key_file)).hexdigest() != config.credential_sha256:
                    raise Rejected('INFERENCE_CREDENTIAL_CHANGED')
            if hashlib.sha256(Path(config.runner).read_bytes()).hexdigest() != config.runner_sha256:
                raise Rejected('PLANNER_RUNNER_CHANGED')
            snapshot_path = self.root / ('run-' + goal_id + '.json')
            private_write(snapshot_path, canonical(snapshot), exclusive=True)
            env={'PATH':'/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin',
                 'HOME':str(self.root),'TMPDIR':str(self.root),
                 'COMMONS_PLANNER_CONFIG':str(snapshot_path)}
            child=subprocess.Popen([config.node,config.runner],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL,env=env,cwd=self.root,start_new_session=True)
            with self.lock:self.process=child
            def expire():
                self.cancel_event.set()
                if child.poll() is None:
                    try:os.killpg(child.pid,signal.SIGTERM)
                    except ProcessLookupError:pass
            timer=threading.Timer(MAX_TURN_SECONDS,expire);timer.daemon=True;timer.start()
            skills=[row for row in self.engine.registry.describe() if row['id'] in grant['allowed_skills']]
            permission_skills=[row for row in self.engine.registry.describe() if row['id'] not in grant['allowed_skills'] and row['id']!='meta.configure']
            initial={'grant':grant,'delegate_key_file':str(self.key_path),'skills':skills,
                     'permission_skills':permission_skills,
                     'maximum_task_ttl':self.engine.policy.approval_ttl,
                     'history':self.history(0)['goals'][1:4],
                     'discovery_topic':'commons',
                     'known_programs':[
                         {'name':'Commons shared group',
                          'program_id':'4865d6e70a30b8f2aa95dd63a866d3c95adffd52ce1e888077d584c3bcb685d6',
                          'account':'eb038d5a8a9e154c9d23ff8f9c48656ac970e061d688d5262d736770020c9ac6',
                          'use':'program.query to read the current shared value, members and threshold'}
                     ]}
            child.stdin.write(canonical({'kind':'start','value':initial})+b'\n');child.stdin.flush()
            finished=False
            for _ in range(100):
                line=child.stdout.readline(MAX_FRAME+2)
                if not line:break
                if len(line)>MAX_FRAME or not line.endswith(b'\n'):raise Rejected('INVALID_PLANNER_FRAME')
                frame=parse(line)
                if self.cancel_event.is_set() and self._row(goal_id)['state'] == 'cancelled':break
                if not isinstance(frame,dict):raise Rejected('INVALID_PLANNER_FRAME')
                if frame.get('kind')=='request' and set(frame)=={'kind','id','command'}:
                    request_id=identifier(frame['id'])
                    try:
                        value=self.request(grant,frame['command'])
                        # Tools receive bounded complete data or a visible omission.
                        if len(canonical(value))>20000:
                            value={k:value[k] for k in ['id','skill','state','maximum_spend','asset'] if k in value}
                            value['result']={'omitted':True,'reason':'Read complete output in task details'}
                        reply={'kind':'reply','id':request_id,'success':True,'result':value}
                    except Rejected as error:reply={'kind':'reply','id':request_id,'success':False,'error':str(error)}
                    child.stdin.write(canonical(reply)+b'\n');child.stdin.flush()
                elif frame.get('kind')=='status':
                    self._set(goal_id,state='working' if frame.get('event')=='tool_task' else 'thinking')
                elif frame.get('kind')=='commentary' and set(frame)=={'kind','text'}:
                    text=frame['text']
                    if not isinstance(text,str) or len(text)>700:raise Rejected('INVALID_PLANNER_COMMENTARY')
                    with self.lock:
                        count=self.db.execute('SELECT COUNT(*) FROM conversation_updates WHERE goal_id=?',(goal_id,)).fetchone()[0]
                        if count<8 and text.strip():
                            self.db.execute('INSERT INTO conversation_updates VALUES (?,?,?,?)',(goal_id,count,text.strip(),int(self.clock())))
                elif frame.get('kind')=='done':
                    text=frame.get('text','')
                    if not isinstance(text,str) or len(text)>7000:raise Rejected('INVALID_PLANNER_RESPONSE')
                    error=frame.get('error')
                    if error is not None and (not isinstance(error,str) or not re.fullmatch(r'[A-Z_0-9]{1,100}',error)):
                        error='MODEL_TRANSPORT_FAILED'
                    with self.lock:
                        if self._row(goal_id)['state'] != 'cancelled':
                            ids=json.loads(self._row(goal_id)['task_ids'] or '[]')
                            generic=(not text) or ('Open Activity' in text) or text.startswith('The agent finished this turn')
                            if ids and generic:
                                summary=describe_tasks([self.engine.get(task_id) for task_id in ids])
                                if summary: text=summary
                            self._set(goal_id,state='failed' if error else ('waiting' if frame.get('waiting') else 'completed'),reply=text,error=error)
                    finished=True;break
                else:raise Rejected('INVALID_PLANNER_FRAME')
            if not finished:raise Rejected('PLANNER_INTERRUPTED')
        except Exception as error:
            code=str(error) if isinstance(error,Rejected) else 'PLANNER_RUN_FAILED'
            with self.lock:
                if self._row(goal_id)['state']!='cancelled':self._set(goal_id,state='failed',error=code)
        finally:
            if timer:timer.cancel()
            if child:
                if child.poll() is None:
                    try:os.killpg(child.pid,signal.SIGTERM)
                    except ProcessLookupError:pass
                    try:child.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=3)
                for stream in [child.stdin,child.stdout]:
                    if stream:stream.close()
            with self.lock:self.process=None;self.active=None

    def close(self):
        self.closing=True;self.cancel_event.set()
        with self.lock:
            child=self.process
            if child and child.poll() is None:child.terminate()
        if self.thread:self.thread.join(timeout=6)
        # Do not close SQLite under an effect worker still unwinding.
        if not self.thread or not self.thread.is_alive():self.db.close()
