"""Fail-closed LEZ program deployment and generic public calls.

Both operations always require an owner approval in the immutable skill registry.
Generic program calls are deliberately public-account calls with a zero LEZ spend
ceiling. Typed adapters can add stronger autonomous policies later; this escape
hatch never guesses a spend bound for arbitrary program bytecode.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
from .codec import Rejected,canonical
from .engine import Prepared,Receipt
from .filesystem import FileRoot

class ProgramAdapter:
    def __init__(self,root:Path,wallet,engine):
        self.root=root;self.wallet=wallet;self.engine=engine;self.inputs=FileRoot(root/'inputs')
        with engine.tx() as db:db.execute('''CREATE TABLE IF NOT EXISTS program_effects(task_id TEXT PRIMARY KEY,skill TEXT NOT NULL,args TEXT NOT NULL,tx_hash TEXT,program_id TEXT,result TEXT)''')
    def row(self,id):
        with self.engine.tx() as db:r=db.execute('SELECT * FROM program_effects WHERE task_id=?',(id,)).fetchone()
        if not r:raise Rejected('PROGRAM_EFFECT_NOT_PREPARED')
        return dict(r)
    def prepare(self,task):
        if task['skill'] not in ['program.call','program.deploy']:raise Rejected('PROGRAM_SKILL_UNAVAILABLE')
        if task['maximum_spend']!='0':raise Rejected('GENERIC_PROGRAM_SPEND_MUST_BE_ZERO')
        args=task['arguments'];id=task['id']
        with self.engine.tx() as db:
            raw=canonical(args).decode();old=db.execute('SELECT * FROM program_effects WHERE task_id=?',(id,)).fetchone()
            if old and (old['skill']!=task['skill'] or old['args']!=raw):raise Rejected('PROGRAM_EFFECT_REUSED')
            db.execute('INSERT OR IGNORE INTO program_effects(task_id,skill,args) VALUES (?,?,?)',(id,task['skill'],raw))
        if task['skill']=='program.deploy':
            path=self.inputs.parts(args['binary_path'])
            source=self.inputs.root.joinpath(*path);resolved=source.resolve()
            if not resolved.is_relative_to(self.inputs.root) or source.is_symlink() or not source.is_file() or not 0<source.stat().st_size<=64*1024*1024:raise Rejected('PROGRAM_BINARY_DENIED')
            intent={'kind':'program-deploy','arguments':{'binary_path':str(resolved)},'expires_at':task['deadline']}
        else:
            resolved=self.resolve_call(args)
            intent={'kind':'program-call-public','arguments':resolved,'expires_at':task['deadline']}
        result=self.wallet.invoke('prepare-program',{'operation_id':'program-'+id,'intent':intent},timeout=7100)
        if result.get('state') not in ['prepared','confirmed'] or result.get('maximum_spend')!='0':raise Rejected('PROGRAM_PREPARATION_MISMATCH')
        tx=result.get('tx_hash')
        if not isinstance(tx,str) or not re.fullmatch('[0-9a-f]{64}',tx):raise Rejected('PROGRAM_TRANSACTION_HASH_MISSING')
        program_id=result.get('program_id')
        if task['skill']=='program.deploy' and (not isinstance(program_id,str) or not re.fullmatch('[0-9a-f]{64}',program_id)):raise Rejected('DEPLOYED_PROGRAM_ID_MISSING')
        with self.engine.tx() as db:db.execute('UPDATE program_effects SET tx_hash=?,program_id=? WHERE task_id=?',(tx,program_id,id))
        return Prepared(tx,0,id)
    def resolve_call(self,args):
        self.validate_call(args)
        resolved=json.loads(canonical(args).decode())
        own=None
        for item in resolved['params']['accounts']:
            if item['account_id']=='self':
                if own is None:
                    own=self.wallet.invoke('program-account',{})
                    if own.get('wallet_owned') is not True or own.get('public') is not True or not re.fullmatch('[0-9a-f]{64}',own.get('account_id','')):raise Rejected('PROGRAM_ACCOUNT_UNAVAILABLE')
                item['account_id']=own['account_id']
        return resolved
    def validate_call(self,args):
        if set(args)!={'program_id','instruction','params'} or not re.fullmatch('[0-9a-f]{64}',args['program_id']):raise Rejected('INVALID_PROGRAM_CALL')
        instruction=args['instruction']
        if not isinstance(instruction,str) or len(instruction)>131072 or len(instruction)%8 or not re.fullmatch('[0-9a-f]*',instruction):raise Rejected('INVALID_PROGRAM_INSTRUCTION')
        params=args['params']
        if not isinstance(params,dict) or set(params)!={'accounts'} or not isinstance(params['accounts'],list) or not 1<=len(params['accounts'])<=16:raise Rejected('INVALID_PROGRAM_ACCOUNTS')
        signers=0
        for item in params['accounts']:
            if not isinstance(item,dict) or set(item)!={'account_id','signer'} or item.get('account_id')!='self' and not re.fullmatch('[0-9a-f]{64}',item.get('account_id','')) or type(item.get('signer')) is not bool:raise Rejected('INVALID_PROGRAM_ACCOUNT')
            signers+=int(item['signer'])
        if not signers:raise Rejected('PROGRAM_CALL_REQUIRES_WALLET_SIGNER')
    def broadcast(self,effect):
        row=self.row(effect.opaque_handle)
        if row['tx_hash']!=effect.reference:raise Rejected('PROGRAM_HASH_BINDING_MISMATCH')
        result=self.wallet.invoke('broadcast',{'operation_id':'program-'+effect.opaque_handle},timeout=120)
        if result.get('tx_hash')!=effect.reference:raise Rejected('PROGRAM_BROADCAST_HASH_MISMATCH')
    def lookup(self,effect):
        row=self.row(effect.opaque_handle)
        result=self.wallet.invoke('reconcile',{'operation_id':'program-'+effect.opaque_handle},timeout=120)
        if result.get('tx_hash')!=effect.reference:raise Rejected('PROGRAM_RECEIPT_HASH_MISMATCH')
        if result.get('state')!='confirmed':return Receipt(effect.reference,'pending')
        block=result.get('block_id')
        if type(block)is not int or block<0:raise Rejected('PROGRAM_CONFIRMATION_BLOCK_MISSING')
        value={'transaction_hash':effect.reference,'block_id':block,'program_id':row['program_id'],'testnet':True}
        with self.engine.tx() as db:db.execute('UPDATE program_effects SET result=? WHERE task_id=?',(canonical(value).decode(),effect.opaque_handle))
        return Receipt(effect.reference,'confirmed',0,value)
