"""Authoritative wallet effects via the installed LEZ Wallet Core companion."""
from __future__ import annotations
import json
from pathlib import Path
import re
import threading
from .codec import Rejected,canonical,parse,amount
from .engine import Prepared,Receipt

class WalletAdapter:
    def __init__(self,root:Path,wire,engine):
        self.root=root;self.wire=wire;self.engine=engine;self.ready=False;self.guard=threading.RLock()
        with engine.tx() as db:db.execute('CREATE TABLE IF NOT EXISTS wallet_effects(task_id TEXT PRIMARY KEY,skill TEXT NOT NULL,arguments TEXT NOT NULL,result TEXT,tx_hash TEXT,amount TEXT NOT NULL)')
    def initialize(self):
        with self.guard:
            if not self.ready:self.wire.call('wallet.init',{});self.ready=True
    def invoke(self,mode,params=None,timeout=120):
        self.initialize();return self.wire.call('wallet.invoke',{'mode':mode,'params':params or {}},timeout=timeout)
    def recipient(self,name):
        path=self.root/'payment-addresses.json'
        if not path.is_file() or path.is_symlink() or path.stat().st_size>100000:raise Rejected('VERIFIED_RECIPIENT_DIRECTORY_REQUIRED')
        data=parse(path.read_bytes())
        if not isinstance(data,dict) or name not in data:raise Rejected('FRESH_RECIPIENT_ADDRESS_REQUIRED')
        recipient=data[name]
        if not isinstance(recipient,dict) or set(recipient)!={'account_id','npk','vpk_borsh','identifier'}:raise Rejected('INVALID_RECIPIENT_DESCRIPTOR')
        # The Rust companion independently derives and matches the account ID.
        return recipient
    def prepare(self,task):
        skill=task['skill'];args=task['arguments'];id=task['id'];maximum=amount(task['maximum_spend'])
        if skill not in ['wallet.balance','wallet.history','wallet.send','program.query']:raise Rejected('WALLET_SKILL_UNAVAILABLE')
        with self.engine.tx() as db:
            old=db.execute('SELECT * FROM wallet_effects WHERE task_id=?',(id,)).fetchone()
            if old and (old['skill']!=skill or old['arguments']!=canonical(args).decode()):raise Rejected('WALLET_EFFECT_REUSED')
            db.execute('INSERT OR IGNORE INTO wallet_effects VALUES (?,?,?,NULL,NULL,?)',(id,skill,canonical(args).decode(),str(maximum)))
        if skill=='wallet.send':
            units=amount(args['amount'])
            if units<=0 or units>maximum:raise Rejected('WALLET_QUOTE_MISMATCH')
            intent={'kind':'transfer-private','arguments':{'recipient':self.recipient(args['recipient']),'amount':str(units)},'expires_at':task['deadline']}
            result=self.invoke('prepare',{'operation_id':id,'intent':intent},timeout=7100)
            if result.get('state') not in ['prepared','confirmed'] or amount(result.get('maximum_spend'))!=units or not re.fullmatch('[a-f0-9]{64}',result.get('tx_hash','')):raise Rejected('WALLET_PREPARATION_MISMATCH')
            with self.engine.tx() as db:db.execute('UPDATE wallet_effects SET tx_hash=? WHERE task_id=?',(result['tx_hash'],id))
            return Prepared(result['tx_hash'],units,id)
        if maximum!=0:raise Rejected('READ_OPERATION_HAS_SPEND')
        if skill=='wallet.balance':result=self.invoke('balance')
        elif skill=='wallet.history':result=self.invoke('history')
        else:
            params=args['params']
            if not isinstance(params,dict) or set(params)!={'account'} or not isinstance(params['account'],str):raise Rejected('PROGRAM_QUERY_REQUIRES_ACCOUNT')
            result=self.invoke('query',{'account':params['account']})
            expected=args['program_id']
            actual=result.get('program_owner')
            if isinstance(actual,list):
                import struct
                actual=struct.pack('<8I',*actual).hex() if len(actual)==8 else ''
            if expected!=actual:raise Rejected('QUERIED_ACCOUNT_PROGRAM_MISMATCH')
        with self.engine.tx() as db:db.execute('UPDATE wallet_effects SET result=? WHERE task_id=?',(canonical(result).decode(),id))
        return Prepared('wallet-read:'+id,0,id)
    def broadcast(self,effect):
        with self.engine.tx() as db:row=db.execute('SELECT * FROM wallet_effects WHERE task_id=?',(effect.opaque_handle,)).fetchone()
        if not row:raise Rejected('WALLET_EFFECT_MISSING')
        if row['skill']!='wallet.send':return
        if row['tx_hash']!=effect.reference:raise Rejected('WALLET_HASH_BINDING_MISMATCH')
        result=self.invoke('broadcast',{'operation_id':effect.opaque_handle},timeout=120)
        if result.get('tx_hash')!=effect.reference:raise Rejected('WALLET_BROADCAST_HASH_MISMATCH')
    def lookup(self,effect):
        with self.engine.tx() as db:row=db.execute('SELECT * FROM wallet_effects WHERE task_id=?',(effect.opaque_handle,)).fetchone()
        if not row:return Receipt(effect.reference,'pending')
        if row['skill']!='wallet.send':return Receipt(effect.reference,'confirmed',0,json.loads(row['result'])) if row['result'] else Receipt(effect.reference,'pending')
        result=self.invoke('reconcile',{'operation_id':effect.opaque_handle},timeout=120)
        if result.get('tx_hash')!=effect.reference:raise Rejected('WALLET_RECEIPT_HASH_MISMATCH')
        if result.get('state')=='confirmed':
            if type(result.get('block_id'))is not int or result['block_id']<0:raise Rejected('WALLET_CONFIRMATION_BLOCK_MISSING')
            return Receipt(effect.reference,'confirmed',int(row['amount']),{'transaction_hash':effect.reference,'block_id':result['block_id'],'private':True})
        return Receipt(effect.reference,'pending')
