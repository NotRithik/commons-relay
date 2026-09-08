"""Metadata and owner-authorized configuration without model privileges."""
from dataclasses import asdict
import json
from .codec import Rejected,canonical,amount
from .engine import Prepared,Receipt,Policy

class MetaAdapter:
    def __init__(self,service):
        self.service=service;self.engine=service.engine
        with self.engine.tx() as db:db.execute('CREATE TABLE IF NOT EXISTS meta_effects(task_id TEXT PRIMARY KEY,skill TEXT NOT NULL,args TEXT NOT NULL,result TEXT)')
    def prepare(self,task):
        if task['skill'] not in ['meta.skills','meta.status','meta.configure','agent.card']:raise Rejected('META_SKILL_NOT_REGISTERED')
        if task['maximum_spend']!='0':raise Rejected('META_OPERATION_CANNOT_SPEND')
        if task['skill']=='meta.configure':self.validate_config(self.configuration(task['arguments']))
        with self.engine.tx() as db:
            raw=canonical(task['arguments']).decode();row=db.execute('SELECT * FROM meta_effects WHERE task_id=?',(task['id'],)).fetchone()
            if row and (row['args']!=raw or row['skill']!=task['skill']):raise Rejected('META_EFFECT_REUSED')
            db.execute('INSERT OR IGNORE INTO meta_effects VALUES (?,?,?,NULL)',(task['id'],task['skill'],raw))
        return Prepared('meta:'+task['id'],0,task['id'])
    def configuration(self,args):
        if not isinstance(args,dict) or set(args)!={'key','value'} or not isinstance(args['key'],str):raise Rejected('INVALID_CONFIGURATION_ARGUMENTS')
        return {args['key']:args['value']}
    def validate_config(self,config):
        allowed={'name','owner_address','spending_limit','period_limit','hard_maximum','approval_ttl'}
        if not isinstance(config,dict) or len(config)!=1 or set(config)-allowed:raise Rejected('CONFIGURATION_FIELD_NOT_ALLOWED')
        if 'name' in config and (not isinstance(config['name'],str) or not 1<=len(config['name'])<=100):raise Rejected('INVALID_AGENT_NAME')
        if 'owner_address' in config:self.service.get_messaging().mailbox.get_contact(config['owner_address'])
        updated=asdict(self.engine.policy)
        for external,key in [('spending_limit','per_transaction'),('period_limit','per_period'),('hard_maximum','hard_maximum')]:
            if external in config:updated[key]=amount(config[external])
        if 'approval_ttl' in config:updated['approval_ttl']=config['approval_ttl']
        updated['version']+=1
        return Policy(**updated)
    def broadcast(self,effect):
        with self.engine.tx() as db:row=db.execute('SELECT * FROM meta_effects WHERE task_id=?',(effect.opaque_handle,)).fetchone()
        if not row:raise Rejected('META_EFFECT_NOT_PREPARED')
        if row['result']:return
        args=json.loads(row['args'])
        if row['skill']=='meta.skills':result={'skills':self.engine.registry.describe()}
        elif row['skill']=='meta.status':
            from .status_views import observed_status
            result=observed_status(self.service)
        elif row['skill']=='agent.card':result=self.service.get_agent_protocol().card()
        else:
            config=self.configuration(args);policy=self.validate_config(config)
            result=self.engine.update_policy_from_task(effect.opaque_handle,policy,config)
            if 'owner_address' in config and self.service.controller:self.service.controller.owner_address=config['owner_address']
        with self.engine.tx() as db:db.execute('UPDATE meta_effects SET result=? WHERE task_id=?',(canonical(result).decode(),effect.opaque_handle))
    def lookup(self,effect):
        with self.engine.tx() as db:row=db.execute('SELECT result FROM meta_effects WHERE task_id=?',(effect.opaque_handle,)).fetchone()
        return Receipt(effect.reference,'confirmed',0,json.loads(row[0])) if row and row[0] else Receipt(effect.reference,'pending')
