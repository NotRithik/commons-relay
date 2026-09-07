"""Encrypted file catalogue and content-addressed transport boundary.

The transport is supplied by the Logos adapter. The vault never reaches into a
user home directory or passes file keys to an inference provider.
"""
from __future__ import annotations
from dataclasses import dataclass
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import threading
import time
from typing import Protocol
from .codec import Rejected,b64,unb64,canonical,parse,digest,identifier
from .file_crypto import Sodium,MAX_FILE
from .filesystem import FileRoot
from .signing import Ed25519,protected_directory,sign_envelope,verify_envelope,key_id

class StorePort(Protocol):
    def upload(self,ciphertext_file:Path,operation_id:str)->str: ...
    def download(self,address:str,destination:Path,maximum_bytes:int)->None: ...

@dataclass(frozen=True)
class Peer:
    name:str
    signing_key:bytes
    box_key:bytes
    def __post_init__(self):
        identifier(self.name)
        if len(self.signing_key)!=32 or len(self.box_key)!=32:raise Rejected('INVALID_PEER_KEYS')

def cid(value:str)->str:
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9]{20,180}',value):raise Rejected('INVALID_CONTENT_ADDRESS')
    return value

def operation_id(value:str)->str:
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',value):raise Rejected('INVALID_FILE_OPERATION_ID')
    return value

class Vault:
    def __init__(self,root:Path,inputs:Path,outputs:Path,crypto:Sodium,maximum_file=MAX_FILE,clock=time.time):
        self.root=protected_directory(root);self.input=FileRoot(inputs);self.output=FileRoot(outputs)
        self.crypto=crypto;self.maximum=maximum_file;self.clock=clock
        if type(maximum_file)is not int or not 0<=maximum_file<=MAX_FILE:raise Rejected('INVALID_FILE_LIMIT')
        self.blobs=protected_directory(self.root/'blobs');self.downloads=protected_directory(self.root/'downloads');self.blob_root=FileRoot(self.blobs)
        self.signer=Ed25519(self.root/'keys');self.signing_private=self.signer.scratch/'identity.pem'
        if self.signing_private.exists():self.signing_public=self.signer.public(self.signing_private)
        else:self.signing_public=self.signer.generate(self.signing_private)
        secret=self.signer.scratch/'box-secret';public=self.signer.scratch/'box-public'
        if secret.exists() or public.exists():
            if not secret.is_file() or secret.is_symlink() or stat.S_IMODE(secret.stat().st_mode)&0o077:raise Rejected('BOX_KEY_PERMISSIONS')
            self.box_secret=secret.read_bytes();self.box_public=public.read_bytes()
            if len(self.box_secret)!=32 or len(self.box_public)!=32:raise Rejected('INVALID_BOX_KEY')
        else:
            self.box_public,self.box_secret=self.crypto.box_keypair()
            for path,data in [(secret,self.box_secret),(public,self.box_public)]:
                fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        dbpath=self.root/'catalog.sqlite'
        if dbpath.is_symlink():raise Rejected('SYMLINK_CATALOG')
        if not dbpath.exists():
            fd=os.open(dbpath,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
        if stat.S_IMODE(dbpath.stat().st_mode)&0o077:raise Rejected('CATALOG_PERMISSIONS')
        self.db=sqlite3.connect(dbpath,isolation_level=None,check_same_thread=False);self.db.row_factory=sqlite3.Row;self.guard=threading.RLock()
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL');self.db.execute('PRAGMA trusted_schema=OFF')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS blobs(operation TEXT PRIMARY KEY,intent_hash TEXT NOT NULL,label TEXT NOT NULL,
            key BLOB NOT NULL,ciphertext_path TEXT NOT NULL,ciphertext_hash TEXT NOT NULL,bytes INTEGER NOT NULL,
            ciphertext_bytes INTEGER NOT NULL,address TEXT,state TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS files(address TEXT PRIMARY KEY,key BLOB NOT NULL,ciphertext_hash TEXT NOT NULL,
            bytes INTEGER NOT NULL,ciphertext_bytes INTEGER NOT NULL,label TEXT NOT NULL,sender TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS shares(id TEXT PRIMARY KEY,sender TEXT NOT NULL,body_hash TEXT NOT NULL,address TEXT NOT NULL);
        ''')
    def close(self):self.db.close()
    @contextmanager
    def tx(self):
        with self.guard:
            self.db.execute('BEGIN IMMEDIATE')
            try:yield self.db
            except BaseException:self.db.rollback();raise
            else:self.db.commit()
    def identity(self)->Peer:return Peer(key_id(self.signing_public),self.signing_public,self.box_public)
    def prepare_upload(self,operation:str,relative:str,label:str)->dict:
        operation_id(operation);self.input.parts(relative)
        if not isinstance(label,str) or not 1<=len(label)<=200:raise Rejected('INVALID_FILE_LABEL')
        intent=digest({'path':relative,'label':label})
        with self.tx() as db:
            existing=db.execute('SELECT * FROM blobs WHERE operation=?',(operation,)).fetchone()
            if existing:
                if existing['intent_hash']!=intent:raise Rejected('FILE_OPERATION_REUSED')
                return {'operation_id':operation,'reference':'file:'+operation,'state':existing['state'],'bytes':existing['bytes']}
            if db.execute('SELECT COUNT(*) FROM blobs').fetchone()[0]>=1000:raise Rejected('VAULT_FILE_LIMIT')
            key=secrets.token_bytes(32);name=operation+'-'+secrets.token_hex(12)+'.bin'
            with self.input.read(relative,self.maximum) as source,self.blob_root.create(name) as dest:
                measure=self.crypto.encrypt(source,dest,{'label':label,'version':1},key,maximum=self.maximum)
            db.execute('INSERT INTO blobs VALUES (?,?,?,?,?,?,?,?,?,?)',(operation,intent,label,key,name,measure['ciphertext_sha256'],measure['plaintext_bytes'],measure['ciphertext_bytes'],None,'prepared'))
            return {'operation_id':operation,'reference':'file:'+operation,'state':'prepared','bytes':measure['plaintext_bytes']}
    def upload(self,operation:str,store:StorePort)->dict:
        operation_id(operation)
        with self.guard:
            row=self.db.execute('SELECT * FROM blobs WHERE operation=?',(operation,)).fetchone()
        if not row:raise Rejected('FILE_OPERATION_NOT_PREPARED')
        if row['state']=='stored':return {'address':row['address'],'label':row['label'],'bytes':row['bytes']}
        path=self.blobs/row['ciphertext_path']
        if path.is_symlink() or not path.is_file():raise Rejected('PREPARED_CIPHERTEXT_MISSING')
        with path.open('rb') as f:
            if hashlib.file_digest(f,'sha256').hexdigest()!=row['ciphertext_hash']:raise Rejected('PREPARED_CIPHERTEXT_CHANGED')
        address=cid(store.upload(path,operation))
        with self.tx() as db:
            old=db.execute('SELECT * FROM files WHERE address=?',(address,)).fetchone()
            if old and (old['ciphertext_hash']!=row['ciphertext_hash'] or old['key']!=row['key']):raise Rejected('CONTENT_ADDRESS_COLLISION')
            db.execute('INSERT OR IGNORE INTO files VALUES (?,?,?,?,?,?,?)',(address,row['key'],row['ciphertext_hash'],row['bytes'],row['ciphertext_bytes'],row['label'],key_id(self.signing_public)))
            db.execute("UPDATE blobs SET address=?,state='stored' WHERE operation=?",(address,operation))
        return {'address':address,'label':row['label'],'bytes':row['bytes']}
    def list(self)->list[dict]:
        with self.guard:return [dict(r) for r in self.db.execute('SELECT address,label,bytes,sender FROM files ORDER BY address LIMIT 1000')]
    def _file(self,address):
        cid(address)
        with self.guard:row=self.db.execute('SELECT * FROM files WHERE address=?',(address,)).fetchone()
        if not row:raise Rejected('FILE_KEY_NOT_AVAILABLE')
        return row
    def download(self,address:str,relative:str,store:StorePort)->dict:
        self.output.parts(relative);row=self._file(address)
        temp=self.downloads/(secrets.token_hex(16)+'.bin')
        try:
            store.download(address,temp,row['ciphertext_bytes'])
            if temp.is_symlink() or not temp.is_file() or temp.stat().st_size!=row['ciphertext_bytes']:raise Rejected('DOWNLOADED_FILE_SIZE_MISMATCH')
            with temp.open('rb') as f:
                if hashlib.file_digest(f,'sha256').hexdigest()!=row['ciphertext_hash']:raise Rejected('DOWNLOADED_CIPHERTEXT_CHANGED')
            with temp.open('rb') as source,self.output.create(relative) as destination:
                info=self.crypto.decrypt(source,destination,bytes(row['key']),maximum=self.maximum)
                if info['plaintext_bytes']!=row['bytes'] or info['metadata'].get('label')!=row['label']:raise Rejected('FILE_METADATA_MISMATCH')
            return {'address':address,'path':relative,'bytes':row['bytes'],'authenticated':True}
        finally:
            if temp.exists() and not temp.is_symlink():temp.unlink()
    def make_share(self,address:str,recipient:Peer,lifetime=300)->dict:
        if type(lifetime)is not int or not 1<=lifetime<=3600:raise Rejected('INVALID_SHARE_EXPIRY')
        row=self._file(address)
        payload={'address':address,'key':b64(bytes(row['key'])),'ciphertext_hash':row['ciphertext_hash'],'bytes':row['bytes'],'ciphertext_bytes':row['ciphertext_bytes'],'label':row['label'],'version':1}
        body={'domain':'commons/commons_relay/file-share/v1','recipient':key_id(recipient.signing_key),'recipient_box_key':b64(recipient.box_key),
              'share_id':secrets.token_hex(16),'expires_at':int(self.clock())+lifetime,'sealed':b64(self.crypto.seal(recipient.box_key,canonical(payload)))}
        return sign_envelope(body,self.signing_private,self.signer)
    def receive_share(self,envelope:dict,sender:Peer)->dict:
        body=verify_envelope(envelope,sender.signing_key,self.signer)
        expected={'domain','recipient','recipient_box_key','share_id','expires_at','sealed'}
        if set(body)!=expected or body['domain']!='commons/commons_relay/file-share/v1' or body['recipient']!=key_id(self.signing_public) or body['recipient_box_key']!=b64(self.box_public):
            raise Rejected('WRONG_SHARE_RECIPIENT')
        if type(body['expires_at'])is not int or not int(self.clock())<body['expires_at']<=int(self.clock())+3600:raise Rejected('SHARE_EXPIRED')
        operation_id(body['share_id'])
        payload=parse(self.crypto.open_sealed(self.box_public,self.box_secret,unb64(body['sealed'],8192)))
        if not isinstance(payload,dict) or set(payload)!={'address','key','ciphertext_hash','bytes','ciphertext_bytes','label','version'} or payload['version']!=1:raise Rejected('INVALID_FILE_SHARE')
        address=cid(payload['address']);key=unb64(payload['key'],32)
        if len(key)!=32 or not re.fullmatch('[0-9a-f]{64}',payload['ciphertext_hash']):raise Rejected('INVALID_FILE_SHARE')
        if type(payload['bytes'])is not int or not 0<=payload['bytes']<=self.maximum or type(payload['ciphertext_bytes'])is not int or not 0<payload['ciphertext_bytes']<=self.maximum+1024*1024:raise Rejected('SHARED_FILE_TOO_LARGE')
        if not isinstance(payload['label'],str) or not 1<=len(payload['label'])<=200:raise Rejected('INVALID_FILE_LABEL')
        with self.tx() as db:
            old=db.execute('SELECT * FROM shares WHERE id=?',(body['share_id'],)).fetchone()
            if old and (old['body_hash']!=digest(body) or old['sender']!=key_id(sender.signing_key)):raise Rejected('SHARE_ID_REPLAY_MISMATCH')
            existing=db.execute('SELECT * FROM files WHERE address=?',(address,)).fetchone()
            if existing and (existing['key']!=key or existing['ciphertext_hash']!=payload['ciphertext_hash']):raise Rejected('CONFLICTING_FILE_SHARE')
            db.execute('INSERT OR IGNORE INTO files VALUES (?,?,?,?,?,?,?)',(address,key,payload['ciphertext_hash'],payload['bytes'],payload['ciphertext_bytes'],payload['label'],key_id(sender.signing_key)))
            db.execute('INSERT OR IGNORE INTO shares VALUES (?,?,?,?)',(body['share_id'],key_id(sender.signing_key),digest(body),address))
        return {'address':address,'label':payload['label'],'sender':key_id(sender.signing_key)}
