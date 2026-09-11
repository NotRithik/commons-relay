"""Trusted execution of storage skills through the Core bridge and private vault."""
import json
import hashlib
from .engine import Prepared,Receipt
from .codec import Rejected
from .vault import Vault

class StorageAdapter:
    def __init__(self,vault:Vault,store):
        self.vault=vault;self.store=store;self.prepared={};self.results={}
        with self.vault.tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS verified_downloads(operation TEXT PRIMARY KEY,address TEXT NOT NULL,path TEXT NOT NULL,bytes INTEGER NOT NULL,plaintext_sha256 TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS recovery_attempts(task_id TEXT PRIMARY KEY,attempts INTEGER NOT NULL,last_try INTEGER NOT NULL)')
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
        elif row['skill']=='storage.download':result=self.vault.download(arguments['address'],arguments['path'],self.store,operation=effect.opaque_handle)
        elif row['skill']=='storage.list':result={'files':self.vault.list()}
        else:raise Rejected('SKILL_ADAPTER_NOT_CONNECTED')
        self._save_result(effect.opaque_handle,result)
    def _save_result(self,task_id,result):
        with self.vault.tx() as db:
            db.execute('UPDATE effects SET result=? WHERE task_id=?',(json.dumps(result,separators=(',',':')),task_id))
    def recover_unknown(self,effect:Prepared)->Receipt:
        # Only a download is safe to repeat here: it does not publish, transfer,
        # pay, or mutate network state, and FileRoot refuses to overwrite output.
        with self.vault.guard:
            row=self.vault.db.execute('SELECT * FROM effects WHERE task_id=?',(effect.opaque_handle,)).fetchone()
        if not row:return Receipt(effect.reference,'rejected',0,{'error':'STORAGE_DOWNLOAD_STATE_MISSING'})
        if row['result']:return Receipt(effect.reference,'confirmed',0,json.loads(row['result']))
        if row['skill']!='storage.download':return Receipt(effect.reference,'pending')
        args=json.loads(row['args']);target=self.vault.output.root/args['path']
        with self.vault.tx() as db:
            now=int(self.vault.clock());previous=db.execute('SELECT attempts,last_try FROM recovery_attempts WHERE task_id=?',(effect.opaque_handle,)).fetchone()
            attempts=previous['attempts'] if previous else 0
            last_try=previous['last_try'] if previous else 0
            if attempts>=3:return Receipt(effect.reference,'rejected',0,{'error':'STORED_FILE_UNAVAILABLE'})
            if attempts and now-last_try<8:return Receipt(effect.reference,'pending')
            db.execute('INSERT INTO recovery_attempts VALUES (?,?,?) ON CONFLICT(task_id) DO UPDATE SET attempts=attempts+1,last_try=excluded.last_try',(effect.opaque_handle,1,now))
        if target.exists() or target.is_symlink():
            with self.vault.guard:
                verified=self.vault.db.execute('SELECT * FROM verified_downloads WHERE operation=?',(effect.opaque_handle,)).fetchone()
            if not verified or verified['address']!=args['address'] or verified['path']!=args['path']:
                return Receipt(effect.reference,'rejected',0,{'error':'DOWNLOAD_OUTPUT_NOT_VERIFIED'})
            try:
                with self.vault.output.read(args['path'],verified['bytes']) as saved:
                    digest=hashlib.file_digest(saved,'sha256').hexdigest()
                if digest!=verified['plaintext_sha256']:raise Rejected('DOWNLOAD_OUTPUT_CONFLICT')
            except (Rejected,OSError):
                return Receipt(effect.reference,'rejected',0,{'error':'DOWNLOAD_OUTPUT_CONFLICT'})
            result={'address':args['address'],'path':args['path'],'bytes':verified['bytes'],'authenticated':True}
            self._save_result(effect.opaque_handle,result);return Receipt(effect.reference,'confirmed',0,result)
        try:result=self.vault.download(args['address'],args['path'],self.store,operation=effect.opaque_handle)
        except Rejected:return Receipt(effect.reference,'pending')
        self._save_result(effect.opaque_handle,result);return Receipt(effect.reference,'confirmed',0,result)
    def lookup(self,effect:Prepared)->Receipt:
        with self.vault.guard:
            row=self.vault.db.execute('SELECT * FROM effects WHERE task_id=?',(effect.opaque_handle,)).fetchone()
            # The catalogue may already have an upload commit when the parent
            # task lost its response. Reconcile it without re-uploading.
            uploaded=self.vault.db.execute('SELECT address,label,bytes FROM blobs WHERE operation=? AND state=?',(effect.opaque_handle,'stored')).fetchone()
        if row and row['result']:return Receipt(effect.reference,'confirmed',0,json.loads(row['result']))
        if row and row['skill']=='storage.upload' and uploaded:return Receipt(effect.reference,'confirmed',0,dict(uploaded))
        return Receipt(effect.reference,'pending')
