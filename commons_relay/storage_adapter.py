"""Trusted execution of storage skills through the Core bridge and private vault."""
import json
from .engine import Prepared,Receipt
from .codec import Rejected
from .vault import Vault

class StorageAdapter:
    def __init__(self,vault:Vault,store):
        self.vault=vault;self.store=store;self.prepared={};self.results={}
    def prepare(self,task:dict)->Prepared:
        skill=task['skill'];arguments=task['arguments'];task_id=task['id']
        if int(task['maximum_spend'])!=0:raise Rejected('UNEXPECTED_STORAGE_PAYMENT')
        if skill in ['storage.upload','storage.download'] and hasattr(self.store,'initialize'):
            self.store.initialize()
        if skill=='storage.upload':self.vault.prepare_upload(task_id,arguments['path'],arguments['label'])
        elif skill=='storage.download':
            self.vault.output.parts(arguments['path']);self.vault._file(arguments['address'])
        elif skill!='storage.list':raise Rejected('SKILL_ADAPTER_NOT_CONNECTED')
        # Persist before effects, not just an in-memory closure.
        with self.vault.tx() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS effects(task_id TEXT PRIMARY KEY,skill TEXT NOT NULL,args TEXT NOT NULL,result TEXT)''')
            old=db.execute('SELECT skill,args FROM effects WHERE task_id=?',(task_id,)).fetchone()
            encoded=json.dumps(arguments,sort_keys=True,separators=(',',':'))
            if old and (old['skill']!=skill or old['args']!=encoded):raise Rejected('STORAGE_EFFECT_REUSED')
            db.execute('INSERT OR IGNORE INTO effects VALUES (?,?,?,NULL)',(task_id,skill,encoded))
        return Prepared('storage:'+task_id,0,task_id)
    def broadcast(self,effect:Prepared):
        with self.vault.guard:
            row=self.vault.db.execute('SELECT * FROM effects WHERE task_id=?',(effect.opaque_handle,)).fetchone()
        if not row:raise Rejected('STORAGE_EFFECT_NOT_PREPARED')
        if row['result']:return
        arguments=json.loads(row['args'])
        if row['skill']=='storage.upload':result=self.vault.upload(effect.opaque_handle,self.store)
        elif row['skill']=='storage.download':result=self.vault.download(arguments['address'],arguments['path'],self.store)
        elif row['skill']=='storage.list':result={'files':self.vault.list()}
        else:raise Rejected('SKILL_ADAPTER_NOT_CONNECTED')
        with self.vault.tx() as db:
            db.execute('UPDATE effects SET result=? WHERE task_id=?',(json.dumps(result,separators=(',',':')),effect.opaque_handle))
    def lookup(self,effect:Prepared)->Receipt:
        with self.vault.guard:
            row=self.vault.db.execute('SELECT * FROM effects WHERE task_id=?',(effect.opaque_handle,)).fetchone()
            # The catalogue may already have an upload commit when the parent
            # task lost its response. Reconcile it without re-uploading.
            uploaded=self.vault.db.execute('SELECT address,label,bytes FROM blobs WHERE operation=? AND state=?',(effect.opaque_handle,'stored')).fetchone()
        if row and row['result']:return Receipt(effect.reference,'confirmed',0,json.loads(row['result']))
        if row and row['skill']=='storage.upload' and uploaded:return Receipt(effect.reference,'confirmed',0,dict(uploaded))
        return Receipt(effect.reference,'pending')
