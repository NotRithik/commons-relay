"""Persistent encrypted messaging over the Logos Delivery Core module.

Delivery topics carry only recipient-sealed, sender-signed envelopes. Application
receipts acknowledge a durable inbox write, not merely a local send callback.
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from .codec import Rejected,b64,unb64,canonical,parse,digest,identifier
from .signing import sign_envelope,verify_envelope,key_id,protected_directory
from .vault import Vault,Peer

DOMAIN='commons/relay/message/v1'
KINDS=frozenset(['text','ack','file-share','group-invite','group-message','owner-command','owner-result','a2a-request','a2a-response','a2a-event','agent-card'])

def topic(address:str)->str:
    if not isinstance(address,str) or not 1<=len(address)<=180:raise Rejected('INVALID_MESSAGE_ADDRESS')
    return '/commons-relay/1/'+hashlib.sha256(address.encode()).hexdigest()+'/json'

@dataclass(frozen=True)
class Contact:
    address:str
    signing_key:bytes
    box_key:bytes
    label:str=''
    def __post_init__(self):
        topic(self.address)
        if len(self.signing_key)!=32 or len(self.box_key)!=32 or len(self.label)>200:raise Rejected('INVALID_CONTACT')
    def public(self):return {'address':self.address,'signing_key':b64(self.signing_key),'box_key':b64(self.box_key),'label':self.label}
    @classmethod
    def from_public(cls,value):
        if not isinstance(value,dict) or set(value)!={'address','signing_key','box_key','label'}:raise Rejected('INVALID_CONTACT_DOCUMENT')
        return cls(value['address'],unb64(value['signing_key'],32),unb64(value['box_key'],32),value['label'])

class Mailbox:
    def __init__(self,root:Path,vault:Vault,address:str,clock=time.time):
        self.root=protected_directory(root);self.vault=vault;self.address=address;self.clock=clock;topic(address)
        self.guard=threading.RLock();path=self.root/'mailbox.sqlite'
        if path.is_symlink():raise Rejected('SYMLINK_MAILBOX')
        import os
        if not path.exists():
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
        if path.stat().st_mode&0o077:raise Rejected('MAILBOX_PERMISSIONS')
        self.db=sqlite3.connect(path,isolation_level=None,check_same_thread=False);self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL');self.db.execute('PRAGMA trusted_schema=OFF')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS contacts(address TEXT PRIMARY KEY,signing BLOB NOT NULL,box BLOB NOT NULL,label TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inbox(id TEXT PRIMARY KEY,sender TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL,body_hash TEXT NOT NULL,received INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY,recipient TEXT NOT NULL,kind TEXT NOT NULL,wire TEXT NOT NULL,intent_hash TEXT NOT NULL,state TEXT NOT NULL,created INTEGER NOT NULL,expires INTEGER NOT NULL,next_send INTEGER NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,request_id TEXT);
        CREATE TABLE IF NOT EXISTS groups(id TEXT PRIMARY KEY,creator TEXT NOT NULL,members TEXT NOT NULL,state TEXT NOT NULL,created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS counters(sender TEXT NOT NULL,window INTEGER NOT NULL,count INTEGER NOT NULL,PRIMARY KEY(sender,window));
        ''')
    def close(self):self.db.close()
    @contextmanager
    def tx(self):
        with self.guard:
            self.db.execute('BEGIN IMMEDIATE')
            try:yield self.db
            except BaseException:self.db.rollback();raise
            else:self.db.commit()
    def contact(self):return Contact(self.address,self.vault.signing_public,self.vault.box_public,self.address)
    def add_contact(self,contact:Contact):
        # Deployment / signed owner settings call this. Never trust a random
        # network message as permission to replace an existing contact key.
        with self.tx() as db:
            existing=db.execute('SELECT * FROM contacts WHERE address=?',(contact.address,)).fetchone()
            if existing and (existing['signing']!=contact.signing_key or existing['box']!=contact.box_key):raise Rejected('CONTACT_KEY_CHANGED')
            if not existing and db.execute('SELECT COUNT(*) FROM contacts').fetchone()[0]>=256:raise Rejected('CONTACT_LIMIT')
            db.execute('INSERT OR IGNORE INTO contacts VALUES (?,?,?,?)',(contact.address,contact.signing_key,contact.box_key,contact.label))
    def get_contact(self,address):
        if address==self.address:return self.contact()
        with self.guard:row=self.db.execute('SELECT * FROM contacts WHERE address=?',(address,)).fetchone()
        if not row:raise Rejected('UNKNOWN_MESSAGE_CONTACT')
        return Contact(row['address'],bytes(row['signing']),bytes(row['box']),row['label'])
    def contacts(self):
        with self.guard:return [Contact(r['address'],bytes(r['signing']),bytes(r['box']),r['label']).public() for r in self.db.execute('SELECT * FROM contacts ORDER BY address')]
    def enqueue(self,recipient:str,kind:str,payload,message_id=None,ttl=3600):
        if kind not in KINDS:raise Rejected('UNKNOWN_MESSAGE_KIND')
        if type(ttl)is not int or not 1<=ttl<=86400:raise Rejected('INVALID_MESSAGE_TTL')
        encoded=canonical(payload)
        if len(encoded)>16000:raise Rejected('MESSAGE_PAYLOAD_TOO_LARGE')
        message_id=identifier(message_id or secrets.token_hex(16))
        to=self.get_contact(recipient);now=int(self.clock())
        intent=digest({'recipient':recipient,'kind':kind,'payload':payload})
        with self.tx() as db:
            old=db.execute('SELECT * FROM outbox WHERE id=?',(message_id,)).fetchone()
            if old:
                if old['intent_hash']!=intent:raise Rejected('MESSAGE_ID_REUSED')
                return {'message_id':message_id,'recipient':recipient,'state':old['state']}
            if db.execute("SELECT COUNT(*) FROM outbox WHERE state NOT IN ('acknowledged','expired')").fetchone()[0]>=1000:raise Rejected('OUTBOX_FULL')
            body={'domain':DOMAIN,'id':message_id,'sender':self.address,'recipient':recipient,'kind':kind,'payload':payload,'created':now,'expires':now+ttl}
            signed=sign_envelope(body,self.vault.signing_private,self.vault.signer)
            cipher=self.vault.crypto.seal(to.box_key,canonical(signed))
            wire=canonical({'version':1,'recipient':recipient,'ciphertext':b64(cipher)}).decode()
            if len(wire.encode())>48000:raise Rejected('MESSAGE_ENVELOPE_TOO_LARGE')
            db.execute('INSERT INTO outbox(id,recipient,kind,wire,intent_hash,state,created,expires,next_send) VALUES (?,?,?,?,?,?,?,?,?)',(message_id,recipient,kind,wire,intent,'queued',now,now+ttl,now))
        return {'message_id':message_id,'recipient':recipient,'state':'queued'}
    def outgoing(self,limit=4):
        now=int(self.clock())
        with self.tx() as db:
            db.execute("UPDATE outbox SET state='expired' WHERE expires<=? AND state!='acknowledged'",(now,))
            rows=db.execute("SELECT id,recipient,wire,attempts FROM outbox WHERE state IN ('queued','sent') AND expires>? AND next_send<=? AND attempts<20 ORDER BY created,id LIMIT ?",(now,now,limit)).fetchall()
            for r in rows:db.execute("UPDATE outbox SET next_send=?,attempts=attempts+1 WHERE id=?",(now+min(300,2**min(r['attempts'],8)+2),r['id']))
            return [dict(r) for r in rows]
    def mark_sent(self,id,request_id):
        if not isinstance(request_id,str) or not 1<=len(request_id)<=160:raise Rejected('INVALID_DELIVERY_REQUEST_ID')
        with self.tx() as db:
            row=db.execute('SELECT kind FROM outbox WHERE id=?',(id,)).fetchone()
            if row:db.execute("UPDATE outbox SET state=CASE WHEN kind='ack' THEN 'acknowledged' WHEN state='acknowledged' THEN state ELSE 'sent' END,request_id=? WHERE id=?",(request_id,id))
    def status(self,id):
        with self.guard:r=self.db.execute('SELECT id,recipient,kind,state,attempts FROM outbox WHERE id=?',(id,)).fetchone()
        if not r:raise Rejected('MESSAGE_NOT_FOUND')
        return dict(r)
    def receive(self,raw:str,received_topic:str):
        if received_topic!=topic(self.address) or not isinstance(raw,str) or len(raw.encode())>48000:raise Rejected('MESSAGE_ROUTE_MISMATCH')
        envelope=parse(raw.encode())
        if not isinstance(envelope,dict) or set(envelope)!={'version','recipient','ciphertext'} or envelope['version']!=1 or envelope['recipient']!=self.address:raise Rejected('INVALID_MESSAGE_ENVELOPE')
        signed=parse(self.vault.crypto.open_sealed(self.vault.box_public,self.vault.box_secret,unb64(envelope['ciphertext'],35000)))
        if not isinstance(signed,dict) or not isinstance(signed.get('body'),dict):raise Rejected('INVALID_SIGNED_MESSAGE')
        claimed=signed['body'].get('sender');sender=self.get_contact(claimed)
        body=verify_envelope(signed,sender.signing_key,self.vault.signer)
        if set(body)!={'domain','id','sender','recipient','kind','payload','created','expires'} or body['domain']!=DOMAIN or body['recipient']!=self.address or body['kind'] not in KINDS:raise Rejected('INVALID_MESSAGE_BINDING')
        identifier(body['id']);now=int(self.clock())
        if type(body['created'])is not int or type(body['expires'])is not int or not body['created']<=now+60 or not now<body['expires']<=body['created']+86400:raise Rejected('MESSAGE_EXPIRED')
        if len(canonical(body['payload']))>16000:raise Rejected('MESSAGE_PAYLOAD_TOO_LARGE')
        encoded=canonical(body).decode();digest_body=digest(body)
        with self.tx() as db:
            old=db.execute('SELECT * FROM inbox WHERE id=?',(body['id'],)).fetchone()
            if old:
                if old['body_hash']!=digest_body:raise Rejected('MESSAGE_REPLAY_MISMATCH')
                duplicate=True
            else:
                duplicate=False;window=now//60
                previous=db.execute('SELECT count FROM counters WHERE sender=? AND window=?',(sender.address,window)).fetchone()
                if previous and previous[0]>=120:raise Rejected('PEER_RATE_LIMIT')
                if db.execute('SELECT COUNT(*) FROM inbox').fetchone()[0]>=10000:raise Rejected('INBOX_FULL')
                db.execute('INSERT INTO counters VALUES (?,?,1) ON CONFLICT(sender,window) DO UPDATE SET count=count+1',(sender.address,window))
                db.execute('DELETE FROM counters WHERE window<?',(window-5,))
                if body['kind']=='ack':
                    ack=body['payload']
                    if not isinstance(ack,dict) or set(ack)!={'message_id'}:raise Rejected('INVALID_MESSAGE_ACK')
                    db.execute("UPDATE outbox SET state='acknowledged' WHERE id=? AND recipient=?",(identifier(ack['message_id']),sender.address))
                if body['kind']=='file-share':self.vault.receive_share(body['payload'],Peer(sender.address,sender.signing_key,sender.box_key))
                if body['kind']=='group-invite':self._receive_invite(db,body)
                if body['kind']=='group-message':self._check_group_message(db,body)
                db.execute('INSERT INTO inbox VALUES (?,?,?,?,?,?)',(body['id'],sender.address,body['kind'],encoded,digest_body,now))
        # Ack only after durable commit. Duplicate payloads are never re-executed;
        # returning the same ack lets a sender recover a lost acknowledgement.
        if body['kind']!='ack':
            ack_id='ack-'+hashlib.sha256((sender.address+'\0'+body['id']).encode()).hexdigest()
            self.enqueue(sender.address,'ack',{'message_id':body['id']},message_id=ack_id,ttl=max(1,body['expires']-now))
            if duplicate:
                with self.tx() as db:
                    db.execute("UPDATE outbox SET state='queued',next_send=? WHERE id=? AND next_send<=?",(now,ack_id,now))
        return {**body,'duplicate':duplicate}
    def messages(self,after=0,limit=100):
        if type(after)is not int or after<0:raise Rejected('INVALID_INBOX_CURSOR')
        with self.guard:rows=self.db.execute("SELECT rowid,body FROM inbox WHERE rowid>? AND kind!='ack' ORDER BY rowid LIMIT ?",(after,min(100,limit))).fetchall()
        return [{'cursor':r[0],**json.loads(r[1])} for r in rows]
    def create_group(self,members:list[str],group_id=None):
        if not isinstance(members,list) or not 1<=len(members)<=31 or any(not isinstance(x,str) for x in members) or len(set(members))!=len(members):raise Rejected('INVALID_GROUP_MEMBERS')
        members=sorted(set(members+[self.address]))
        for member in members:self.get_contact(member)
        group_id=identifier(group_id or 'group-'+secrets.token_hex(16))
        with self.tx() as db:
            existing=db.execute('SELECT * FROM groups WHERE id=?',(group_id,)).fetchone()
            if existing and (existing['creator']!=self.address or json.loads(existing['members'])!=members):raise Rejected('GROUP_ID_REUSED')
            db.execute('INSERT OR IGNORE INTO groups VALUES (?,?,?,?,?)',(group_id,self.address,json.dumps(members),'joined',int(self.clock())))
        for member in members:
            if member!=self.address:self.enqueue(member,'group-invite',{'group_id':group_id,'members':members,'creator':self.address},message_id='invite-'+hashlib.sha256((group_id+member).encode()).hexdigest()[:40])
        return {'group_id':group_id,'members':members,'invitations':'queued over encrypted recipient topics'}
    def _receive_invite(self,db,body):
        p=body['payload']
        if not isinstance(p,dict) or set(p)!={'group_id','members','creator'} or p['creator']!=body['sender']:raise Rejected('INVALID_GROUP_INVITE')
        identifier(p['group_id']);members=p['members']
        if not isinstance(members,list) or not 2<=len(members)<=32 or any(not isinstance(x,str) for x in members) or len(set(members))!=len(members) or self.address not in members or p['creator'] not in members:raise Rejected('INVALID_GROUP_MEMBERS')
        for member in members:self.get_contact(member)
        existing=db.execute('SELECT * FROM groups WHERE id=?',(p['group_id'],)).fetchone()
        if existing and (existing['creator']!=p['creator'] or json.loads(existing['members'])!=members):raise Rejected('GROUP_INVITE_CHANGED')
        db.execute('INSERT OR IGNORE INTO groups VALUES (?,?,?,?,?)',(p['group_id'],p['creator'],json.dumps(members),'invited',int(self.clock())))
    def join_group(self,group_id):
        identifier(group_id)
        with self.tx() as db:
            row=db.execute('SELECT * FROM groups WHERE id=?',(group_id,)).fetchone()
            if not row:raise Rejected('VERIFIED_GROUP_INVITE_REQUIRED')
            db.execute("UPDATE groups SET state='joined' WHERE id=?",(group_id,))
            return {'group_id':group_id,'joined':True,'members':json.loads(row['members'])}
    def send_group(self,group_id,message,message_id=None):
        with self.guard:row=self.db.execute("SELECT * FROM groups WHERE id=? AND state='joined'",(group_id,)).fetchone()
        if not row:raise Rejected('GROUP_NOT_JOINED')
        if not isinstance(message,str) or len(message.encode())>12000:raise Rejected('GROUP_MESSAGE_TOO_LARGE')
        message_id=identifier(message_id or secrets.token_hex(16));messages=[]
        for member in json.loads(row['members']):
            if member!=self.address:messages.append(self.enqueue(member,'group-message',{'group_id':group_id,'text':message},message_id=message_id+'-'+hashlib.sha256(member.encode()).hexdigest()[:12]))
        return {'group_id':group_id,'messages':messages}
    def _check_group_message(self,db,body):
        p=body['payload']
        if not isinstance(p,dict) or set(p)!={'group_id','text'} or not isinstance(p['text'],str):raise Rejected('INVALID_GROUP_MESSAGE')
        row=db.execute("SELECT * FROM groups WHERE id=? AND state='joined'",(p['group_id'],)).fetchone()
        if not row or body['sender'] not in json.loads(row['members']):raise Rejected('SENDER_NOT_IN_JOINED_GROUP')

class MessagingRuntime:
    def __init__(self,wire,mailbox:Mailbox):
        self.wire=wire;self.mailbox=mailbox;self.cursor=0;self.started=False;self.guard=threading.Lock();self.stop_event=threading.Event();self.thread=None;self.last_error=None;self.discovery_handlers={}
    def initialize(self):
        with self.guard:
            if self.started:return
            self.wire.call('delivery.init',{});self.wire.call('delivery.start',{});self.wire.call('delivery.subscribe',{'topic':topic(self.mailbox.address)})
            self.started=True
    def pump(self):
        self.initialize()
        with self.guard:
            result=self.wire.call('delivery.events',{'after':str(self.cursor)})
            if not isinstance(result,dict) or not isinstance(result.get('events'),list):raise Rejected('INVALID_DELIVERY_EVENTS')
            latest=int(result['latest'])
            if latest<self.cursor:self.cursor=0;return []
            received=[]
            for event in result['events']:
                seq=int(event['sequence']);self.cursor=max(self.cursor,seq)
                if event['event']=='messageReceived':
                    if event['topic'] in self.discovery_handlers:
                        try:self.discovery_handlers[event['topic']](event['payload'])
                        except Rejected:pass
                        continue
                    try:message=self.mailbox.receive(event['payload'],event['topic'])
                    except Rejected:continue
                    if not message['duplicate']:received.append(message)
            for pending in self.mailbox.outgoing():
                try:
                    request_id=self.wire.call('delivery.send',{'topic':topic(pending['recipient']),'payload':pending['wire']})
                    self.mailbox.mark_sent(pending['id'],request_id)
                except Rejected as error:self.last_error=str(error)
            return received
    def start(self,on_message=None):
        if self.thread:return
        self.initialize()
        def loop():
            while not self.stop_event.is_set():
                try:
                    for message in self.pump():
                        if on_message:on_message(message)
                except Exception:self.last_error='DELIVERY_POLL_FAILED'
                self.stop_event.wait(1)
        self.thread=threading.Thread(target=loop,name='logos-messaging-io',daemon=True);self.thread.start()
    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=3)
