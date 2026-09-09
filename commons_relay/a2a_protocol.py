"""Durable A2A task server and client bookkeeping over encrypted Logos messages.

Payments are an advertised extension. A payment claim is checked against the
actual private receiver commitment in the stated transaction, not an asserted
hash, a model response, or a balance number supplied by the peer.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import secrets
import threading
import time
from .codec import Rejected,canonical,parse,digest,identifier,amount,b64
from .signing import key_id
from .a2a_types import jcs as jcs_card_bytes, PAYMENT_EXTENSION,BINDING_EXTENSION,TERMINAL,sign_card,verify_card,task_document

class RpcError(Exception):
    def __init__(self,code,message):super().__init__(message);self.code=code;self.message=message

class AgentProtocol:
    def __init__(self,service):
        self.service=service;self.engine=service.engine;self.runtime=service.get_messaging();self.mailbox=self.runtime.mailbox
        self.root=service.root;self.wallet=service.get_wallet();self.guard=threading.RLock()
        self.refund_worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix='a2a-refund-io');self.refund_future=None
        self.config=self.load_config();self.exports=set(self.config['exports']);self.last_announce=0
        with self.engine.tx() as db:
            db.executescript('''
             CREATE TABLE IF NOT EXISTS a2a_tasks(id TEXT PRIMARY KEY,context_id TEXT NOT NULL,peer TEXT NOT NULL,message_id TEXT NOT NULL,intent_hash TEXT NOT NULL,skill TEXT NOT NULL,args TEXT NOT NULL,state TEXT NOT NULL,sequence INTEGER NOT NULL,created INTEGER NOT NULL,updated INTEGER NOT NULL,deadline INTEGER NOT NULL,quote TEXT,payment_state TEXT NOT NULL,payment_hash TEXT UNIQUE,refund_address TEXT,refund_hash TEXT,local_task TEXT,result TEXT,error TEXT,UNIQUE(peer,message_id));
             CREATE TABLE IF NOT EXISTS a2a_events(task_id TEXT NOT NULL,sequence INTEGER NOT NULL,event TEXT NOT NULL,PRIMARY KEY(task_id,sequence));
             CREATE TABLE IF NOT EXISTS a2a_subscriptions(task_id TEXT NOT NULL,peer TEXT NOT NULL,rpc_id TEXT NOT NULL,last_sequence INTEGER NOT NULL,PRIMARY KEY(task_id,peer,rpc_id));
             CREATE TABLE IF NOT EXISTS a2a_rpc_requests(peer TEXT NOT NULL,id TEXT NOT NULL,request_hash TEXT NOT NULL,request TEXT NOT NULL,response TEXT,PRIMARY KEY(peer,id));
             CREATE TABLE IF NOT EXISTS a2a_client_requests(id TEXT PRIMARY KEY,peer TEXT NOT NULL,request TEXT NOT NULL,response TEXT,created INTEGER NOT NULL);
             CREATE TABLE IF NOT EXISTS a2a_remote_tasks(peer TEXT NOT NULL,id TEXT NOT NULL,document TEXT NOT NULL,sequence INTEGER NOT NULL,PRIMARY KEY(peer,id));
             CREATE TABLE IF NOT EXISTS a2a_cards(address TEXT PRIMARY KEY,card TEXT NOT NULL,received INTEGER NOT NULL);
             CREATE TABLE IF NOT EXISTS a2a_refunds(task_id TEXT PRIMARY KEY,intent TEXT NOT NULL,state TEXT NOT NULL,tx_hash TEXT,error TEXT,next_try INTEGER NOT NULL DEFAULT 0);
            ''')
    def load_config(self):
        path=self.root/'services.json'
        if not path.is_file():return {'name':'Commons Relay','description':'Owner-controlled Logos agent','exports':{},'discovery_topic':'commons'}
        if path.is_symlink() or path.stat().st_size>20000 or path.stat().st_mode&0o077:raise Rejected('SERVICE_CONFIGURATION_PERMISSIONS')
        cfg=parse(path.read_bytes())
        if not isinstance(cfg,dict) or set(cfg)!={'name','description','exports','discovery_topic'} or not isinstance(cfg['exports'],dict) or len(cfg['exports'])>32:raise Rejected('INVALID_SERVICE_CONFIGURATION')
        for name,entry in cfg['exports'].items():
            self.engine.registry.get(name)
            if not isinstance(entry,dict) or set(entry)!={'price','description'} or not isinstance(entry['description'],str):raise Rejected('INVALID_SERVICE_PRICE')
            if amount(entry['price'])>self.engine.policy.hard_maximum:raise Rejected('SERVICE_PRICE_EXCEEDS_POLICY')
            if name in ['wallet.send','program.call','program.deploy','meta.configure']:raise Rejected('UNSAFE_REMOTE_SERVICE_EXPORT')
        return cfg
    def card(self):
        schemas={s['id']:s for s in self.engine.registry.describe()};contact=self.mailbox.contact()
        identity=self.root/'vault/identity-public.json'
        root_info=parse(identity.read_bytes()) if identity.is_file() else {'address':contact.address}
        card={'name':self.config['name'],'description':self.config['description'],'version':'0.1.0',
          'supportedInterfaces':[{'url':'logos://'+contact.address,'protocolBinding':'LOGOS-MESSAGING','protocolVersion':'1.0'}],
          'capabilities':{'streaming':True,'pushNotifications':False,'extendedAgentCard':True,'extensions':[
            {'uri':BINDING_EXTENSION,'required':True,'description':'Signed, recipient-encrypted Logos Messaging transport.',
             'params':{'address':contact.address,'signingPublicKey':b64(contact.signing_key),'encryptionPublicKey':b64(contact.box_key),'rootNpk':root_info.get('root_npk'),'discoveryTopic':self.config['discovery_topic']}},
            {'uri':PAYMENT_EXTENSION,'required':any(amount(x['price']) for x in self.config['exports'].values()),
             'description':'Exact task-bound private LEZ payment; refundable before successful fulfillment.',
             'params':{'network':'https://testnet.lez.logos.co/','asset':'LEZ-testnet','prices':{k:v['price'] for k,v in self.config['exports'].items()},
                       'inputSchemas':{k:schemas[k]['input_schema'] for k in self.exports}}}]},
          'defaultInputModes':['application/json'],'defaultOutputModes':['application/json'],
          'skills':[{'id':name,'name':name,'description':cfg['description'],'tags':[name.split('.')[0]],'inputModes':['application/json'],'outputModes':['application/json']} for name,cfg in self.config['exports'].items()]}
        return sign_card(card,self.mailbox.vault.signing_private,contact.signing_key,self.mailbox.vault.signer)
    def discovery_topic(self,name):
        if not isinstance(name,str) or not 1<=len(name)<=128:raise Rejected('INVALID_DISCOVERY_TOPIC')
        return '/commons-relay/1/discovery-'+hashlib.sha256(name.encode()).hexdigest()[:32]+'/json'
    def publish_storage_card(self):
        card=self.card();fingerprint=digest(card)
        with self.engine.tx() as db:row=db.execute("SELECT value FROM metadata WHERE key='agent-card-storage'").fetchone()
        if row:
            cached=json.loads(row[0])
            if cached['hash']==fingerprint:return cached['cid']
        self.service.storage_adapter();self.service.store.initialize()
        directory=self.root/'public';directory.mkdir(mode=0o700,exist_ok=True)
        path=directory/'agent-card.json'
        if path.is_symlink():raise Rejected('SYMLINK_PUBLIC_CARD')
        temporary=path.with_suffix('.tmp');temporary.write_bytes(jcs_card_bytes(card));temporary.chmod(0o600);temporary.replace(path)
        result=self.service.bridge.call('storage.publish-card',{'path':str(path)})
        address=result.get('address') if isinstance(result,dict) else None
        if not isinstance(address,str) or not 20<=len(address)<=180:raise Rejected('CARD_STORAGE_RECEIPT_MISSING')
        with self.engine.tx() as db:db.execute("INSERT INTO metadata VALUES ('agent-card-storage',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(canonical({'hash':fingerprint,'cid':address}).decode(),))
        return address
    def discover(self,name):
        # agent.start and the controller's first tick can overlap. Keep card
        # publication single-flight so Storage bootstrap/upload and the public
        # announcement cannot be duplicated by those two callers.
        with self.guard:
            topic=self.discovery_topic(name);self.runtime.start()
            if topic not in self.runtime.discovery_handlers:
                def receive(raw):
                    payload=parse(raw.encode())
                    if not isinstance(payload,dict) or set(payload)!={'kind','address','card','storageCid'} or payload['kind']!='agent-card':raise Rejected('INVALID_DISCOVERY_ANNOUNCEMENT')
                    if payload['address']==self.mailbox.address:return
                    self.remember_card(payload['address'],payload['card'])
                self.runtime.discovery_handlers[topic]=receive
                self.service.bridge.call('delivery.subscribe',{'topic':topic})
            address=self.publish_storage_card()
            announcement={'kind':'agent-card','address':self.mailbox.address,'card':self.card(),'storageCid':address}
            self.service.bridge.call('delivery.publish-card',{'topic':topic,'payload':canonical(announcement).decode()})
            # Also deliver a signed, encrypted announcement to configured peers.
            # That catches peers joining after the public mesh announcement.
            epoch=int(self.engine.clock())//60
            for peer in self.mailbox.contacts():
                self.mailbox.enqueue(peer['address'],'agent-card',self.card(),message_id='card-'+hashlib.sha256((peer['address']+str(epoch)).encode()).hexdigest(),ttl=300)
            self.last_announce=time.monotonic();return self.cards()
    def remember_card(self,peer,card):
        contact=self.mailbox.get_contact(peer);value=verify_card(card,contact.signing_key,self.mailbox.vault.signer)
        if value['supportedInterfaces'][0]['url']!='logos://'+peer:raise Rejected('CARD_ADDRESS_MISMATCH')
        with self.engine.tx() as db:db.execute('INSERT INTO a2a_cards VALUES (?,?,?) ON CONFLICT(address) DO UPDATE SET card=excluded.card,received=excluded.received',(peer,canonical(card).decode(),int(self.engine.clock())))
        return value
    def verified_card(self,address):
        self.mailbox.get_contact(address)
        with self.engine.tx() as db:row=db.execute('SELECT card FROM a2a_cards WHERE address=?',(address,)).fetchone()
        if not row:raise Rejected('AGENT_CARD_REQUIRED_FOR_PRICE')
        contact=self.mailbox.get_contact(address)
        return verify_card(json.loads(row[0]),contact.signing_key,self.mailbox.vault.signer)
    def cards(self):
        with self.engine.tx() as db:return [{'address':r['address'],'card':json.loads(r['card']),'received':r['received']} for r in db.execute('SELECT * FROM a2a_cards ORDER BY address')]
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
        allowed={'state','quote','payment_state','payment_hash','refund_hash','local_task','result','error'}
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
        if method=='GetExtendedAgentCard' and not params:return self.card()
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
            if set(data)!={'payment'} or not isinstance(data['payment'],dict) or set(data['payment'])!={'quoteId','transactionHash'}:raise RpcError(-32602,'InvalidPaymentExtension')
            quote=json.loads(row['quote']);payment=data['payment']
            if payment['quoteId']!=quote['id'] or not isinstance(payment['transactionHash'],str) or len(payment['transactionHash'])!=64:raise RpcError(-32602,'PaymentQuoteMismatch')
            self.confirm_payment(row,payment['transactionHash'])
            return self.send_result(row['id'],peer,cfg)
        if set(data)!={'skill','arguments','refundAddress'} or not isinstance(data['arguments'],dict):raise RpcError(-32602,'InvalidTaskRequest')
        skill=data['skill']
        if skill not in self.exports:raise RpcError(-32005,'UnsupportedSkillError')
        quote_policy=self.engine.registry.get(skill).validate(data['arguments'])
        if quote_policy.maximum!=0:raise RpcError(-32005,'ServiceCannotSpendOwnerFunds')
        refund=data['refundAddress'];self.validate_receiver(refund)
        fingerprint=digest(data)
        with self.engine.tx() as db:old=db.execute('SELECT * FROM a2a_tasks WHERE peer=? AND message_id=?',(peer,message['messageId'])).fetchone()
        if old:
            if old['intent_hash']!=fingerprint:raise RpcError(-32602,'MessageIdReusedWithDifferentIntent')
            return self.send_result(old['id'],peer,cfg)
        now=int(self.engine.clock());task=secrets.token_hex(16);context=message.get('contextId',secrets.token_hex(16));identifier(context)
        price=amount(self.config['exports'][skill]['price']);receiver=self.fresh_receiver() if price else None
        quote={'id':'quote-'+task,'taskId':task,'contextId':context,'provider':self.mailbox.address,'client':peer,'skill':skill,
               'argumentsHash':digest(data['arguments']),'asset':'LEZ-testnet','network':'https://testnet.lez.logos.co/','amount':str(price),
               'recipient':receiver,'expiresAt':now+7200,'refund':'full before successful fulfillment','refundAddressHash':digest(refund)}
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
    def confirm_payment(self,row,hash):
        if row['payment_hash']:
            if row['payment_hash']!=hash:raise RpcError(-32602,'PaymentTransactionChanged')
            if not row['local_task'] and row['state'] not in TERMINAL:self.start_service(row['id'])
            return
        quote=json.loads(row['quote']);price=amount(quote['amount'])
        if not price:raise RpcError(-32602,'PaymentNotRequested')
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
        local=self.engine.accept_service_task(key_id(contact.signing_key),'a2a-'+id,row['skill'],json.loads(row['args']),self.exports,int(self.engine.clock())+3600)
        self._set(id,state='working',local_task=local['id'])
        self.service.get_controller().schedule(local['id'])
    def queue_refund(self,id):
        row=self._row(id)
        if row['payment_state'] in ['refunded','not-required','unpaid']:return
        quote=json.loads(row['quote'])
        intent={'kind':'transfer-private','arguments':{'recipient':json.loads(row['refund_address']),'amount':quote['amount']},'expires_at':int(self.engine.clock())+86400}
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
                self._set(id,payment_state='refunded',refund_hash=result['tx_hash'])
            else:
                with self.engine.tx() as db:db.execute("UPDATE a2a_refunds SET state='pending',next_try=? WHERE task_id=?",(int(self.engine.clock())+10,id))
        except Exception:
            with self.engine.tx() as db:db.execute("UPDATE a2a_refunds SET state='pending',error='REFUND_NEEDS_RECONCILIATION',next_try=? WHERE task_id=?",(int(self.engine.clock())+30,id))
    def tick(self):
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
            if local['state']=='completed':self._set(row['id'],state='completed',result=canonical(local['result']).decode())
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
    def close(self):self.refund_worker.shutdown(wait=False,cancel_futures=True)
