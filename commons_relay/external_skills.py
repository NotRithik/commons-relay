"""Owner-installed, zero-spend skill extensions isolated in subprocesses.

Extensions are opt-in executable programs pinned by SHA-256 in an operator-owned
extension root. They never run inside the Relay worker process and inherit no
ambient API keys. The protocol separates prepare, execute and lookup so a worker
restart cannot turn an ambiguous effect into a blind second execution.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
from .codec import Rejected,canonical,identifier
from .engine import Prepared,Receipt
from .schema import check_schema
from .skills import Skill,Quote
from .signing import protected_directory

MAX_MANIFEST=65536
MAX_RESPONSE=65536
MAX_TIMEOUT=60

@dataclass(frozen=True)
class ExtensionSpec:
    id:str
    description:str
    executable:Path
    executable_sha256:str
    timeout_seconds:int
    input_schema:dict
    public:bool=False
    def skill(self)->Skill:
        properties=self.input_schema['properties'];required=self.input_schema['required']
        return Skill(self.id,self.description,tuple(required),lambda _:Quote('LEZ-testnet',0),self.public,self.input_schema)

class ExternalSkills:
    def __init__(self,profile:Path):
        self.profile=profile;self.specs={}
        config=profile/'extensions.json'
        if not config.exists():return
        if config.is_symlink() or not config.is_file() or config.stat().st_size>MAX_MANIFEST or stat.S_IMODE(config.stat().st_mode)&0o077:raise Rejected('EXTENSION_CONFIGURATION_PERMISSIONS')
        root_raw=os.environ.get('COMMONS_RELAY_EXTENSION_ROOT')
        if not root_raw:raise Rejected('EXTENSION_ROOT_REQUIRED')
        root_candidate=Path(root_raw).expanduser().absolute()
        if root_candidate.is_symlink() or not root_candidate.is_dir():raise Rejected('EXTENSION_ROOT_INVALID')
        self.root=root_candidate.resolve(strict=True)
        value=json.loads(config.read_text())
        if not isinstance(value,dict) or set(value)!={'manifests'} or not isinstance(value['manifests'],list) or len(value['manifests'])>32:raise Rejected('EXTENSION_CONFIGURATION_INVALID')
        for name in value['manifests']:
            if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,120}[.]json',name):raise Rejected('EXTENSION_MANIFEST_NAME_INVALID')
            spec=self._manifest(name)
            if spec.id in self.specs:raise Rejected('DUPLICATE_EXTENSION_SKILL')
            self.specs[spec.id]=spec
    def _manifest(self,name:str)->ExtensionSpec:
        path=self.root/name
        if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_MANIFEST:raise Rejected('EXTENSION_MANIFEST_INVALID')
        value=json.loads(path.read_text())
        required={'schema','id','description','executable','executable_sha256','timeout_seconds','input_schema','public'}
        if not isinstance(value,dict) or set(value)!=required or value['schema']!=1:raise Rejected('EXTENSION_MANIFEST_INVALID')
        id=identifier(value['id'])
        if id.startswith(('storage.','messaging.','wallet.','program.','agent.','meta.')):raise Rejected('EXTENSION_RESERVED_NAMESPACE')
        if not isinstance(value['description'],str) or not 1<=len(value['description'])<=500:raise Rejected('EXTENSION_DESCRIPTION_INVALID')
        executable_name=value['executable']
        if not isinstance(executable_name,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,120}',executable_name):raise Rejected('EXTENSION_EXECUTABLE_NAME_INVALID')
        executable=self.root/executable_name
        if executable.is_symlink() or not executable.is_file() or not os.access(executable,os.X_OK):raise Rejected('EXTENSION_EXECUTABLE_INVALID')
        executable=executable.resolve(strict=True)
        if executable.parent!=self.root:raise Rejected('EXTENSION_EXECUTABLE_OUTSIDE_ROOT')
        expected=value['executable_sha256']
        if not isinstance(expected,str) or not re.fullmatch(r'[0-9a-f]{64}',expected):raise Rejected('EXTENSION_HASH_INVALID')
        actual=hashlib.sha256(executable.read_bytes()).hexdigest()
        if actual!=expected:raise Rejected('EXTENSION_HASH_MISMATCH')
        timeout=value['timeout_seconds']
        if type(timeout)is not int or not 1<=timeout<=MAX_TIMEOUT:raise Rejected('EXTENSION_TIMEOUT_INVALID')
        schema=value['input_schema'];check_schema(schema)
        if schema.get('type')!='object' or schema.get('additionalProperties') is not False or not isinstance(schema.get('properties'),dict) or not isinstance(schema.get('required'),list) or set(schema['required'])!=set(schema['properties']) or len(schema['required'])>32:raise Rejected('EXTENSION_SCHEMA_MUST_REQUIRE_ALL_FIELDS')
        if type(value['public'])is not bool:raise Rejected('EXTENSION_PUBLIC_FLAG_INVALID')
        return ExtensionSpec(id,value['description'],executable,expected,timeout,schema,value['public'])
    def skills(self)->list[Skill]:return [spec.skill() for spec in self.specs.values()]
    def has(self,name:str)->bool:return name in self.specs

class ExternalAdapter:
    def __init__(self,profile:Path,extensions:ExternalSkills,engine):
        self.profile=profile;self.extensions=extensions;self.engine=engine;self.runtime=protected_directory(profile/'extension-runtime')
        with engine.tx() as db:db.execute('''CREATE TABLE IF NOT EXISTS extension_effects(task_id TEXT PRIMARY KEY,skill TEXT NOT NULL,args TEXT NOT NULL,effect_id TEXT,result TEXT,state TEXT NOT NULL)''')
    def _row(self,id):
        with self.engine.tx() as db:row=db.execute('SELECT * FROM extension_effects WHERE task_id=?',(id,)).fetchone()
        if not row:raise Rejected('EXTENSION_EFFECT_MISSING')
        return dict(row)
    def _invoke(self,spec:ExtensionSpec,request:dict)->dict:
        scratch=protected_directory(self.runtime/spec.id.replace('/','_'))
        env={'PATH':'/opt/homebrew/bin:/usr/bin:/bin','HOME':str(scratch),'TMPDIR':str(scratch),'LANG':'C.UTF-8'}
        try:
            completed=subprocess.run([str(spec.executable)],input=canonical(request)+b'\n',stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,cwd=scratch,timeout=spec.timeout_seconds)
        except subprocess.TimeoutExpired:raise Rejected('EXTENSION_TIMEOUT') from None
        if completed.returncode!=0 or len(completed.stdout)>MAX_RESPONSE or completed.stderr and len(completed.stderr)>MAX_RESPONSE:raise Rejected('EXTENSION_PROCESS_FAILED')
        try:value=json.loads(completed.stdout)
        except Exception:raise Rejected('EXTENSION_RESPONSE_INVALID') from None
        if not isinstance(value,dict):raise Rejected('EXTENSION_RESPONSE_INVALID')
        canonical(value);return value
    def prepare(self,task):
        if task['skill'] not in self.extensions.specs:raise Rejected('EXTENSION_SKILL_UNAVAILABLE')
        if task['maximum_spend']!='0':raise Rejected('EXTENSION_SKILLS_CANNOT_SPEND')
        spec=self.extensions.specs[task['skill']];raw=canonical(task['arguments']).decode()
        with self.engine.tx() as db:
            old=db.execute('SELECT * FROM extension_effects WHERE task_id=?',(task['id'],)).fetchone()
            if old and (old['skill']!=task['skill'] or old['args']!=raw):raise Rejected('EXTENSION_EFFECT_REUSED')
            db.execute("INSERT OR IGNORE INTO extension_effects(task_id,skill,args,state) VALUES (?,?,?,'new')",(task['id'],task['skill'],raw))
        row=self._row(task['id'])
        if not row['effect_id']:
            result=self._invoke(spec,{'protocol':'commons-relay-extension/v1','phase':'prepare','task_id':task['id'],'arguments':task['arguments']})
            if set(result)!={'effect_id'}:raise Rejected('EXTENSION_PREPARE_RESPONSE_INVALID')
            effect=identifier(result['effect_id'])
            with self.engine.tx() as db:db.execute("UPDATE extension_effects SET effect_id=?,state='prepared' WHERE task_id=?",(effect,task['id']))
        else:effect=row['effect_id']
        return Prepared('extension:'+task['id'],0,task['id'])
    def broadcast(self,effect):
        row=self._row(effect.opaque_handle);spec=self.extensions.specs[row['skill']]
        if row['state'] in ['confirmed','rejected']:return
        if not row['effect_id']:raise Rejected('EXTENSION_NOT_PREPARED')
        result=self._invoke(spec,{'protocol':'commons-relay-extension/v1','phase':'execute','task_id':row['task_id'],'effect_id':row['effect_id'],'arguments':json.loads(row['args'])})
        self._record(row,result)
    def _record(self,row,result):
        if set(result)!={'state','result'} or result['state'] not in ['confirmed','pending','rejected'] or result['result'] is not None and not isinstance(result['result'],dict):raise Rejected('EXTENSION_EFFECT_RESPONSE_INVALID')
        with self.engine.tx() as db:db.execute('UPDATE extension_effects SET state=?,result=? WHERE task_id=?',(result['state'],canonical(result['result']).decode() if result['result'] is not None else None,row['task_id']))
    def lookup(self,effect):
        row=self._row(effect.opaque_handle);spec=self.extensions.specs[row['skill']]
        if row['state']=='pending':
            result=self._invoke(spec,{'protocol':'commons-relay-extension/v1','phase':'lookup','task_id':row['task_id'],'effect_id':row['effect_id'],'arguments':json.loads(row['args'])});self._record(row,result);row=self._row(effect.opaque_handle)
        if row['state']=='confirmed':return Receipt(effect.reference,'confirmed',0,json.loads(row['result']) if row['result'] else {})
        if row['state']=='rejected':return Receipt(effect.reference,'rejected',0,json.loads(row['result']) if row['result'] else {})
        return Receipt(effect.reference,'pending')
