"""Durable policy enforcement and task lifecycle.

Only adapter code may perform effects. The database commits 'broadcasting'
before that call; uncertainty keeps the reservation instead of trying twice.
This module has no network client, model client, wallet key, or shell skill.
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path
import sqlite3
import stat
import threading
import time
from typing import Callable, Protocol
import uuid
from .codec import Rejected, amount, canonical, digest, identifier
from .signing import Ed25519, key_id, protected_directory, verify_envelope
from .skills import Registry, Quote, default_registry

REQUEST_DOMAIN='commons/commons_relay/request/v1'
APPROVAL_DOMAIN='commons/commons_relay/approval/v1'
GRANT_DOMAIN='commons/commons_relay/goal-grant/v1'
DELEGATED_DOMAIN='commons/commons_relay/delegated-request/v1'
TERMINAL=frozenset(['completed','failed','rejected','canceled'])

@dataclass(frozen=True)
class Policy:
    per_transaction: int = 100
    per_period: int = 300
    hard_maximum: int = 10000
    period_seconds: int = 86400
    approval_ttl: int = 600
    version: int = 1
    def __post_init__(self):
        for n in [self.per_transaction,self.per_period,self.hard_maximum]:
            if type(n) is not int:raise Rejected('INVALID_POLICY')
            amount(str(n))
        if self.per_transaction>self.hard_maximum or self.per_transaction>self.per_period:
            raise Rejected('INVALID_POLICY_LIMITS')
        for n in [self.period_seconds,self.approval_ttl,self.version]:
            if type(n) is not int or n<1:raise Rejected('INVALID_POLICY')

@dataclass(frozen=True)
class Prepared:
    reference: str
    maximum_spend: int
    opaque_handle: str
    def __post_init__(self):
        identifier(self.reference);identifier(self.opaque_handle)
        if type(self.maximum_spend) is not int:raise Rejected('INVALID_PREPARED_EFFECT')
        amount(str(self.maximum_spend))

@dataclass(frozen=True)
class Receipt:
    reference: str
    state: str
    actual_spend: int = 0
    result: dict | None = None
    def __post_init__(self):
        identifier(self.reference)
        if self.state not in ['confirmed','rejected','pending']:raise Rejected('INVALID_EFFECT_RECEIPT')
        if type(self.actual_spend) is not int:raise Rejected('INVALID_EFFECT_RECEIPT')
        amount(str(self.actual_spend));canonical(self.result)

class Adapter(Protocol):
    def prepare(self,task:dict)->Prepared: ...
    def broadcast(self,effect:Prepared)->None: ...
    def lookup(self,effect:Prepared)->Receipt: ...

class Engine:
    def __init__(self,state_dir:Path,agent_id:str,owner_public_key:bytes,crypto:Ed25519,
                 policy:Policy|None=None,registry:Registry|None=None,clock:Callable[[],float]=time.time,
                 quote_provider:Callable[[str,dict,object],object]|None=None):
        self.root=protected_directory(state_dir);self.agent=identifier(agent_id)
        self.owner=key_id(owner_public_key);self.owner_key=owner_public_key
        self.crypto=crypto;self.policy=policy or Policy();self.registry=registry or default_registry();self.clock=clock;self.quote_provider=quote_provider
        self.guard=threading.RLock();path=self.root/'state.sqlite'
        if path.is_symlink():raise Rejected('SYMLINK_DATABASE')
        if not path.exists():
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
        if not path.is_file() or stat.S_IMODE(path.stat().st_mode)&0o077:raise Rejected('DATABASE_NOT_PRIVATE')
        self.db=sqlite3.connect(path,timeout=10,isolation_level=None,check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA trusted_schema=OFF');self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS grants(id TEXT PRIMARY KEY,body_hash TEXT NOT NULL,
          delegate TEXT NOT NULL,skills TEXT NOT NULL,maximum TEXT NOT NULL,used TEXT NOT NULL,
          max_steps INTEGER NOT NULL,steps INTEGER NOT NULL,expires INTEGER NOT NULL,
          revoked INTEGER NOT NULL DEFAULT 0,goal TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS grant_tasks(task_id TEXT PRIMARY KEY,grant_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks(
          id TEXT PRIMARY KEY, request_id TEXT NOT NULL, requester TEXT NOT NULL,
          skill TEXT NOT NULL, args TEXT NOT NULL, intent TEXT NOT NULL, hash TEXT NOT NULL,
          amount TEXT NOT NULL, asset TEXT NOT NULL, state TEXT NOT NULL, phase TEXT NOT NULL,
          created INTEGER NOT NULL, updated INTEGER NOT NULL, deadline INTEGER NOT NULL,
          authorization TEXT NOT NULL, approval_id TEXT, worker TEXT, lease TEXT,
          heartbeat INTEGER, prepared TEXT, result TEXT, error TEXT,
          UNIQUE(requester,request_id));
        CREATE TABLE IF NOT EXISTS reservations(task_id TEXT PRIMARY KEY REFERENCES tasks(id),
          amount TEXT NOT NULL, asset TEXT NOT NULL, created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS spending(task_id TEXT PRIMARY KEY REFERENCES tasks(id),
          amount TEXT NOT NULL, asset TEXT NOT NULL, settled INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS approvals(nonce TEXT PRIMARY KEY,task_id TEXT NOT NULL,
          body_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT NOT NULL,
          at INTEGER NOT NULL,state TEXT NOT NULL,code TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,
          body TEXT NOT NULL,next_try INTEGER NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
          delivered INTEGER NOT NULL DEFAULT 0);
        ''')
        expected={'schema':'1','agent':self.agent,'owner':self.owner,'policy':canonical(asdict(self.policy)).decode()}
        with self.tx() as db:
            for k,v in expected.items():
                row=db.execute('SELECT value FROM metadata WHERE key=?',(k,)).fetchone()
                if row and row['value']!=v:
                    if k=='policy' and json.loads(row['value']).get('version',0)>self.policy.version:
                        self.policy=Policy(**json.loads(row['value']))
                    else:raise Rejected('STORED_CONFIGURATION_MISMATCH')
                db.execute('INSERT OR IGNORE INTO metadata VALUES (?,?)',(k,v))
    def close(self):
        with self.guard:self.db.close()
    @contextmanager
    def tx(self):
        with self.guard:
            self.db.execute('BEGIN IMMEDIATE')
            try:yield self.db
            except BaseException:self.db.rollback();raise
            else:self.db.commit()
    def now(self,db)->int:
        raw=int(self.clock())
        if raw<0:raise Rejected('INVALID_CLOCK')
        previous=db.execute("SELECT value FROM metadata WHERE key='last_time'").fetchone()
        now=max(raw,int(previous['value']) if previous else 0)
        db.execute("INSERT INTO metadata(key,value) VALUES('last_time',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(now),))
        return now
    def _event(self,db,task,state,code,now):
        db.execute('INSERT INTO events(task_id,at,state,code) VALUES (?,?,?,?)',(task,now,state,code))
    def _fetch(self,db,task):
        row=db.execute('SELECT * FROM tasks WHERE id=?',(task,)).fetchone()
        if not row:raise Rejected('TASK_NOT_FOUND')
        return row
    def _view(self,row,full=False):
        result={'id':row['id'],'skill':row['skill'],'state':row['state'],'phase':row['phase'],
                'maximum_spend':row['amount'],'asset':row['asset'],'intent_hash':row['hash'],
                'created':row['created'],'updated':row['updated'],'deadline':row['deadline'],
                'error':row['error'],'result':json.loads(row['result']) if row['result'] else None}
        if full:result['arguments']=json.loads(row['args'])
        if row['prepared']:result['effect_reference']=json.loads(row['prepared'])['reference']
        return result
    def _expire(self,db,now):
        rows=db.execute("SELECT id FROM tasks WHERE state IN ('submitted','input-required') AND deadline < ?",(now,)).fetchall()
        for row in rows:
            db.execute("UPDATE tasks SET state='rejected',phase='expired',error='AUTHORIZATION_EXPIRED',updated=? WHERE id=?",(now,row['id']))
            db.execute('DELETE FROM reservations WHERE task_id=?',(row['id'],))
            db.execute('UPDATE notifications SET delivered=1 WHERE task_id=?',(row['id'],))
            self._event(db,row['id'],'rejected','AUTHORIZATION_EXPIRED',now)
    def _usage(self,db,asset,now)->int:
        spent=db.execute('SELECT amount FROM spending WHERE asset=? AND settled>?',(asset,now-self.policy.period_seconds)).fetchall()
        pending=db.execute('SELECT amount FROM reservations WHERE asset=?',(asset,)).fetchall()
        # Decimal text avoids SQLite's signed 64-bit SUM overflow.
        return sum(int(r['amount']) for r in spent)+sum(int(r['amount']) for r in pending)
    def usage(self,asset='LEZ-testnet'):
        with self.tx() as db:
            now=self.now(db);self._expire(db,now)
            return self._usage(db,asset,now)
    def register_grant(self,envelope:dict)->dict:
        body=verify_envelope(envelope,self.owner_key,self.crypto)
        required={'domain','agent_id','grant_id','delegate_key_id','goal','allowed_skills','maximum_spend','max_steps','expires_at','policy_version'}
        if set(body) not in (required, required|{'inference_hash'}) or body['domain']!=GRANT_DOMAIN or body['agent_id']!=self.agent or body['policy_version']!=self.policy.version:
            raise Rejected('INVALID_GOAL_GRANT')
        if 'inference_hash' in body and (not isinstance(body['inference_hash'],str) or len(body['inference_hash']) != 64 or any(c not in '0123456789abcdef' for c in body['inference_hash'])):raise Rejected('INVALID_INFERENCE_REVIEW')
        grant_id=identifier(body['grant_id']);delegate=identifier(body['delegate_key_id'])
        if not delegate.startswith('ed25519:') or len(delegate)!=72:raise Rejected('INVALID_DELEGATE_KEY')
        skills=body['allowed_skills']
        if not isinstance(skills,list) or not 1<=len(skills)<=32 or len(set(skills))!=len(skills):raise Rejected('INVALID_GRANT_SKILLS')
        for skill in skills:self.registry.get(skill)
        maximum=amount(body['maximum_spend'])
        if maximum>self.policy.hard_maximum:raise Rejected('GRANT_EXCEEDS_HARD_LIMIT')
        if type(body['max_steps'])is not int or not 1<=body['max_steps']<=100:raise Rejected('INVALID_STEP_BUDGET')
        if not isinstance(body['goal'],str) or not 1<=len(body['goal'])<=8000:raise Rejected('INVALID_GOAL')
        if type(body['expires_at'])is not int:raise Rejected('INVALID_GRANT_EXPIRY')
        with self.tx() as db:
            now=self.now(db)
            if not now<body['expires_at']<=now+86400:raise Rejected('GRANT_EXPIRED')
            old=db.execute('SELECT * FROM grants WHERE id=?',(grant_id,)).fetchone()
            if old:
                if old['body_hash']!=digest(body):raise Rejected('GRANT_ID_ALREADY_USED')
            else:
                db.execute('INSERT INTO grants(id,body_hash,delegate,skills,maximum,used,max_steps,steps,expires,goal) VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (grant_id,digest(body),delegate,canonical(skills).decode(),str(maximum),'0',body['max_steps'],0,body['expires_at'],body['goal']))
        return {'grant_id':grant_id,'allowed_skills':skills,'expires_at':body['expires_at'],'max_steps':body['max_steps']}
    def _consume_grant(self,db,grant_id,requester,skill,maximum,now):
        grant=db.execute('SELECT * FROM grants WHERE id=?',(grant_id,)).fetchone()
        if not grant or grant['revoked'] or grant['expires']<=now:raise Rejected('GOAL_GRANT_NOT_ACTIVE')
        if grant['delegate']!=requester or skill not in json.loads(grant['skills']):raise Rejected('GOAL_SCOPE_VIOLATION')
        if grant['steps']>=grant['max_steps']:raise Rejected('GOAL_STEP_LIMIT')
        used=int(grant['used'])+maximum
        if used>int(grant['maximum']):raise Rejected('GOAL_SPENDING_LIMIT')
        db.execute('UPDATE grants SET used=?,steps=steps+1 WHERE id=?',(str(used),grant_id))
    def _check_task_grant(self,db,task,now):
        row=db.execute('SELECT g.expires,g.revoked FROM grants g JOIN grant_tasks t ON t.grant_id=g.id WHERE t.task_id=?',(task,)).fetchone()
        if row and (row['revoked'] or row['expires']<=now):raise Rejected('GOAL_GRANT_NOT_ACTIVE')
    def revoke_grant(self,envelope:dict)->dict:
        body=verify_envelope(envelope,self.owner_key,self.crypto)
        if set(body)!={'domain','agent_id','grant_id','expires_at'} or body['domain']!='commons/commons_relay/revoke/v1' or body['agent_id']!=self.agent:
            raise Rejected('INVALID_REVOCATION')
        with self.tx() as db:
            now=self.now(db)
            if type(body['expires_at'])is not int or not now<body['expires_at']<=now+self.policy.approval_ttl:raise Rejected('REVOCATION_EXPIRED')
            db.execute('UPDATE grants SET revoked=1 WHERE id=?',(identifier(body['grant_id']),))
            tasks=db.execute("SELECT t.id FROM tasks t JOIN grant_tasks g ON g.task_id=t.id WHERE g.grant_id=? AND t.state IN ('submitted','input-required')",(body['grant_id'],)).fetchall()
            for task in tasks:
                db.execute("UPDATE tasks SET state='canceled',phase='grant-revoked',updated=? WHERE id=?",(now,task['id']))
                db.execute('DELETE FROM reservations WHERE task_id=?',(task['id'],))
                self._event(db,task['id'],'canceled','GOAL_REVOKED',now)
        return {'grant_id':body['grant_id'],'revoked':True}
    def overview(self)->dict:
        with self.tx() as db:
            now=self.now(db);self._expire(db,now)
            rows=db.execute('SELECT * FROM tasks ORDER BY created DESC,rowid DESC LIMIT 100').fetchall()
            return {'agent_id':self.agent,'owner_key_id':self.owner,'policy':asdict(self.policy),'reserved_and_recent_spend':str(self._usage(db,'LEZ-testnet',now)),
                    'tasks':[self._view(r) for r in rows], 'skills':self.registry.describe(),
                    'inference':'disabled unless explicitly connected','state_store':'SQLite WAL'}
    def submit(self,envelope:dict,public_key:bytes|None=None,*,reviewed_intent:dict|None=None)->dict:
        public=public_key if public_key is not None else self.owner_key
        requester=key_id(public);body=verify_envelope(envelope,public,self.crypto)
        fields={'domain','agent_id','request_id','skill','arguments','expires_at'}
        delegated = body.get('domain') == DELEGATED_DOMAIN
        if delegated:fields=fields|{'grant_id'}
        if set(body)!=fields or body['domain'] not in [REQUEST_DOMAIN,DELEGATED_DOMAIN] or body['agent_id']!=self.agent:
            raise Rejected('INVALID_REQUEST_BINDING')
        request_id=identifier(body['request_id']);skill=self.registry.get(body['skill'])
        if requester!=self.owner and not skill.public and not delegated:raise Rejected('SKILL_NOT_AUTHORIZED')
        if skill.name=='meta.configure' and requester!=self.owner:raise Rejected('CONFIGURATION_REQUIRES_OWNER_SIGNATURE')
        if not isinstance(body['arguments'],dict):raise Rejected('INVALID_SKILL_ARGUMENTS')
        quote=skill.validate(body['arguments'])
        if self.quote_provider is not None:quote=self.quote_provider(skill.name,body['arguments'],quote)
        # The trusted quote provider may replace the static zero-cost descriptor
        # for signed peer services, but it must return the same validated type.
        from .skills import Quote
        if not isinstance(quote,Quote):raise Rejected('INVALID_DYNAMIC_QUOTE')
        if quote.maximum>self.policy.hard_maximum:raise Rejected('HARD_LIMIT_EXCEEDED')
        if type(body['expires_at']) is not int:raise Rejected('INVALID_EXPIRY')
        with self.tx() as db:
            now=self.now(db);self._expire(db,now)
            if not now<body['expires_at']<=now+self.policy.approval_ttl:raise Rejected('REQUEST_EXPIRED')
            intent={'agent_id':self.agent,'requester':requester,'skill':skill.name,'arguments':body['arguments'],
                    'asset':quote.asset,'maximum_spend':str(quote.maximum),'policy_version':self.policy.version}
            if delegated:intent['grant_id']=identifier(body['grant_id'])
            if reviewed_intent is not None and intent!=reviewed_intent:
                raise Rejected('REVIEWED_INTENT_CHANGED')
            intent_hash=digest(intent)
            previous=db.execute('SELECT * FROM tasks WHERE requester=? AND request_id=?',(requester,request_id)).fetchone()
            if previous:
                if previous['hash']!=intent_hash:raise Rejected('REQUEST_ID_REUSED_WITH_DIFFERENT_INTENT')
                return self._view(previous)
            if delegated:self._consume_grant(db,body['grant_id'],requester,skill.name,quote.maximum,now)
            active=db.execute("SELECT COUNT(*) FROM tasks WHERE state NOT IN ('completed','failed','rejected','canceled')").fetchone()[0]
            if active>=256:raise Rejected('TOO_MANY_ACTIVE_TASKS')
            auto=(quote.autonomous and quote.maximum<=self.policy.per_transaction and
                  self._usage(db,quote.asset,now)+quote.maximum<=self.policy.per_period)
            task=uuid.uuid4().hex;state='submitted' if auto else 'input-required'
            phase='queued' if auto else 'awaiting-owner'
            db.execute('''INSERT INTO tasks(id,request_id,requester,skill,args,intent,hash,amount,asset,state,phase,
                       created,updated,deadline,authorization) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (task,request_id,requester,skill.name,canonical(body['arguments']).decode(),canonical(intent).decode(),
                        intent_hash,str(quote.maximum),quote.asset,state,phase,now,now,body['expires_at'],'autonomous' if auto else 'none'))
            if delegated:db.execute('INSERT INTO grant_tasks VALUES (?,?)',(task,body['grant_id']))
            if auto:db.execute('INSERT INTO reservations VALUES (?,?,?,?)',(task,str(quote.maximum),quote.asset,now))
            else:
                notice={'kind':'approval-required','agent_id':self.agent,'task_id':task,'intent_hash':intent_hash,
                        'skill':skill.name,'asset':quote.asset,'maximum_spend':str(quote.maximum),'expires_at':body['expires_at']}
                db.execute('INSERT INTO notifications(id,task_id,body,next_try) VALUES (?,?,?,?)',
                           (uuid.uuid4().hex,task,canonical(notice).decode(),now))
            self._event(db,task,state,'ACCEPTED' if auto else 'OWNER_APPROVAL_REQUIRED',now)
            return self._view(self._fetch(db,task))
    def get(self,task_id:str,requester:str|None=None)->dict:
        with self.tx() as db:
            now=self.now(db);self._expire(db,now);row=self._fetch(db,task_id)
            if requester is not None and requester not in [self.owner,row['requester']]:raise Rejected('TASK_NOT_AUTHORIZED')
            return self._view(row,full=requester in [None,self.owner])
    def approval_body(self,task_id:str,nonce:str,expires_at:int)->dict:
        task=self.get(task_id)
        return {'domain':APPROVAL_DOMAIN,'agent_id':self.agent,'task_id':task_id,'intent_hash':task['intent_hash'],
                'approval_id':identifier(nonce),'decision':'approve','expires_at':expires_at,'policy_version':self.policy.version}
    def accept_service_task(self,requester:str,request_id:str,skill_name:str,arguments:dict,exported:set[str],expires:int)->dict:
        """Internal authenticated A2A boundary. Only owner-exported zero-spend
        services are accepted here; this is not exposed as an unsigned CLI method.
        """
        requester=identifier(requester);request_id=identifier(request_id)
        skill=self.registry.get(skill_name)
        if skill_name not in exported or skill_name in ['wallet.send','program.call','program.deploy','meta.configure']:raise Rejected('REMOTE_SKILL_NOT_EXPORTED')
        quote=skill.validate(arguments)
        if quote.maximum!=0:raise Rejected('REMOTE_SERVICE_CANNOT_SPEND_OWNER_FUNDS')
        with self.tx() as db:
            now=self.now(db)
            if type(expires)is not int or not now<expires<=now+86400:raise Rejected('REMOTE_TASK_EXPIRED')
            intent={'agent_id':self.agent,'requester':requester,'skill':skill_name,'arguments':arguments,'maximum_spend':'0','asset':quote.asset,'policy_version':self.policy.version,'authority':'owner-configured-service'}
            fingerprint=digest(intent)
            old=db.execute('SELECT * FROM tasks WHERE requester=? AND request_id=?',(requester,request_id)).fetchone()
            if old:
                if old['hash']!=fingerprint:raise Rejected('REMOTE_REQUEST_ID_REUSED')
                return self._view(old)
            active=db.execute("SELECT COUNT(*) FROM tasks WHERE state NOT IN ('completed','failed','rejected','canceled')").fetchone()[0]
            if active>=256:raise Rejected('TOO_MANY_ACTIVE_TASKS')
            task=uuid.uuid4().hex
            db.execute('''INSERT INTO tasks(id,request_id,requester,skill,args,intent,hash,amount,asset,state,phase,created,updated,deadline,authorization) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (task,request_id,requester,skill.name,canonical(arguments).decode(),canonical(intent).decode(),fingerprint,'0',quote.asset,'submitted','queued',now,now,expires,'service-export'))
            db.execute('INSERT INTO reservations VALUES (?,?,?,?)',(task,'0',quote.asset,now));self._event(db,task,'submitted','SERVICE_ACCEPTED',now)
            return self._view(db.execute('SELECT * FROM tasks WHERE id=?',(task,)).fetchone())
    def approve(self,envelope:dict)->dict:
        body=verify_envelope(envelope,self.owner_key,self.crypto)
        fields={'domain','agent_id','task_id','intent_hash','approval_id','decision','expires_at','policy_version'}
        if set(body)!=fields or body['domain']!=APPROVAL_DOMAIN or body['agent_id']!=self.agent or body['policy_version']!=self.policy.version:
            raise Rejected('INVALID_APPROVAL_BINDING')
        if body['decision'] not in ['approve','reject'] or type(body['expires_at']) is not int:
            raise Rejected('INVALID_APPROVAL')
        nonce=identifier(body['approval_id']);task_id=identifier(body['task_id'])
        with self.tx() as db:
            now=self.now(db);self._expire(db,now);row=self._fetch(db,task_id)
            if row['hash']!=body['intent_hash']:raise Rejected('APPROVAL_INTENT_MISMATCH')
            if not now<body['expires_at']<=row['deadline']:raise Rejected('APPROVAL_EXPIRED')
            previous=db.execute('SELECT * FROM approvals WHERE nonce=?',(nonce,)).fetchone()
            if previous:
                if previous['task_id']==task_id and previous['body_hash']==digest(body):return self._view(row)
                raise Rejected('APPROVAL_NONCE_REUSED')
            if row['state']!='input-required':raise Rejected('TASK_NOT_AWAITING_APPROVAL')
            db.execute('INSERT INTO approvals VALUES (?,?,?)',(nonce,task_id,digest(body)))
            if body['decision']=='approve':
                db.execute('INSERT INTO reservations VALUES (?,?,?,?)',(task_id,row['amount'],row['asset'],now))
                db.execute("UPDATE tasks SET state='submitted',phase='queued',authorization='owner',approval_id=?,deadline=?,updated=? WHERE id=?",
                           (nonce,body['expires_at'],now,task_id))
                state='submitted';code='OWNER_APPROVED'
            else:
                db.execute("UPDATE tasks SET state='rejected',phase='rejected',error='OWNER_REJECTED',updated=? WHERE id=?",(now,task_id))
                state='rejected';code='OWNER_REJECTED'
            db.execute('UPDATE notifications SET delivered=1 WHERE task_id=?',(task_id,))
            self._event(db,task_id,state,code,now);return self._view(self._fetch(db,task_id))
    def start(self,task_id:str,worker:str)->tuple[dict,str]:
        identifier(worker)
        with self.tx() as db:
            now=self.now(db);self._expire(db,now);row=self._fetch(db,task_id)
            if row['state']!='submitted' or row['phase']!='queued':raise Rejected('TASK_NOT_EXECUTABLE')
            self._check_task_grant(db,task_id,now)
            if not db.execute('SELECT 1 FROM reservations WHERE task_id=?',(task_id,)).fetchone():raise Rejected('RESERVATION_MISSING')
            lease=uuid.uuid4().hex
            db.execute("UPDATE tasks SET state='working',phase='preparing',worker=?,lease=?,heartbeat=?,updated=? WHERE id=?",(worker,lease,now,now,task_id))
            self._event(db,task_id,'working','PREPARING',now)
            return self._view(self._fetch(db,task_id),True),lease
    def _leased(self,db,task,lease):
        row=self._fetch(db,task)
        if row['lease']!=lease or row['state']!='working':raise Rejected('INVALID_WORKER_LEASE')
        return row
    def record_prepared(self,task:str,lease:str,effect:Prepared)->None:
        with self.tx() as db:
            now=self.now(db);row=self._leased(db,task,lease)
            if row['phase']!='preparing' or effect.maximum_spend>int(row['amount']):raise Rejected('PREPARED_EFFECT_EXCEEDS_AUTHORIZATION')
            db.execute("UPDATE tasks SET prepared=?,phase='prepared',heartbeat=?,updated=? WHERE id=?",
                       (canonical(asdict(effect)).decode(),now,now,task))
            self._event(db,task,'working','PREPARED',now)
    def mark_broadcasting(self,task:str,lease:str):
        with self.tx() as db:
            now=self.now(db);row=self._leased(db,task,lease)
            if row['phase']!='prepared' or not row['prepared']:raise Rejected('EFFECT_NOT_PREPARED')
            if now>=row['deadline']:raise Rejected('AUTHORIZATION_EXPIRED')
            self._check_task_grant(db,task,now)
            db.execute("UPDATE tasks SET phase='broadcasting',heartbeat=?,updated=? WHERE id=?",(now,now,task))
            self._event(db,task,'working','BROADCASTING',now)
    def fail_before_broadcast(self,task:str,lease:str):
        with self.tx() as db:
            now=self.now(db);row=self._leased(db,task,lease)
            if row['phase'] not in ['preparing','prepared']:raise Rejected('BROADCAST_MAY_HAVE_OCCURRED')
            db.execute("UPDATE tasks SET state='failed',phase='preparation-failed',error='PREPARATION_FAILED',updated=? WHERE id=?",(now,task))
            db.execute('DELETE FROM reservations WHERE task_id=?',(task,));self._event(db,task,'failed','PREPARATION_FAILED',now)
    def uncertain(self,task:str,lease:str):
        with self.tx() as db:
            now=self.now(db);row=self._leased(db,task,lease)
            db.execute("UPDATE tasks SET state='unknown',phase='needs-reconciliation',error='EFFECT_STATUS_UNKNOWN',updated=? WHERE id=?",(now,task))
            self._event(db,task,'unknown','RECONCILIATION_REQUIRED',now)
    def settle(self,task:str,receipt:Receipt):
        with self.tx() as db:
            now=self.now(db);row=self._fetch(db,task)
            if not row['prepared'] or json.loads(row['prepared'])['reference']!=receipt.reference:
                raise Rejected('RECEIPT_REFERENCE_MISMATCH')
            if row['state'] in TERMINAL:return self._view(row)
            if row['state'] not in ['working','unknown'] or row['phase'] not in ['broadcasting','needs-reconciliation']:
                raise Rejected('EFFECT_NOT_BROADCAST')
            if receipt.state=='pending':
                db.execute("UPDATE tasks SET state='unknown',phase='needs-reconciliation',updated=? WHERE id=?",(now,task))
            else:
                if receipt.actual_spend>int(row['amount']):raise Rejected('CONFIRMED_SPEND_EXCEEDS_AUTHORIZATION')
                if receipt.state=='confirmed':
                    db.execute('INSERT INTO spending VALUES (?,?,?,?)',(task,str(receipt.actual_spend),row['asset'],now))
                    state='completed';code='CONFIRMED'
                else:
                    if receipt.actual_spend!=0:raise Rejected('REJECTED_RECEIPT_HAS_SPENDING')
                    state='failed';code='NETWORK_REJECTED'
                db.execute('DELETE FROM reservations WHERE task_id=?',(task,))
                db.execute('UPDATE tasks SET state=?,phase=?,result=?,updated=?,error=? WHERE id=?',
                           (state,'settled',canonical(receipt.result).decode(),now,None if state=='completed' else code,task))
                self._event(db,task,state,code,now)
            return self._view(self._fetch(db,task))
    def execute(self,task_id:str,adapter:Adapter,worker='local-worker')->dict:
        task,lease=self.start(task_id,worker)
        try:
            effect=adapter.prepare(task)
            self.record_prepared(task_id,lease,effect)
        except Exception:
            self.fail_before_broadcast(task_id,lease)
            return self.get(task_id)
        try:
            self.mark_broadcasting(task_id,lease)
        except Rejected:
            self.fail_before_broadcast(task_id,lease)
            return self.get(task_id)
        try:
            adapter.broadcast(effect)
            return self.settle(task_id,adapter.lookup(effect))
        except Exception:
            self.uncertain(task_id,lease)
            return self.get(task_id)
    def reconcile(self,task_id:str,adapter:Adapter)->dict:
        with self.tx() as db:
            row=self._fetch(db,task_id)
            if row['state']!='unknown' or not row['prepared']:raise Rejected('TASK_NOT_RECONCILABLE')
            effect=Prepared(**json.loads(row['prepared']))
        return self.settle(task_id,adapter.lookup(effect))
    def cancel(self,task_id:str,requester:str)->dict:
        with self.tx() as db:
            now=self.now(db);row=self._fetch(db,task_id)
            if requester not in [self.owner,row['requester']]:raise Rejected('TASK_NOT_AUTHORIZED')
            if row['state']=='canceled':return self._view(row)
            if row['state'] not in ['submitted','input-required']:raise Rejected('TASK_NOT_CANCELABLE')
            db.execute("UPDATE tasks SET state='canceled',phase='canceled',updated=? WHERE id=?",(now,task_id))
            db.execute('DELETE FROM reservations WHERE task_id=?',(task_id,));db.execute('UPDATE notifications SET delivered=1 WHERE task_id=?',(task_id,))
            self._event(db,task_id,'canceled','OWNER_OR_REQUESTER_CANCELED',now);return self._view(self._fetch(db,task_id))
    def recover_stale(self,maximum_silence=600)->int:
        if type(maximum_silence) is not int or maximum_silence<1:raise Rejected('INVALID_RECOVERY_WINDOW')
        with self.tx() as db:
            now=self.now(db);rows=db.execute("SELECT id FROM tasks WHERE state='working' AND heartbeat<?",(now-maximum_silence,)).fetchall()
            for row in rows:
                db.execute("UPDATE tasks SET state='unknown',phase='needs-reconciliation',updated=?,error='STALE_WORKER' WHERE id=?",(now,row['id']))
                self._event(db,row['id'],'unknown','STALE_WORKER',now)
            return len(rows)
    def update_policy_from_task(self,task_id:str,policy:Policy,config:dict)->dict:
        with self.tx() as db:
            now=self.now(db);task=db.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
            if not task or task['skill']!='meta.configure' or task['requester']!=self.owner or task['phase']!='broadcasting':raise Rejected('CONFIGURATION_TASK_NOT_AUTHORIZED')
            key='configuration-task:'+task_id
            old=db.execute('SELECT value FROM metadata WHERE key=?',(key,)).fetchone()
            if old:return json.loads(old['value'])
            active=db.execute("SELECT COUNT(*) FROM tasks WHERE id!=? AND state='working'",(task_id,)).fetchone()[0]
            if active:raise Rejected('CONFIGURATION_WAITS_FOR_ACTIVE_EFFECTS')
            if policy.version!=self.policy.version+1:raise Rejected('CONFIGURATION_VERSION_MISMATCH')
            for row in db.execute("SELECT id FROM tasks WHERE id!=? AND state IN ('submitted','input-required')",(task_id,)).fetchall():
                db.execute("UPDATE tasks SET state='canceled',phase='policy-changed',updated=? WHERE id=?",(now,row['id']))
                db.execute('DELETE FROM reservations WHERE task_id=?',(row['id'],));self._event(db,row['id'],'canceled','POLICY_CHANGED',now)
            db.execute("UPDATE metadata SET value=? WHERE key='policy'",(canonical(asdict(policy)).decode(),))
            for name in ['name','owner_address']:
                if name in config:db.execute('INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('runtime:'+name,json.dumps(config[name])))
            result={'policy':asdict(policy),'applied':config,'pending_old_policy_tasks_canceled':True}
            db.execute('INSERT INTO metadata VALUES (?,?)',(key,canonical(result).decode()))
            self.policy=policy
            return result
    def notices(self)->list[dict]:
        with self.tx() as db:
            now=self.now(db);self._expire(db,now)
            rows=db.execute('SELECT * FROM notifications WHERE delivered=0 AND next_try<=? ORDER BY next_try LIMIT 10',(now,)).fetchall()
            result=[]
            for row in rows:
                attempt=row['attempts']+1;delay=min(300,2**min(attempt,8))
                db.execute('UPDATE notifications SET attempts=?,next_try=? WHERE id=?',(attempt,now+delay,row['id']))
                result.append({'id':row['id'],'body':json.loads(row['body']),'attempt':attempt})
            return result
    def notice_delivered(self,notice_id:str):
        with self.tx() as db:db.execute('UPDATE notifications SET delivered=1 WHERE id=?',(notice_id,))
    def events(self,task_id:str,requester:str,cursor=0,limit=100)->list[dict]:
        if type(cursor)is not int or cursor<0 or type(limit)is not int or not 1<=limit<=100:raise Rejected('INVALID_EVENT_CURSOR')
        self.get(task_id,requester)
        with self.guard:
            return [dict(r) for r in self.db.execute('SELECT seq,task_id,at,state,code FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?',(task_id,cursor,limit))]
