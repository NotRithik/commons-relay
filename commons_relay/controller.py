"""Durable owner channel and single-writer task scheduling.

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

OWNER_DOMAIN='commons/relay/owner-command/v1'
OWNER_METHODS=frozenset(['status','skills','task','submit','approve','grant','revoke','cancel','configure',
                         'owner.snapshot','owner.skills','owner.skill','owner.task',
                         'planner.status','planner.history','planner.goal','planner.start','planner.cancel'])

class Controller:
    def __init__(self,service,owner_address:str):
        self.service=service;self.engine=service.engine;self.runtime=service.get_messaging();self.mailbox=self.runtime.mailbox
        self.owner_address=identifier(owner_address);self.mailbox.get_contact(owner_address)
        self.stop_event=threading.Event();self.thread=None;self.worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix='relay-effects')
        self.future=None;self.active=None;self.last_error=None;self.protocol=None;self.last_heartbeat=0
        with self.engine.tx() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS scheduled_tasks(task_id TEXT PRIMARY KEY,created INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS owner_commands(id TEXT PRIMARY KEY,body_hash TEXT NOT NULL,response TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS control_cursor(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS owner_task_events(seq INTEGER PRIMARY KEY);
            ''')
    def schedule(self,task_id):
        task=self.engine.get(task_id)
        if task['state']=='submitted':
            with self.engine.tx() as db:db.execute('INSERT OR IGNORE INTO scheduled_tasks VALUES (?,?)',(task_id,int(time.time())))
        return task
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
            encoded=canonical(result)
            if len(encoded)>14000:result={'command_id':rid,'success':False,'error':'OWNER_RESULT_TOO_LARGE'}
            with self.engine.tx() as db:db.execute('INSERT OR IGNORE INTO owner_commands VALUES (?,?,?)',(rid,fingerprint,canonical(result).decode()))
        self.mailbox.enqueue(self.owner_address,'owner-result',result,message_id='owner-'+rid,ttl=86400)
        return result
    def process_inbox(self):
        with self.engine.tx() as db:row=db.execute("SELECT value FROM control_cursor WHERE key='inbox'").fetchone()
        cursor=row[0] if row else 0
        for message in self.mailbox.messages(cursor,limit=20):
            try:
                if message['kind']=='owner-command':self.handle_owner(message)
                elif self.protocol and message['kind'] in ['a2a-request','a2a-response','a2a-event','agent-card']:self.protocol.handle_message(message)
            except Rejected as error:self.last_error=str(error)
            else:pass
            # Rejections are final for this signed message; a requester can send
            # a new corrected request. Success/replay state is durable above.
            with self.engine.tx() as db:db.execute("INSERT INTO control_cursor VALUES ('inbox',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(message['cursor'],))
    def notices(self):
        for notice in self.engine.notices():
            body=notice['body']
            self.mailbox.enqueue(self.owner_address,'owner-result',{'notice':body},message_id='notice-'+notice['id'],ttl=max(1,min(86400,body['expires_at']-int(self.engine.clock()))))
            if self.mailbox.status('notice-'+notice['id'])['state']=='acknowledged':self.engine.notice_delivered(notice['id'])
        with self.engine.tx() as db:
            rows=db.execute('SELECT e.* FROM events e JOIN scheduled_tasks s ON s.task_id=e.task_id LEFT JOIN owner_task_events d ON d.seq=e.seq WHERE d.seq IS NULL ORDER BY e.seq LIMIT 10').fetchall()
        for row in rows:
            value={'task_update':{'task_id':row['task_id'],'state':row['state'],'code':row['code'],'sequence':row['seq']}}
            if row['state'] in TERMINAL:value['task']=self.engine.get(row['task_id'])
            if len(canonical(value))>14000:value={'task_update':value['task_update'],'result_available_through':'task'}
            self.mailbox.enqueue(self.owner_address,'owner-result',value,message_id='task-event-'+str(row['seq']),ttl=86400)
            with self.engine.tx() as db:db.execute('INSERT OR IGNORE INTO owner_task_events VALUES (?)',(row['seq'],))
    def _execute(self,id):
        task=self.engine.get(id);return self.engine.execute(id,self.service.adapter_for(task['skill']),worker='controller')
    def work(self):
        if self.future and self.future.done():
            try:self.future.result()
            except Exception:self.last_error='TASK_WORKER_FAILED'
            self.future=None;self.active=None
        if self.future:
            if time.monotonic()-self.last_heartbeat>5:
                with self.engine.tx() as db:db.execute("UPDATE tasks SET heartbeat=? WHERE id=? AND worker='controller' AND state='working'",(int(self.engine.clock()),self.active))
                self.last_heartbeat=time.monotonic()
            return
        with self.engine.tx() as db:row=db.execute("SELECT t.id,t.state,t.skill FROM tasks t JOIN scheduled_tasks s ON s.task_id=t.id WHERE t.state IN ('submitted','unknown') ORDER BY t.created LIMIT 1").fetchone()
        if not row:return
        self.active=row['id']
        if row['state']=='submitted':self.future=self.worker.submit(self._execute,row['id'])
        else:self.future=self.worker.submit(self.engine.reconcile,row['id'],self.service.adapter_for(row['skill']))
    def tick(self):
        self.process_inbox();self.notices();self.work()
        if self.protocol:self.protocol.tick()
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
