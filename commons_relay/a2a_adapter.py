"""Client-side A2A skills using signed discovery, bounded quotes and private funds."""
from __future__ import annotations
import json
import time
from .codec import Rejected,canonical,digest,amount
from .engine import Prepared,Receipt
from .a2a_types import PAYMENT_EXTENSION,BINDING_EXTENSION,verify_card

class A2AAdapter:
    def __init__(self,service):
        self.service=service;self.engine=service.engine;self.protocol=service.get_agent_protocol();self.wallet=service.get_wallet()
        with self.engine.tx() as db:db.execute('''CREATE TABLE IF NOT EXISTS a2a_effects(task_id TEXT PRIMARY KEY,skill TEXT NOT NULL,args TEXT NOT NULL,peer TEXT,remote_task TEXT,context_id TEXT,quote TEXT,refund_address TEXT,payment_hash TEXT,result TEXT,last_poll INTEGER NOT NULL DEFAULT 0)''')
    def row(self,id):
        with self.engine.tx() as db:row=db.execute('SELECT * FROM a2a_effects WHERE task_id=?',(id,)).fetchone()
        if not row:raise Rejected('A2A_EFFECT_NOT_PREPARED')
        return dict(row)
    def save(self,id,**values):
        allowed={'remote_task','context_id','quote','refund_address','payment_hash','result','last_poll'}
        if set(values)-allowed:raise Rejected('A2A_EFFECT_FIELD_DENIED')
        with self.engine.tx() as db:db.execute('UPDATE a2a_effects SET '+','.join(k+'=?' for k in values)+' WHERE task_id=?',(*values.values(),id))
    def prepare(self,task):
        skill=task['skill'];args=task['arguments'];id=task['id'];max_spend=amount(task['maximum_spend'])
        if skill not in ['agent.discover','agent.task','agent.subscribe','agent.cancel']:raise Rejected('A2A_SKILL_UNAVAILABLE')
        self.service.get_controller().start()
        peer=args.get('agent_address')
        with self.engine.tx() as db:
            old=db.execute('SELECT * FROM a2a_effects WHERE task_id=?',(id,)).fetchone()
            if old and (old['skill']!=skill or old['args']!=canonical(args).decode()):raise Rejected('A2A_EFFECT_REUSED')
            db.execute('INSERT OR IGNORE INTO a2a_effects(task_id,skill,args,peer) VALUES (?,?,?,?)',(id,skill,canonical(args).decode(),peer))
        if skill=='agent.discover':
            self.protocol.discover(args['topic']);self.save(id,result=canonical({'agents':self.protocol.cards()}).decode());return Prepared('a2a-read:'+id,0,id)
        self.protocol.mailbox.get_contact(peer)
        if skill!='agent.task':return Prepared('a2a-control:'+id,0,id)
        row=self.row(id)
        if row['refund_address']:refund=json.loads(row['refund_address'])
        else:
            refund=self.protocol.fresh_receiver();self.save(id,refund_address=canonical(refund).decode())
        if row['remote_task']:
            remote=self.protocol.remote_task(peer,row['remote_task'])
            if remote is None:raise Rejected('REMOTE_TASK_CACHE_MISSING')
        else:
            params={'message':{'messageId':'task-'+id,'role':'ROLE_USER','parts':[{'data':{'skill':args['skill'],'arguments':args['params'],'refundAddress':refund},'mediaType':'application/json'}],
                  'extensions':[PAYMENT_EXTENSION]},'configuration':{'returnImmediately':True,'acceptedOutputModes':['application/json']}}
            request=self.protocol.request(peer,'SendMessage',params,id='task-'+id)
            remote=self.protocol.await_response(request)['task'];self.protocol.remember_task(peer,remote)
            self.save(id,remote_task=remote['id'],context_id=remote['contextId'])
        metadata=remote.get('metadata',{}).get(PAYMENT_EXTENSION,{})
        quote=metadata.get('quote');self.verify_quote(task,remote,quote,refund)
        self.save(id,quote=canonical(quote).decode())
        price=amount(quote['amount'])
        self.protocol.request(peer,'SubscribeToTask',{'id':remote['id']},id='subscribe-'+id)
        if price==0:return Prepared('a2a-free:'+id,0,id)
        # The engine reservation was made before any model or network operation.
        # Only the agreed amount, exact receiver and immutable task quote enter
        # the wallet transaction. A higher returned price cannot enlarge it.
        intent={'kind':'transfer-private','arguments':{'recipient':quote['recipient'],'amount':str(price)},'expires_at':min(task['deadline'],quote['expiresAt'])}
        prepared=self.wallet.invoke('prepare',{'operation_id':'pay-'+id,'intent':intent},timeout=7100)
        if prepared.get('state') not in ['prepared','confirmed'] or prepared.get('maximum_spend')!=str(price):raise Rejected('A2A_PAYMENT_PREPARATION_MISMATCH')
        txhash=prepared.get('tx_hash')
        if not isinstance(txhash,str) or len(txhash)!=64:raise Rejected('A2A_PAYMENT_HASH_MISSING')
        self.save(id,payment_hash=txhash)
        return Prepared(txhash,price,id)
    def verify_quote(self,task,remote,quote,refund):
        args=task['arguments']
        expected={'id','taskId','contextId','provider','client','skill','argumentsHash','asset','network','amount','recipient','expiresAt','refund','refundAddressHash'}
        if not isinstance(quote,dict) or set(quote)!=expected:raise Rejected('PAYMENT_QUOTE_REQUIRED')
        if quote['taskId']!=remote['id'] or quote['contextId']!=remote['contextId'] or quote['provider']!=args['agent_address'] or quote['client']!=self.engine.agent or quote['skill']!=args['skill'] or quote['argumentsHash']!=digest(args['params']) or quote['refundAddressHash']!=digest(refund):raise Rejected('PAYMENT_QUOTE_BINDING_MISMATCH')
        if quote['asset']!='LEZ-testnet' or quote['network']!='https://testnet.lez.logos.co/':raise Rejected('PAYMENT_WRONG_NETWORK_OR_ASSET')
        if amount(quote['amount'])!=amount(task['maximum_spend']):raise Rejected('PAYMENT_PRICE_CHANGED_AFTER_AUTHORIZATION')
        if type(quote['expiresAt'])is not int or quote['expiresAt']<=int(self.engine.clock()):raise Rejected('PAYMENT_QUOTE_EXPIRED')
        if amount(quote['amount']):self.protocol.validate_receiver(quote['recipient'])
    def announce_payment(self,row):
        if not row['payment_hash']:return
        quote=json.loads(row['quote']);params={'message':{'messageId':'payment-'+row['task_id'],'taskId':row['remote_task'],'contextId':row['context_id'],'role':'ROLE_USER',
            'parts':[{'data':{'payment':{'quoteId':quote['id'],'transactionHash':row['payment_hash']}},'mediaType':'application/json'}],
            'extensions':[PAYMENT_EXTENSION]},'configuration':{'returnImmediately':True,'acceptedOutputModes':['application/json']}}
        self.protocol.request(row['peer'],'SendMessage',params,id='payment-'+row['task_id'])
    def broadcast(self,effect):
        row=self.row(effect.opaque_handle);args=json.loads(row['args'])
        if row['skill']=='agent.discover':return
        if row['skill'] in ['agent.cancel','agent.subscribe']:
            method='CancelTask' if row['skill']=='agent.cancel' else 'SubscribeToTask'
            request=self.protocol.request(row['peer'],method,{'id':args['task_id']},id='control-'+row['task_id'])
            result=self.protocol.await_response(request);self.save(row['task_id'],result=canonical(result).decode());return
        if row['payment_hash']:
            if effect.reference!=row['payment_hash']:raise Rejected('A2A_PAYMENT_HASH_CHANGED')
            result=self.wallet.invoke('broadcast',{'operation_id':'pay-'+row['task_id']},timeout=120)
            if result.get('tx_hash')!=effect.reference:raise Rejected('A2A_PAYMENT_BROADCAST_MISMATCH')
            if result.get('state')=='confirmed':self.announce_payment(row)
    def lookup(self,effect):
        row=self.row(effect.opaque_handle)
        if row['skill']!='agent.task':return Receipt(effect.reference,'confirmed',0,json.loads(row['result'])) if row['result'] else Receipt(effect.reference,'pending')
        price=amount(json.loads(row['quote'])['amount'])
        if row['payment_hash']:
            payment=self.wallet.invoke('reconcile',{'operation_id':'pay-'+row['task_id']},timeout=120)
            if payment.get('state')!='confirmed':return Receipt(effect.reference,'pending')
            if payment.get('tx_hash')!=effect.reference:raise Rejected('A2A_PAYMENT_RECEIPT_MISMATCH')
            self.announce_payment(row)
        remote=self.protocol.remote_task(row['peer'],row['remote_task'])
        now=int(self.engine.clock())
        if now-row['last_poll']>=10:
            rid='poll-'+row['task_id']+'-'+str(now)
            self.protocol.request(row['peer'],'GetTask',{'id':row['remote_task']},id=rid);self.save(row['task_id'],last_poll=now)
        if not remote:return Receipt(effect.reference,'pending')
        state=remote['status']['state'];payment=remote.get('metadata',{}).get(PAYMENT_EXTENSION,{})
        if state=='TASK_STATE_COMPLETED' and remote.get('artifacts'):
            result={'remote_task_id':row['remote_task'],'provider':row['peer'],'artifacts':remote['artifacts'],'payment_transaction':row['payment_hash'],'paid_amount':str(price)}
            self.save(row['task_id'],result=canonical(result).decode());return Receipt(effect.reference,'confirmed',price,result)
        if state in ['TASK_STATE_FAILED','TASK_STATE_REJECTED','TASK_STATE_CANCELED']:
            if price==0:return Receipt(effect.reference,'rejected',0,{'remote_task_id':row['remote_task'],'state':state})
            if payment.get('paymentState')=='refunded' and payment.get('refundTransaction'):
                refund=json.loads(row['refund_address']);receipt=self.wallet.invoke('check-payment',{'account':refund['account_id'],'tx_hash':payment['refundTransaction'],'amount':str(price)})
                if receipt.get('confirmed')is True and receipt.get('amount')==str(price):
                    return Receipt(effect.reference,'rejected',0,{'remote_task_id':row['remote_task'],'state':state,'refund_transaction':payment['refundTransaction'],'refund_verified':True})
        return Receipt(effect.reference,'pending')
