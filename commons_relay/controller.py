"""Durable owner channel with isolated observation, I/O and wallet lanes.

The loop performs local I/O and signature checks, never model inference. It
continues delivering owner notices while the effect worker is proving a payment.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import threading
import time
from .codec import Rejected,canonical,parse,digest,identifier
from .signing import verify_envelope
from .engine import TERMINAL

OBSERVATION_SKILLS = ('meta.skills','agent.card','agent.discover','agent.ping','storage.list','messaging.inbox')
IO_SKILLS = ('storage.upload','storage.download','storage.share','messaging.send','messaging.join','messaging.create_group','agent.subscribe','agent.cancel')

OWNER_DOMAIN='commons/relay/owner-command/v1'
OWNER_METHODS=frozenset(['status','skills','task','submit','approve','grant','revoke','cancel','configure',
                         'provider.status','provider.configure','provider.directory','owner.ping','owner.snapshot','owner.skills','owner.skill','owner.task',
                         'planner.status','planner.history','planner.goal','planner.start','planner.cancel','planner.configure','planner.permission','planner.permission_view'])

class Controller:
    def __init__(self,service,owner_address:str):
        self.service=service;self.engine=service.engine;self.runtime=service.get_messaging();self.mailbox=self.runtime.mailbox
        self.owner_address=identifier(owner_address);self.mailbox.get_contact(owner_address)
        self.stop_event=threading.Event();self.thread=None;self.worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix='relay-effects')
        self.future=None;self.active=None;self.last_error=None;self.protocol=None;self.last_heartbeat=0;self.last_tick=None
        self.read_worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix='relay-observations');self.read_future=None;self.read_active=None
        self.io_worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix='relay-io');self.io_future=None;self.io_active=None
        self.reconcile_after={}
        with self.engine.tx() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS scheduled_tasks(task_id TEXT PRIMARY KEY,created INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS owner_commands(id TEXT PRIMARY KEY,body_hash TEXT NOT NULL,response TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS control_cursor(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS owner_task_events(seq INTEGER PRIMARY KEY);
              CREATE TABLE IF NOT EXISTS inbound_failures(message_id TEXT PRIMARY KEY,kind TEXT NOT NULL,cursor INTEGER NOT NULL,code TEXT NOT NULL,created INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS control_failures(stage TEXT PRIMARY KEY,code TEXT NOT NULL,count INTEGER NOT NULL,last_seen INTEGER NOT NULL);
            ''')
    def schedule(self,task_id):
        task=self.engine.get(task_id)
        if task['state']=='submitted':
            with self.engine.tx() as db:db.execute('INSERT OR IGNORE INTO scheduled_tasks VALUES (?,?)',(task_id,int(time.time())))
        return task
    def resume_recovered(self,recovery):
        if not isinstance(recovery,dict) or set(recovery)!={'resumed','reconcile','failed'}:raise Rejected('INVALID_RECOVERY_SET')
        pending=[]
        for task_id in [*recovery['resumed'],*recovery['reconcile']]:
            task=self.engine.get(task_id)
            expected='submitted' if task_id in recovery['resumed'] else 'unknown'
            if task['state']!=expected:raise Rejected('RECOVERY_STATE_CHANGED')
            with self.engine.tx() as db:db.execute('INSERT OR IGNORE INTO scheduled_tasks VALUES (?,?)',(task_id,int(time.time())))
            pending.append(task_id)
        return {'resumed':len(recovery['resumed']),'reconciliation_required':len(recovery['reconcile']),
                'failed':len(recovery['failed']),'scheduled':pending}
    def handle_owner(self,message):
        if message['kind']!='owner-command' or message['sender']!=self.owner_address:raise Rejected('OWNER_CHANNEL_SENDER_MISMATCH')
        body=verify_envelope(message['payload'],self.engine.owner_key,self.engine.crypto)
        if set(body)!={'domain','agent_id','request_id','command','expires_at'} or body['domain']!=OWNER_DOMAIN or body['agent_id']!=self.engine.agent:raise Rejected('OWNER_COMMAND_BINDING_MISMATCH')
        rid=identifier(body['request_id'])
        if type(body['expires_at'])is not int or not int(self.engine.clock())<body['expires_at']<=int(self.engine.clock())+self.engine.policy.approval_ttl:raise Rejected('OWNER_COMMAND_EXPIRED')
        command=body['command']
        if not isinstance(command,dict) or set(command)!={'method','params'} or command['method'] not in OWNER_METHODS or not isinstance(command['params'],dict):raise Rejected('OWNER_METHOD_DENIED')
        fingerprint=digest(body)
        with self.engine.tx() as db:old=db.execute('SELECT * FROM owner_commands WHERE id=?',(rid,)).fetchone()
        if old:
            if old['body_hash']!=fingerprint:raise Rejected('OWNER_COMMAND_ID_REUSED')
            result=json.loads(old['response'])
        else:
            try:
                value=self.service.handle({'id':'owner-'+rid,'method':command['method'],'params':command['params']})
                if command['method'] in ['submit','approve'] and isinstance(value,dict) and value.get('state')=='submitted':self.schedule(value['id'])
                result={'command_id':rid,'success':True,'result':value}
            except Rejected as error:result={'command_id':rid,'success':False,'error':str(error)}
            except Exception as error:
                # A verified owner must receive an explicit uncertain outcome,
                # not wait forever or be encouraged to repeat a side effect.
                trace=error.__traceback__
                while trace and trace.tb_next:trace=trace.tb_next
                function=trace.tb_frame.f_code.co_name if trace else 'unknown'
                line=trace.tb_lineno if trace else 0
                with self.engine.tx() as db:
                    db.execute('CREATE TABLE IF NOT EXISTS owner_command_failures(id TEXT PRIMARY KEY,method TEXT NOT NULL,exception_kind TEXT NOT NULL,function TEXT NOT NULL,line INTEGER NOT NULL)')
                    db.execute('INSERT OR IGNORE INTO owner_command_failures VALUES (?,?,?,?,?)',(rid,command['method'],type(error).__name__,function,line))
                result={'command_id':rid,'success':False,'error':'OWNER_COMMAND_OUTCOME_UNKNOWN'}
            encoded=canonical(result)
            if len(encoded)>14000:result={'command_id':rid,'success':False,'error':'OWNER_RESULT_TOO_LARGE'}
            with self.engine.tx() as db:db.execute('INSERT OR IGNORE INTO owner_commands VALUES (?,?,?)',(rid,fingerprint,canonical(result).decode()))
        self.mailbox.enqueue(self.owner_address,'owner-result',result,message_id='owner-'+rid,ttl=20 if command['method']=='owner.ping' else 86400)
        return result
    def _record_stage_failure(self,stage,code):
        self.last_error=code
        now=int(self.engine.clock())
        with self.engine.tx() as db:
            db.execute("INSERT INTO control_failures(stage,code,count,last_seen) VALUES (?,?,1,?) ON CONFLICT(stage) DO UPDATE SET code=excluded.code,count=count+1,last_seen=excluded.last_seen",(stage,code,now))
    def process_inbox(self):
        with self.engine.tx() as db:row=db.execute("SELECT value FROM control_cursor WHERE key='inbox'").fetchone()
        cursor=row[0] if row else 0
        for message in self.mailbox.messages(cursor,limit=20):
            try:
                if message['kind']=='owner-command':self.handle_owner(message)
                elif self.protocol and message['kind'] in ['a2a-request','a2a-response','a2a-event','agent-card']:self.protocol.handle_message(message)
            except Rejected as error:
                self.last_error=str(error)
            except Exception:
                # One bad remote/background message must never poison the cursor
                # and freeze unrelated owner tasks forever.
                self.last_error='INBOUND_MESSAGE_FAILED'
                with self.engine.tx() as db:
                    db.execute('INSERT OR IGNORE INTO inbound_failures VALUES (?,?,?,?,?)',(str(message.get('id','unknown'))[:256],str(message.get('kind','unknown'))[:64],int(message['cursor']),'INBOUND_MESSAGE_FAILED',int(self.engine.clock())))
            # Every message is either durably handled, rejected, or quarantined.
            # Advance so a malformed peer message cannot head-of-line block chat.
            with self.engine.tx() as db:db.execute("INSERT INTO control_cursor VALUES ('inbox',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(message['cursor'],))
    def notices(self):
        for notice in self.engine.notices():
            body=notice['body']
            self.mailbox.enqueue(self.owner_address,'owner-result',{'notice':body},message_id='notice-'+notice['id'],ttl=max(1,min(86400,body['expires_at']-int(self.engine.clock()))))
            if self.mailbox.status('notice-'+notice['id'])['state']=='acknowledged':self.engine.notice_delivered(notice['id'])
        with self.engine.tx() as db:
            rows=db.execute('SELECT e.* FROM events e JOIN scheduled_tasks s ON s.task_id=e.task_id LEFT JOIN owner_task_events d ON d.seq=e.seq WHERE d.seq IS NULL ORDER BY e.seq LIMIT 10').fetchall()
        for row in rows:
            # Notifications are invalidations, not a second result transport.
            # Nested Agent Cards can fit as results but exceed the signed message
            # nesting limit after being wrapped again. Fetch details on demand.
            message_id='task-event-'+str(row['seq'])
            try:previous=self.mailbox.status(message_id)
            except Rejected as error:
                if str(error)!='MESSAGE_NOT_FOUND':raise
                value={'task_update':{'task_id':row['task_id'],'state':row['state'],'code':row['code'],'sequence':row['seq']},
                       'result_available_through':'task'}
                self.mailbox.enqueue(self.owner_address,'owner-result',value,message_id=message_id,ttl=86400)
            else:
                if previous['recipient']!=self.owner_address or previous['kind']!='owner-result':
                    raise Rejected('OWNER_EVENT_MESSAGE_BINDING_MISMATCH')
            # A crash after enqueue must reuse the already durable notification,
            # not reconstruct a different payload under the same message ID.
            with self.engine.tx() as db:db.execute('INSERT OR IGNORE INTO owner_task_events VALUES (?)',(row['seq'],))
    def _execute(self,id):
        task=self.engine.get(id);adapter=self.service.adapter_for(task['skill'])
        return self.engine.resume_prepared(id,adapter,worker='controller') if task['state']=='submitted' and task['phase']=='prepared' else self.engine.execute(id,adapter,worker='controller')
    def _next_for_lane(self, lane):
        # Only trusted zero-spend built-ins enter the independent lanes. Unknown
        # extensions, owner configuration and every wallet operation stay serial.
        read_slots=','.join('?' for _ in OBSERVATION_SKILLS)
        io_slots=','.join('?' for _ in IO_SKILLS)
        assignment=("CASE WHEN t.amount='0' AND t.skill IN ("+read_slots+") THEN 'read' "
                    "WHEN t.amount='0' AND t.skill IN ("+io_slots+") THEN 'io' ELSE 'effects' END")
        query=("SELECT t.id,t.state,t.skill,t.amount AS maximum_spend FROM tasks t "
               "JOIN scheduled_tasks s ON s.task_id=t.id WHERE t.state IN ('submitted','unknown') "
               "AND ("+assignment+")=? ORDER BY CASE t.state WHEN 'submitted' THEN 0 ELSE 1 END,t.updated,t.created LIMIT 512")
        with self.engine.tx() as db:
            rows=db.execute(query,(*OBSERVATION_SKILLS,*IO_SKILLS,lane)).fetchall()
        now=time.monotonic()
        candidates=[]
        for row in rows:
            target='effects'
            if row['maximum_spend']=='0':
                if row['skill'] in OBSERVATION_SKILLS:target='read'
                elif row['skill'] in IO_SKILLS:target='io'
            if target!=lane:continue
            if row['state']=='unknown' and self.reconcile_after.get(row['id'],0)>now:continue
            candidates.append(row)
        # New independent work is not starved by an unresolved historical receipt.
        return next((r for r in candidates if r['state']=='submitted'),candidates[0] if candidates else None)

    def _work_lane(self,lane):
        prefix='' if lane=='effects' else lane+'_'
        future=getattr(self,prefix+'future');active=getattr(self,prefix+'active')
        if future and future.done():
            try:future.result()
            except Exception:self.last_error=lane.upper()+'_WORKER_FAILED'
            if active:self.reconcile_after[active]=time.monotonic()+10
            setattr(self,prefix+'future',None);setattr(self,prefix+'active',None)
            future=None
        if future:
            with self.engine.tx() as db:db.execute("UPDATE tasks SET heartbeat=? WHERE id=? AND worker='controller' AND state='working'",(int(self.engine.clock()),active))
            return
        row=self._next_for_lane(lane)
        if not row:return
        worker=self.worker if lane=='effects' else getattr(self,lane+'_worker')
        setattr(self,prefix+'active',row['id'])
        if row['state']=='submitted':future=worker.submit(self._execute,row['id'])
        else:future=worker.submit(self.engine.reconcile,row['id'],self.service.adapter_for(row['skill']))
        setattr(self,prefix+'future',future)
        # Keep the throttle map bounded without forgetting live unresolved tasks.
        if len(self.reconcile_after)>1024:
            with self.engine.tx() as db:live={r[0] for r in db.execute("SELECT id FROM tasks WHERE state='unknown'")}
            self.reconcile_after={k:v for k,v in self.reconcile_after.items() if k in live}

    def read_work(self):self._work_lane('read')
    def io_work(self):self._work_lane('io')
    def work(self):self._work_lane('effects')
    def tick(self):
        self.last_tick=time.monotonic();failed=False
        stages=[('inbox',self.process_inbox),('notices',self.notices),('observations',self.read_work),('io',self.io_work),('effects',self.work)]
        if self.protocol:stages.append(('protocol',self.protocol.tick))
        for stage,call in stages:
            try:call()
            except Exception:
                failed=True;self._record_stage_failure(stage,stage.upper()+'_TICK_FAILED')
        if not failed:self.last_error=None
    def start(self):
        if self.thread:return
        self.runtime.start()
        def loop():
            while not self.stop_event.is_set():
                try:self.tick()
                except Exception:self.last_error='CONTROL_TICK_FAILED'
                self.stop_event.wait(1)
        self.thread=threading.Thread(target=loop,name='relay-control-io',daemon=True);self.thread.start()
    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=3)
        self.worker.shutdown(wait=False,cancel_futures=True)
        self.read_worker.shutdown(wait=False,cancel_futures=True)
        self.io_worker.shutdown(wait=False,cancel_futures=True)
