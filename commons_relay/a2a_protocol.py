"""Durable A2A task server and client bookkeeping over encrypted Logos messages.

Payments are an advertised extension. A payment claim is checked against the
actual private receiver commitment in the stated transaction, not an asserted
hash, a model response, or a balance number supplied by the peer.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import re
from pathlib import Path
import secrets
import threading
import time
from .codec import Rejected,canonical,parse,digest,identifier,amount,b64
from .signing import key_id
from .service_identity import is_public_address,contact_from_card
from .a2a_types import jcs as jcs_card_bytes, PAYMENT_EXTENSION,BINDING_EXTENSION,TERMINAL,sign_card,verify_card,task_document

class RpcError(Exception):
    def __init__(self,code,message):super().__init__(message);self.code=code;self.message=message

class AgentProtocol:
    def __init__(self,service):
        self.service=service;self.engine=service.engine;self.runtime=service.get_messaging();self.mailbox=self.runtime.mailbox
        self.root=service.root;self.wallet=service.get_wallet();self.guard=threading.RLock();self.card_store_guard=threading.Lock()
        self.refund_worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix='a2a-refund-io');self.refund_future=None
        self.discovery_worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix='a2a-discovery-io');self.discovery_future=None;self.discovery_error=None
        self.config=self.load_config();self.exports=set(self.config['exports']);self.last_announce=0
        with self.engine.tx() as db:
            db.executescript('''
             CREATE TABLE IF NOT EXISTS a2a_tasks(id TEXT PRIMARY KEY,context_id TEXT NOT NULL,peer TEXT NOT NULL,message_id TEXT NOT NULL,intent_hash TEXT NOT NULL,skill TEXT NOT NULL,args TEXT NOT NULL,state TEXT NOT NULL,sequence INTEGER NOT NULL,created INTEGER NOT NULL,updated INTEGER NOT NULL,deadline INTEGER NOT NULL,quote TEXT,payment_state TEXT NOT NULL,payment_hash TEXT UNIQUE,refund_address TEXT,refund_hash TEXT,local_task TEXT,result TEXT,error TEXT,UNIQUE(peer,message_id));
             CREATE TABLE IF NOT EXISTS a2a_events(task_id TEXT NOT NULL,sequence INTEGER NOT NULL,event TEXT NOT NULL,PRIMARY KEY(task_id,sequence));
             CREATE TABLE IF NOT EXISTS a2a_subscriptions(task_id TEXT NOT NULL,peer TEXT NOT NULL,rpc_id TEXT NOT NULL,last_sequence INTEGER NOT NULL,PRIMARY KEY(task_id,peer,rpc_id));
             CREATE TABLE IF NOT EXISTS a2a_rpc_requests(peer TEXT NOT NULL,id TEXT NOT NULL,request_hash TEXT NOT NULL,request TEXT NOT NULL,response TEXT,PRIMARY KEY(peer,id));
             CREATE TABLE IF NOT EXISTS a2a_client_requests(id TEXT PRIMARY KEY,peer TEXT NOT NULL,request TEXT NOT NULL,response TEXT,created INTEGER NOT NULL);
             CREATE TABLE IF NOT EXISTS a2a_client_events(request_id TEXT NOT NULL,sequence INTEGER NOT NULL,response TEXT NOT NULL,PRIMARY KEY(request_id,sequence));
             CREATE TABLE IF NOT EXISTS a2a_remote_tasks(peer TEXT NOT NULL,id TEXT NOT NULL,document TEXT NOT NULL,sequence INTEGER NOT NULL,PRIMARY KEY(peer,id));
             CREATE TABLE IF NOT EXISTS a2a_cards(address TEXT PRIMARY KEY,card TEXT NOT NULL,received INTEGER NOT NULL);
             CREATE TABLE IF NOT EXISTS a2a_refunds(task_id TEXT PRIMARY KEY,intent TEXT NOT NULL,state TEXT NOT NULL,tx_hash TEXT,error TEXT,next_try INTEGER NOT NULL DEFAULT 0);
            ''')
        with self.engine.tx() as db:
            if 'refund_proof' not in {r[1] for r in db.execute('PRAGMA table_info(a2a_tasks)')}:db.execute('ALTER TABLE a2a_tasks ADD COLUMN refund_proof TEXT')
        from .discovery import PublicDiscovery
        self.discovery=PublicDiscovery(self)
    def load_config(self):
        path=self.root/'services.json'
        if not path.is_file():return {'name':'Commons Relay','description':'Owner-controlled Logos agent','exports':{},'discovery_topic':'commons','public':False}
        if path.is_symlink() or path.stat().st_size>20000 or path.stat().st_mode&0o077:raise Rejected('SERVICE_CONFIGURATION_PERMISSIONS')
        return self.validate_config(parse(path.read_bytes()))
    def validate_config(self,cfg):
        required={'name','description','exports','discovery_topic'}
        if not isinstance(cfg,dict) or not required<=set(cfg) or set(cfg)-required-{'public','allow_public_payments'} or not isinstance(cfg['exports'],dict) or len(cfg['exports'])>32:raise Rejected('INVALID_SERVICE_CONFIGURATION')
        if type(cfg.get('public',False))is not bool or type(cfg.get('allow_public_payments',False))is not bool:raise Rejected('INVALID_PUBLIC_SERVICE_SETTING')
        for key,maximum in [('name',100),('description',1000),('discovery_topic',128)]:
            if not isinstance(cfg[key],str) or not 1<=len(cfg[key])<=maximum:raise Rejected('INVALID_SERVICE_CONFIGURATION')
        for name,entry in cfg['exports'].items():
            skill=self.engine.registry.get(name)
            if not isinstance(entry,dict) or set(entry)!={'price','description'} or not isinstance(entry['description'],str) or not 1<=len(entry['description'])<=500:raise Rejected('INVALID_SERVICE_PRICE')
            if amount(entry['price'])>self.engine.policy.hard_maximum:raise Rejected('SERVICE_PRICE_EXCEEDS_POLICY')
            if name in ['wallet.send','wallet.initialize_public','program.call','program.deploy','meta.configure']:raise Rejected('UNSAFE_REMOTE_SERVICE_EXPORT')
            if cfg.get('public',False):
                if self.service.extensions.has(name):
                    if not self.service.extensions.specs[name].public:raise Rejected('EXTENSION_NOT_APPROVED_FOR_PUBLIC_SERVICE')
                elif name not in {'meta.skills','program.query'}:
                    raise Rejected('PRIVATE_SKILL_CANNOT_BE_PUBLISHED')
        return cfg
    def card(self,public=False):
        config=self.config;exports=set(config['exports'])
        schemas={s['id']:s for s in self.engine.registry.describe()};contact=self.mailbox.public_contact() if public else self.mailbox.contact()
        identity=self.root/'vault/identity-public.json'
        root_info=parse(identity.read_bytes()) if identity.is_file() else {'address':contact.address}
        name=config['name']
        with self.engine.tx() as db:
            row=db.execute("SELECT value FROM metadata WHERE key='runtime:name'").fetchone()
        if row and not public:
            stored=json.loads(row['value'])
            if isinstance(stored,str) and 1<=len(stored)<=100:
                name=stored
        card={'name':name,'description':config['description'],'version':'0.1.0',
          'supportedInterfaces':[{'url':'logos://'+contact.address,'protocolBinding':'LOGOS-MESSAGING','protocolVersion':'1.0'}],
          'capabilities':{'streaming':True,'pushNotifications':False,'extendedAgentCard':True,'extensions':[
            {'uri':BINDING_EXTENSION,'required':True,'description':'Signed, recipient-encrypted Logos Messaging transport.',
             'params':{'address':contact.address,'signingPublicKey':b64(contact.signing_key),'encryptionPublicKey':b64(contact.box_key),'rootNpk':root_info.get('root_npk'),'discoveryTopic':config['discovery_topic']}},
            {'uri':PAYMENT_EXTENSION,'required':any(amount(x['price']) for x in config['exports'].values()),
             'description':'Task-bound LEZ payment. Private by default; explicitly chosen public payments require payer proof.',
             'params':{'network':'https://testnet.lez.logos.co/','asset':'LEZ-testnet','paymentModes':['private','public'] if config.get('allow_public_payments',False) else ['private'],'prices':{k:v['price'] for k,v in config['exports'].items()},
                       'inputSchemas':{k:schemas[k]['input_schema'] for k in exports},
                       'outputSchemas':{k:schemas[k]['output_schema'] for k in exports}}}]},
          'defaultInputModes':['application/json'],'defaultOutputModes':['application/json'],
          'skills':[{'id':name,'name':name,'description':cfg['description'],'tags':[name.split('.')[0]],'inputModes':['application/json'],'outputModes':['application/json']} for name,cfg in config['exports'].items()]}
        return sign_card(card,self.mailbox.vault.signing_private,contact.signing_key,self.mailbox.vault.signer)
    def discovery_topic(self,name):
        if not isinstance(name,str) or not 1<=len(name)<=128:raise Rejected('INVALID_DISCOVERY_TOPIC')
        return '/commons-relay/1/discovery-'+hashlib.sha256(name.encode()).hexdigest()[:32]+'/json'
    def publish_storage_card(self,public=False,*,snapshot=None):
        # Serialize the fixed public file/cache, without holding the control lock
        # across Storage initialization/upload. A slow upload cannot freeze owners.
        with self.card_store_guard:
            return self._publish_storage_card(public,snapshot)
    def _publish_storage_card(self,public,snapshot):
        card=snapshot if snapshot is not None else self.card(public=public);fingerprint=digest(card)
        cache_key='public-agent-card-storage' if public else 'agent-card-storage'
        with self.engine.tx() as db:row=db.execute('SELECT value FROM metadata WHERE key=?',(cache_key,)).fetchone()
        if row:
            cached=json.loads(row[0])
            if cached['hash']==fingerprint:return cached['cid']
        self.service.storage_adapter();self.service.store.initialize()
        directory=self.root/'public';directory.mkdir(mode=0o700,exist_ok=True)
        path=directory/('public-agent-card.json' if public else 'agent-card.json')
        if path.is_symlink():raise Rejected('SYMLINK_PUBLIC_CARD')
        temporary=path.with_suffix('.tmp');temporary.write_bytes(jcs_card_bytes(card));temporary.chmod(0o600);temporary.replace(path)
        result=self.service.bridge.call('storage.publish-card',{'path':str(path)})
        address=result.get('address') if isinstance(result,dict) else None
        if not isinstance(address,str) or not 20<=len(address)<=180:raise Rejected('CARD_STORAGE_RECEIPT_MISSING')
        with self.engine.tx() as db:db.execute('INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(cache_key,canonical({'hash':fingerprint,'cid':address}).decode()))
        return address
    def discover(self,name,wait_seconds=0):
        self.runtime.start()
        self.discovery.query(name)
        if name==self.config['discovery_topic']:
            if self.config.get('public',False):self.discovery.pending=True
            # Keep the original pinned address for existing task/payment journals.
            # It is only shared with contacts, never introduced to strangers.
            epoch=int(self.engine.clock())//60
            for peer in self.mailbox.contacts():
                self.mailbox.enqueue(peer['address'],'agent-card',self.card(),message_id='card-'+hashlib.sha256((peer['address']+str(epoch)+digest(self.card())).encode()).hexdigest(),ttl=300)
            self.last_announce=time.monotonic()
        if wait_seconds:
            deadline=time.monotonic()+min(8,max(0,wait_seconds))
            while time.monotonic()<deadline:
                time.sleep(.2)
        return self.cards(name)
    def remember_card(self,peer,card):
        if is_public_address(peer):
            contact=contact_from_card(card)
            if contact.address!=peer:raise Rejected('CARD_ADDRESS_MISMATCH')
            value=verify_card(card,contact.signing_key,self.mailbox.vault.signer)
            self.mailbox.remember_service_contact(contact)
        else:
            contact=self.mailbox.get_contact(peer);value=verify_card(card,contact.signing_key,self.mailbox.vault.signer)
        if not any(x['url']=='logos://'+peer for x in value['supportedInterfaces']):raise Rejected('CARD_ADDRESS_MISMATCH')
        with self.engine.tx() as db:
            if not db.execute('SELECT 1 FROM a2a_cards WHERE address=?',(peer,)).fetchone() and db.execute('SELECT COUNT(*) FROM a2a_cards').fetchone()[0]>=768:
                db.execute('DELETE FROM a2a_cards WHERE received<?',(int(self.engine.clock())-86400,))
                if db.execute('SELECT COUNT(*) FROM a2a_cards').fetchone()[0]>=768:raise Rejected('AGENT_CARD_CACHE_FULL')
            db.execute('INSERT INTO a2a_cards VALUES (?,?,?) ON CONFLICT(address) DO UPDATE SET card=excluded.card,received=excluded.received',(peer,canonical(card).decode(),int(self.engine.clock())))
        return value
    def verified_card(self,address):
        contact=self.mailbox.get_contact(address)
        with self.engine.tx() as db:row=db.execute('SELECT card,received FROM a2a_cards WHERE address=?',(address,)).fetchone()
        if not row:raise Rejected('AGENT_CARD_REQUIRED_FOR_PRICE')
        if is_public_address(address) and int(self.engine.clock())-row['received']>300:raise Rejected('SERVICE_LISTING_EXPIRED_DISCOVER_AGAIN')
        return verify_card(json.loads(row['card']),contact.signing_key,self.mailbox.vault.signer)
    def cards(self,name=None):
        name=name or self.config['discovery_topic']
        public=self.discovery.cards(name)
        with self.engine.tx() as db:rows=db.execute('SELECT * FROM a2a_cards ORDER BY address').fetchall()
        pinned=[]
        for row in rows:
            if is_public_address(row['address']) or int(self.engine.clock())-row['received']>300:continue
            card=json.loads(row['card'])
            binding=next((e for e in card.get('capabilities',{}).get('extensions',[]) if e.get('uri')==BINDING_EXTENSION),{})
            if binding.get('params',{}).get('discoveryTopic')==name:
                pinned.append({'address':row['address'],'card':card,'received':row['received'],'topic':name,'trust':'owner-contact','identityVerified':True})
        # Prefer a live public listing over its legacy address-book alias.
        # Compare authenticated keys, never an advertised name or wallet claim.
        public_keys=set()
        for entry in public:
            contact=contact_from_card(entry['card'])
            public_keys.add((contact.signing_key,contact.box_key))
        unique=[]
        for entry in pinned:
            contact=self.mailbox.get_contact(entry['address'])
            if (contact.signing_key,contact.box_key) not in public_keys:unique.append(entry)
        return public+unique
    def request(self,peer,method,params,id=None):
        self.mailbox.get_contact(peer);id=identifier(id or secrets.token_hex(16))
        rpc={'jsonrpc':'2.0','id':id,'method':method,'params':params}
        with self.engine.tx() as db:
            old=db.execute('SELECT * FROM a2a_client_requests WHERE id=?',(id,)).fetchone()
            if old and (old['peer']!=peer or old['request']!=canonical(rpc).decode()):raise Rejected('A2A_REQUEST_ID_REUSED')
            db.execute('INSERT OR IGNORE INTO a2a_client_requests VALUES (?,?,?,?,?)',(id,peer,canonical(rpc).decode(),None,int(self.engine.clock())))
        self.mailbox.enqueue(peer,'a2a-request',rpc,message_id='rpc-'+hashlib.sha256((peer+'\0'+id).encode()).hexdigest(),ttl=86400)
        return id
    def response(self,id):
        with self.engine.tx() as db:row=db.execute('SELECT response FROM a2a_client_requests WHERE id=?',(id,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None
    def await_response(self,id,timeout=45):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            reply=self.response(id)
            if reply:
                if 'error' in reply:raise Rejected('A2A_REMOTE_ERROR_'+str(abs(reply['error']['code'])))
                return reply['result']
            time.sleep(.25)
        raise Rejected('A2A_RESPONSE_PENDING')
    def handle_message(self,message):
        peer=message['sender'];payload=message['payload']
        if message['kind']=='agent-card':self.remember_card(peer,payload);return
        if message['kind']=='a2a-request':return self.handle_request(peer,payload)
        if message['kind']=='a2a-response':
            if not isinstance(payload,dict) or payload.get('jsonrpc')!='2.0' or not isinstance(payload.get('id'),str):raise Rejected('INVALID_A2A_RESPONSE')
            with self.engine.tx() as db:
                row=db.execute('SELECT * FROM a2a_client_requests WHERE id=?',(payload['id'],)).fetchone()
                if not row or row['peer']!=peer:raise Rejected('UNREQUESTED_A2A_RESPONSE')
                if row['response'] and row['response']!=canonical(payload).decode():raise Rejected('A2A_RESPONSE_CHANGED')
                db.execute('UPDATE a2a_client_requests SET response=? WHERE id=?',(canonical(payload).decode(),payload['id']))
            result=payload.get('result',{})
            if isinstance(result,dict):
                task=result.get('task') if 'task' in result else result if 'status' in result and 'id' in result else None
                if task:self.remember_task(peer,task)
            return
        if message['kind']=='a2a-event':
            if not isinstance(payload,dict) or set(payload)!={'requestId','sequence','response'}:raise Rejected('INVALID_A2A_EVENT')
            with self.engine.tx() as db:request=db.execute('SELECT * FROM a2a_client_requests WHERE id=?',(payload['requestId'],)).fetchone()
            if not request or request['peer']!=peer:raise Rejected('UNSUBSCRIBED_A2A_EVENT')
            submitted=json.loads(request['request'])
            if submitted['method'] not in ('SubscribeToTask','SendStreamingMessage'):raise Rejected('UNSUBSCRIBED_A2A_EVENT')
            if type(payload['sequence'])is not int or not 1<=payload['sequence']<=2**53-1:raise Rejected('INVALID_A2A_SEQUENCE')
            response=payload['response']
            if not isinstance(response,dict) or len(response)!=1 or not set(response)<= {'task','statusUpdate','artifactUpdate'}:
                raise Rejected('INVALID_A2A_EVENT')
            if submitted['method']=='SubscribeToTask':expected_task=submitted['params']['id']
            else:
                initial=json.loads(request['response']) if request['response'] else {}
                expected_task=initial.get('result',{}).get('task',{}).get('id')
            content=next(iter(response.values()))
            if not isinstance(content,dict) or (content.get('id') if 'task' in response else content.get('taskId'))!=expected_task:
                raise Rejected('A2A_STREAM_TASK_MISMATCH')
            known=self.remote_task(peer,expected_task)
            if known and content.get('contextId')!=known['contextId']:raise Rejected('A2A_STREAM_CONTEXT_MISMATCH')
            with self.engine.tx() as db:
                encoded=canonical(response).decode()
                previous=db.execute('SELECT response FROM a2a_client_events WHERE request_id=? AND sequence=?',(payload['requestId'],payload['sequence'])).fetchone()
                if previous and previous['response']!=encoded:raise Rejected('A2A_STREAM_EVENT_CHANGED')
                if not previous:
                    if db.execute('SELECT COUNT(*) FROM a2a_client_events').fetchone()[0]>=50000:raise Rejected('A2A_CLIENT_EVENT_LIMIT')
                    db.execute('INSERT INTO a2a_client_events VALUES (?,?,?)',(payload['requestId'],payload['sequence'],encoded))
            response=payload['response']
            if 'task' in response:self.remember_task(peer,response['task'])
            elif 'statusUpdate' in response:
                update=response['statusUpdate'];task=self.remote_task(peer,update['taskId'])
                if task:
                    task['status']=update['status'];task.setdefault('metadata',{}).update(update.get('metadata',{}));task['metadata'][BINDING_EXTENSION]={'sequence':str(payload['sequence'])};self.remember_task(peer,task)
            elif 'artifactUpdate' in response:
                update=response['artifactUpdate'];task=self.remote_task(peer,update['taskId'])
                if task:
                    task['artifacts']=[update['artifact']]
                    previous=int(task.get('metadata',{}).get(BINDING_EXTENSION,{}).get('sequence','0'))
                    task.setdefault('metadata',{})[BINDING_EXTENSION]={'sequence':str(max(previous,int(payload['sequence'])))}
                    self.remember_task(peer,task,allow_same=True)
            else:raise Rejected('UNKNOWN_A2A_STREAM_RESPONSE')
    def remember_task(self,peer,task,allow_same=False):
        if not isinstance(task,dict) or not {'id','contextId','status'}<=set(task) or not isinstance(task['status'],dict):raise Rejected('INVALID_A2A_TASK')
        identifier(task['id']);seq=int(task.get('metadata',{}).get(BINDING_EXTENSION,{}).get('sequence','0'))
        if seq<0 or seq>2**53-1:raise Rejected('INVALID_A2A_SEQUENCE')
        with self.engine.tx() as db:
            old=db.execute('SELECT * FROM a2a_remote_tasks WHERE peer=? AND id=?',(peer,task['id'])).fetchone()
            if old and seq<old['sequence']:return
            if old and seq==old['sequence'] and old['document']!=canonical(task).decode() and not allow_same:return
            db.execute('INSERT INTO a2a_remote_tasks VALUES (?,?,?,?) ON CONFLICT(peer,id) DO UPDATE SET document=excluded.document,sequence=excluded.sequence',(peer,task['id'],canonical(task).decode(),seq))
    def remote_task(self,peer,id):
        with self.engine.tx() as db:row=db.execute('SELECT document FROM a2a_remote_tasks WHERE peer=? AND id=?',(peer,id)).fetchone()
        return json.loads(row[0]) if row else None
    def _row(self,id,peer=None):
        with self.engine.tx() as db:row=db.execute('SELECT * FROM a2a_tasks WHERE id=?',(id,)).fetchone()
        if not row or peer is not None and row['peer']!=peer:raise RpcError(-32001,'TaskNotFoundError')
        return dict(row)
    def document(self,id,peer=None):return task_document(self._row(id,peer),int(self.engine.clock()))
    def _set(self,id,**updates):
        allowed={'state','quote','payment_state','payment_hash','refund_hash','refund_proof','local_task','result','error'}
        if set(updates)-allowed:raise Rejected('INVALID_A2A_UPDATE')
        with self.engine.tx() as db:
            row=db.execute('SELECT * FROM a2a_tasks WHERE id=?',(id,)).fetchone()
            if not row:raise Rejected('A2A_TASK_MISSING')
            seq=row['sequence']+1;now=int(self.engine.clock())
            assignments=','.join(k+'=?' for k in updates)+',sequence=?,updated=?'
            db.execute('UPDATE a2a_tasks SET '+assignments+' WHERE id=?',(*updates.values(),seq,now,id))
            current=dict(db.execute('SELECT * FROM a2a_tasks WHERE id=?',(id,)).fetchone());doc=task_document(current,now)
            if 'result' in updates and updates['result'] is not None:
                artifact=doc['artifacts'][0]
                db.execute('INSERT INTO a2a_events VALUES (?,?,?)',(id,seq,canonical({'artifactUpdate':{'taskId':id,'contextId':current['context_id'],'artifact':artifact,'append':False,'lastChunk':True}}).decode()))
                seq+=1;db.execute('UPDATE a2a_tasks SET sequence=? WHERE id=?',(seq,id));doc['metadata'][BINDING_EXTENSION]['sequence']=str(seq)
            event={'statusUpdate':{'taskId':id,'contextId':current['context_id'],'status':doc['status'],'metadata':doc.get('metadata',{})}}
            db.execute('INSERT INTO a2a_events VALUES (?,?,?)',(id,seq,canonical(event).decode()))
        return doc
    def handle_request(self,peer,rpc):
        if not isinstance(rpc,dict) or set(rpc)!={'jsonrpc','id','method','params'} or rpc['jsonrpc']!='2.0' or not isinstance(rpc['params'],dict):raise Rejected('INVALID_A2A_RPC')
        rid=identifier(rpc['id']);fingerprint=digest(rpc)
        with self.engine.tx() as db:
            old=db.execute('SELECT * FROM a2a_rpc_requests WHERE peer=? AND id=?',(peer,rid)).fetchone()
            if old and old['request_hash']!=fingerprint:raise Rejected('A2A_RPC_ID_REUSED')
            if old and old['response']:response=json.loads(old['response'])
            else:response=None
            if not old:
                if db.execute('SELECT COUNT(*) FROM a2a_rpc_requests').fetchone()[0]>=10000:raise Rejected('A2A_RPC_LIMIT')
                db.execute('INSERT INTO a2a_rpc_requests VALUES (?,?,?,?,NULL)',(peer,rid,fingerprint,canonical(rpc).decode()))
        if response is None:
            try:
                result=self.dispatch(peer,rpc['method'],rpc['params'],rid)
                if result is None:return
                response={'jsonrpc':'2.0','id':rid,'result':result}
            except RpcError as error:response={'jsonrpc':'2.0','id':rid,'error':{'code':error.code,'message':error.message}}
            except Rejected:response={'jsonrpc':'2.0','id':rid,'error':{'code':-32602,'message':'InvalidParamsError'}}
            with self.engine.tx() as db:db.execute('UPDATE a2a_rpc_requests SET response=? WHERE peer=? AND id=?',(canonical(response).decode(),peer,rid))
        self.mailbox.enqueue(peer,'a2a-response',response,message_id='rpc-result-'+hashlib.sha256((peer+'\0'+rid).encode()).hexdigest(),ttl=86400)
        return response
    def send_result(self,id,peer,config):
        row=self._row(id,peer)
        if config.get('returnImmediately') is True or row['state'] in TERMINAL|{'input-required','auth-required'}:return {'task':self.document(id,peer)}
        return None
    def dispatch(self,peer,method,params,rid):
        if method in ('CreateTaskPushNotificationConfig','GetTaskPushNotificationConfig','ListTaskPushNotificationConfigs','DeleteTaskPushNotificationConfig'):
            raise RpcError(-32003,'PushNotificationNotSupportedError')
        if 'tenant' in params:
            if params['tenant']!='':raise RpcError(-32602,'InvalidParamsError')
            params={key:value for key,value in params.items() if key!='tenant'}
        if method=='ListTasks':
            from .a2a_listing import list_tasks
            return list_tasks(self,peer,params)
        if method=='SendStreamingMessage':
            config=params.get('configuration',{})
            if not isinstance(config,dict):raise RpcError(-32602,'InvalidParamsError')
            if 'returnImmediately' in config and type(config['returnImmediately'])is not bool:raise RpcError(-32602,'InvalidParamsError')
            immediate={**params,'configuration':{**config,'returnImmediately':True}}
            result=self.send_message(peer,immediate)
            row=self._row(result['task']['id'],peer)
            if row['state'] not in TERMINAL:self.subscribe(row,peer,rid)
            return result
        if method=='GetExtendedAgentCard' and not params:return self.card(public=is_public_address(peer))
        if method=='SendMessage':return self.send_message(peer,params)
        if method=='GetTask' and set(params)<={'id','historyLength'} and isinstance(params.get('id'),str):
            if 'historyLength' in params and (type(params['historyLength'])is not int or not 0<=params['historyLength']<=2147483647):raise RpcError(-32602,'InvalidParamsError')
            return self.document(params['id'],peer)
        if method=='SubscribeToTask' and set(params)=={'id'}:
            row=self._row(params['id'],peer)
            if row['state'] in TERMINAL:raise RpcError(-32004,'UnsupportedOperationError')
            self.subscribe(row,peer,rid)
            return {'task':self.document(row['id'],peer)}
        if method=='CancelTask' and set(params)=={'id'}:
            row=self._row(params['id'],peer)
            if row['state'] in TERMINAL:raise RpcError(-32002,'TaskNotCancelableError')
            if row['local_task']:
                local=self.engine.get(row['local_task'])
                if local['state'] not in ['submitted','input-required']:raise RpcError(-32002,'TaskNotCancelableError')
                contact=self.mailbox.get_contact(peer);self.engine.cancel(row['local_task'],key_id(contact.signing_key))
            self._set(row['id'],state='canceled',error='Task canceled before completion.')
            if row['payment_state']=='paid':self.queue_refund(row['id'])
            return self.document(row['id'],peer)
        raise RpcError(-32601,'MethodNotFoundError')
    def subscribe(self,row,peer,rid):
        with self.engine.tx() as db:
            old=db.execute('SELECT 1 FROM a2a_subscriptions WHERE task_id=? AND peer=? AND rpc_id=?',(row['id'],peer,rid)).fetchone()
            if old is None and db.execute('SELECT count(*) FROM a2a_subscriptions').fetchone()[0]>=1000:raise Rejected('A2A_SUBSCRIPTION_LIMIT')
            db.execute('INSERT OR IGNORE INTO a2a_subscriptions VALUES (?,?,?,?)',(row['id'],peer,rid,row['sequence']))
    def send_message(self,peer,params):
        if set(params)-{'message','configuration','metadata'} or not isinstance(params.get('message'),dict):raise RpcError(-32602,'InvalidParamsError')
        message=params['message'];required={'messageId','role','parts'}
        if not required<=set(message) or set(message)-required-{'contextId','taskId','metadata','extensions'} or message['role']!='ROLE_USER' or not isinstance(message['parts'],list) or len(message['parts'])!=1:raise RpcError(-32602,'InvalidParamsError')
        identifier(message['messageId']);part=message['parts'][0]
        if not isinstance(part,dict) or not isinstance(part.get('data'),dict) or set(part)-{'data','mediaType'}:raise RpcError(-32602,'InvalidParamsError')
        data=part['data'];cfg=params.get('configuration',{})
        if not isinstance(cfg,dict) or set(cfg)-{'acceptedOutputModes','historyLength','returnImmediately'}:raise RpcError(-32602,'InvalidParamsError')
        if 'returnImmediately' in cfg and type(cfg['returnImmediately'])is not bool:raise RpcError(-32602,'InvalidParamsError')
        if 'historyLength' in cfg and (type(cfg['historyLength'])is not int or not 0<=cfg['historyLength']<=2147483647):raise RpcError(-32602,'InvalidParamsError')
        if 'acceptedOutputModes' in cfg:
            modes=cfg['acceptedOutputModes']
            if not isinstance(modes,list) or not modes or any(not isinstance(mode,str) for mode in modes):raise RpcError(-32602,'InvalidParamsError')
            if 'application/json' not in modes:raise RpcError(-32005,'ContentTypeNotSupportedError')
        # Payment negotiation reaches INPUT_REQUIRED immediately, so it conforms
        # to both blocking and returnImmediately SendMessage configurations.
        if 'taskId' in message:
            row=self._row(message['taskId'],peer)
            if message.get('contextId',row['context_id'])!=row['context_id']:raise RpcError(-32602,'ContextMismatchError')
            if set(data)!={'payment'} or not isinstance(data['payment'],dict) or set(data['payment']) not in ({'quoteId','transactionHash'},{'quoteId','transactionHash','payerProof'}):raise RpcError(-32602,'InvalidPaymentExtension')
            quote=json.loads(row['quote']);payment=data['payment']
            if payment['quoteId']!=quote['id'] or not isinstance(payment['transactionHash'],str) or len(payment['transactionHash'])!=64:raise RpcError(-32602,'PaymentQuoteMismatch')
            self.confirm_payment(row,payment['transactionHash'],payment.get('payerProof'))
            return self.send_result(row['id'],peer,cfg)
        if set(data) not in ({'skill','arguments','refundAddress'},{'skill','arguments','refundAddress','paymentMode'}) or not isinstance(data['arguments'],dict):raise RpcError(-32602,'InvalidTaskRequest')
        mode=data.get('paymentMode','private')
        if mode not in ('private','public') or mode=='public' and not self.config.get('allow_public_payments',False):raise RpcError(-32602,'PaymentModeNotSupported')
        skill=data['skill']
        if skill not in self.exports:raise RpcError(-32005,'UnsupportedSkillError')
        if is_public_address(peer) and not self.config.get('public',False):raise RpcError(-32005,'PublicServiceNotEnabled')
        quote_policy=self.engine.registry.get(skill).validate(data['arguments'])
        if quote_policy.maximum!=0:raise RpcError(-32005,'ServiceCannotSpendOwnerFunds')
        price=amount(self.config['exports'][skill]['price'])
        refund=data['refundAddress']
        if price or refund is not None:
            self.validate_public_receiver(refund) if mode=='public' else self.validate_receiver(refund)
        fingerprint=digest(data)
        with self.engine.tx() as db:old=db.execute('SELECT * FROM a2a_tasks WHERE peer=? AND message_id=?',(peer,message['messageId'])).fetchone()
        if old:
            if old['intent_hash']!=fingerprint:raise RpcError(-32602,'MessageIdReusedWithDifferentIntent')
            return self.send_result(old['id'],peer,cfg)
        now=int(self.engine.clock());task=secrets.token_hex(16);context=message.get('contextId',secrets.token_hex(16));identifier(context)
        price=amount(self.config['exports'][skill]['price']);receiver=(self.public_receiver() if mode=='public' else self.fresh_receiver()) if price else None
        quote={'id':'quote-'+task,'taskId':task,'contextId':context,'provider':self.mailbox.local_address_for(peer),'client':peer,'skill':skill,
               'argumentsHash':digest(data['arguments']),'asset':'LEZ-testnet','network':'https://testnet.lez.logos.co/','amount':str(price),
               'recipient':receiver,'expiresAt':now+7200,'refund':'full before successful fulfillment','refundAddressHash':digest(refund)}
        if mode=='public':quote['paymentMode']='public'
        with self.engine.tx() as db:
            if db.execute("SELECT COUNT(*) FROM a2a_tasks WHERE state NOT IN ('completed','failed','canceled','rejected')").fetchone()[0]>=100:raise Rejected('A2A_TASK_LIMIT')
            db.execute('''INSERT INTO a2a_tasks(id,context_id,peer,message_id,intent_hash,skill,args,state,sequence,created,updated,deadline,quote,payment_state,refund_address) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
              (task,context,peer,message['messageId'],fingerprint,skill,canonical(data['arguments']).decode(),'input-required',1,now,now,now+7200,canonical(quote).decode(),'unpaid' if price else 'not-required',canonical(refund).decode()))
        if price==0:self.start_service(task)
        return self.send_result(task,peer,cfg)
    def validate_receiver(self,value):
        if not isinstance(value,dict) or set(value)!={'account_id','npk','vpk_borsh','identifier'}:raise Rejected('INVALID_PRIVATE_RECEIVER')
        for key in ['account_id','npk']:
            if not isinstance(value[key],str) or len(value[key])!=64 or any(c not in '0123456789abcdef' for c in value[key]):raise Rejected('INVALID_PRIVATE_RECEIVER')
        amount(value['identifier'])
        if not isinstance(value['vpk_borsh'],str) or not 2<=len(value['vpk_borsh'])<=8192 or any(c not in '0123456789abcdef' for c in value['vpk_borsh']):raise Rejected('INVALID_PRIVATE_RECEIVER')
    def fresh_receiver(self):
        result=self.wallet.invoke('receive-address');receiver={k:result[k] for k in ['account_id','npk','vpk_borsh','identifier']};self.validate_receiver(receiver);return receiver
    def validate_public_receiver(self,value):
        if not isinstance(value,dict) or set(value)!={'account_id','public','after_block'} or value['public'] is not True:raise Rejected('INVALID_PUBLIC_RECEIVER')
        if not isinstance(value['account_id'],str) or not re.fullmatch('[0-9a-f]{64}',value['account_id']):raise Rejected('INVALID_PUBLIC_RECEIVER')
        if type(value['after_block']) is not int or not 0<=value['after_block']<=9007199254740991:raise Rejected('INVALID_PUBLIC_RECEIVER')
    def public_receiver(self):
        value=self.wallet.invoke('public-account')
        if value.get('wallet_owned') is not True or value.get('initialized') is not True:raise Rejected('PUBLIC_RECEIVING_ACCOUNT_NOT_READY')
        receiver={'account_id':value['account_id'],'public':True,'after_block':value['block_id']}
        self.validate_public_receiver(receiver);return receiver
    def public_receipt(self,receiver,sender,quote,hash,proof,purpose,minimum_block=None):
        self.validate_public_receiver(receiver);self.validate_public_receiver(sender)
        if not isinstance(proof,dict):raise Rejected('PUBLIC_PAYER_PROOF_REQUIRED')
        floor=receiver['after_block'] if minimum_block is None else max(receiver['after_block'],minimum_block)
        return self.wallet.invoke('check-public-payment',{'account':receiver['account_id'],'sender':sender['account_id'],
            'tx_hash':hash,'amount':quote['amount'],'minimum_block':str(floor),'quote_hash':digest(quote),'purpose':purpose,'proof':proof})
    def confirm_payment(self,row,hash,proof=None):
        if row['payment_hash']:
            if row['payment_hash']!=hash:raise RpcError(-32602,'PaymentTransactionChanged')
            if not row['local_task'] and row['state'] not in TERMINAL:self.start_service(row['id'])
            return
        quote=json.loads(row['quote']);price=amount(quote['amount'])
        if not price:raise RpcError(-32602,'PaymentNotRequested')
        if quote.get('paymentMode','private')=='public':
            receipt=self.public_receipt(quote['recipient'],json.loads(row['refund_address']),quote,hash,proof,'payment')
        else:
            if proof is not None:raise Rejected('UNEXPECTED_PUBLIC_PAYMENT_PROOF')
            receipt=self.wallet.invoke('check-payment',{'account':quote['recipient']['account_id'],'tx_hash':hash,'amount':str(price)})
        if receipt.get('confirmed') is not True:raise RpcError(-32006,'PaymentNotYetConfirmed')
        if receipt.get('transaction_hash')!=hash or receipt.get('receiver_account')!=quote['recipient']['account_id'] or receipt.get('amount')!=str(price):raise Rejected('PAYMENT_RECEIPT_BINDING_MISMATCH')
        with self.engine.tx() as db:
            used=db.execute('SELECT id FROM a2a_tasks WHERE payment_hash=?',(hash,)).fetchone()
            if used and used[0]!=row['id']:raise Rejected('PAYMENT_ALREADY_USED')
        self._set(row['id'],payment_state='paid',payment_hash=hash)
        if row['state']=='canceled' or int(self.engine.clock())>row['deadline']:
            self._set(row['id'],state='canceled',error='Payment arrived after cancellation or expiry. Refund pending.');self.queue_refund(row['id'])
        else:self.start_service(row['id'])
    def start_service(self,id):
        row=self._row(id)
        if row['local_task']:return
        contact=self.mailbox.get_contact(row['peer'])
        if row['skill'] not in self.exports:
            self._set(id,state='failed',error='This service was withdrawn before execution.')
            self.queue_refund(id);return
        local=self.engine.accept_service_task(key_id(contact.signing_key),'a2a-'+id,row['skill'],json.loads(row['args']),self.exports,int(self.engine.clock())+3600)
        self._set(id,state='working',local_task=local['id'])
        self.service.get_controller().schedule(local['id'])
    def queue_refund(self,id):
        row=self._row(id)
        if row['payment_state'] in ['refunded','not-required','unpaid']:return
        quote=json.loads(row['quote'])
        mode=quote.get('paymentMode','private');recipient=json.loads(row['refund_address'])
        intent={'kind':'transfer-'+mode,'arguments':{'recipient':recipient['account_id'] if mode=='public' else recipient,'amount':quote['amount']},'expires_at':int(self.engine.clock())+86400}
        with self.engine.tx() as db:db.execute('INSERT OR IGNORE INTO a2a_refunds(task_id,intent,state) VALUES (?,?,?)',(id,canonical(intent).decode(),'queued'))
        self._set(id,payment_state='refund-pending')
    def _refund(self,id):
        with self.engine.tx() as db:row=db.execute('SELECT * FROM a2a_refunds WHERE task_id=?',(id,)).fetchone()
        operation='refund-'+id
        try:
            prepared=self.wallet.invoke('prepare',{'operation_id':operation,'intent':json.loads(row['intent'])},timeout=7100)
            if prepared.get('state') not in ['prepared','confirmed','submitted','unknown']:raise Rejected('REFUND_NOT_PREPARED')
            with self.engine.tx() as db:db.execute("UPDATE a2a_refunds SET state='prepared',tx_hash=? WHERE task_id=?",(prepared['tx_hash'],id))
            result=self.wallet.invoke('broadcast',{'operation_id':operation})
            if result.get('state')!='confirmed':result=self.wallet.invoke('reconcile',{'operation_id':operation})
            if result.get('state')=='confirmed':
                with self.engine.tx() as db:db.execute("UPDATE a2a_refunds SET state='confirmed',error=NULL WHERE task_id=?",(id,))
                task=self._row(id);quote=json.loads(task['quote']);updates={}
                if quote.get('paymentMode','private')=='public':
                    proof=self.wallet.invoke('public-payment-proof',{'operation_id':operation,'quote_hash':digest(quote),'purpose':'refund'})
                    updates['refund_proof']=canonical(proof).decode()
                self._set(id,payment_state='refunded',refund_hash=result['tx_hash'],**updates)
            else:
                with self.engine.tx() as db:db.execute("UPDATE a2a_refunds SET state='pending',next_try=? WHERE task_id=?",(int(self.engine.clock())+10,id))
        except Exception:
            with self.engine.tx() as db:db.execute("UPDATE a2a_refunds SET state='pending',error='REFUND_NEEDS_RECONCILIATION',next_try=? WHERE task_id=?",(int(self.engine.clock())+30,id))
    def tick(self):
        if self.discovery_future and self.discovery_future.done():
            try:self.discovery_future.result();self.discovery_error=None
            except Exception as error:
                self.discovery_error=str(error) if isinstance(error,Rejected) else 'DISCOVERY_PUBLICATION_FAILED'
                self.discovery.last_publish=time.monotonic()-45
            self.discovery_future=None
        if not self.discovery_future:
            self.discovery_future=self.discovery_worker.submit(self.discovery.tick)
        if self.runtime.discovery_handlers and time.monotonic()-self.last_announce>60:
            try:self.discover(self.config['discovery_topic'])
            except Rejected:self.last_announce=time.monotonic()-45
        with self.engine.tx() as db:pending=[dict(r) for r in db.execute('SELECT * FROM a2a_rpc_requests WHERE response IS NULL LIMIT 10')]
        for request in pending:
            try:self.handle_request(request['peer'],json.loads(request['request']))
            except (Rejected,RpcError):pass
        with self.engine.tx() as db:rows=[dict(r) for r in db.execute("SELECT * FROM a2a_tasks WHERE local_task IS NOT NULL AND state NOT IN ('completed','failed','canceled','rejected') LIMIT 100")]
        for row in rows:
            local=self.engine.get(row['local_task'])
            if local['state']=='completed':
                result=canonical(local['result'])
                if len(result)>10000:
                    self._set(row['id'],state='failed',error='The service result is too large for this message. The provider must return a stored-file reference instead.')
                    self.queue_refund(row['id'])
                else:self._set(row['id'],state='completed',result=result.decode())
            elif local['state'] in ['failed','rejected','canceled']:
                self._set(row['id'],state=local['state'],error=local.get('error') or 'Service did not complete.');self.queue_refund(row['id'])
        with self.engine.tx() as db:subscriptions=[dict(r) for r in db.execute('SELECT * FROM a2a_subscriptions')]
        for sub in subscriptions:
            # The receiver must durably store the initial Task response before
            # later events are queued. Transport retries retain exact messages.
            initial_id='rpc-result-'+hashlib.sha256((sub['peer']+'\0'+sub['rpc_id']).encode()).hexdigest()
            try:initial_status=self.mailbox.status(initial_id)
            except Rejected:continue
            if initial_status['state']!='acknowledged':continue
            with self.engine.tx() as db:events=list(db.execute('SELECT * FROM a2a_events WHERE task_id=? AND sequence>? ORDER BY sequence LIMIT 10',(sub['task_id'],sub['last_sequence'])))
            for event in events:
                payload={'requestId':sub['rpc_id'],'sequence':event['sequence'],'response':json.loads(event['event'])}
                self.mailbox.enqueue(sub['peer'],'a2a-event',payload,message_id='a2a-event-'+hashlib.sha256((sub['peer']+'\0'+sub['task_id']+'\0'+sub['rpc_id']+'\0'+str(event['sequence'])).encode()).hexdigest(),ttl=86400)
                with self.engine.tx() as db:db.execute('UPDATE a2a_subscriptions SET last_sequence=? WHERE task_id=? AND peer=? AND rpc_id=?',(event['sequence'],sub['task_id'],sub['peer'],sub['rpc_id']))
            with self.engine.tx() as db:
                current=db.execute('SELECT state,sequence FROM a2a_tasks WHERE id=?',(sub['task_id'],)).fetchone()
                delivered=db.execute('SELECT last_sequence FROM a2a_subscriptions WHERE task_id=? AND peer=? AND rpc_id=?',(sub['task_id'],sub['peer'],sub['rpc_id'])).fetchone()
                if current and delivered and current['state'] in TERMINAL and delivered[0]>=current['sequence']:
                    db.execute('DELETE FROM a2a_subscriptions WHERE task_id=? AND peer=? AND rpc_id=?',(sub['task_id'],sub['peer'],sub['rpc_id']))
        if self.refund_future and self.refund_future.done():self.refund_future=None
        if self.refund_future is None:
            with self.engine.tx() as db:row=db.execute("SELECT task_id FROM a2a_refunds WHERE state!='confirmed' AND next_try<=? ORDER BY rowid LIMIT 1",(int(self.engine.clock()),)).fetchone()
            if row:self.refund_future=self.refund_worker.submit(self._refund,row[0])
    def close(self):
        self.discovery_worker.shutdown(wait=False,cancel_futures=True)
        self.refund_worker.shutdown(wait=False,cancel_futures=True)
        self.discovery_worker.shutdown(wait=False,cancel_futures=True)
