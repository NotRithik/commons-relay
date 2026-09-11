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
        with self.engine.tx() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS a2a_effects(task_id TEXT PRIMARY KEY,skill TEXT NOT NULL,args TEXT NOT NULL,peer TEXT,remote_task TEXT,context_id TEXT,quote TEXT,refund_address TEXT,payment_hash TEXT,result TEXT,last_poll INTEGER NOT NULL DEFAULT 0)''')
            db.execute('''CREATE TABLE IF NOT EXISTS a2a_peer_health(peer TEXT PRIMARY KEY,status TEXT NOT NULL,observed INTEGER NOT NULL,rtt_ms INTEGER)''')
            db.execute('CREATE TABLE IF NOT EXISTS a2a_verified_refunds(tx_hash TEXT PRIMARY KEY,task_id TEXT UNIQUE NOT NULL)')
    def row(self,id):
        with self.engine.tx() as db:row=db.execute('SELECT * FROM a2a_effects WHERE task_id=?',(id,)).fetchone()
        if not row:raise Rejected('A2A_EFFECT_NOT_PREPARED')
        return dict(row)
    def save(self,id,**values):
        allowed={'remote_task','context_id','quote','refund_address','payment_hash','result','last_poll'}
        if set(values)-allowed:raise Rejected('A2A_EFFECT_FIELD_DENIED')
        with self.engine.tx() as db:db.execute('UPDATE a2a_effects SET '+','.join(k+'=?' for k in values)+' WHERE task_id=?',(*values.values(),id))
    def peer_health(self,peer):
        with self.engine.tx() as db:row=db.execute('SELECT status,observed,rtt_ms FROM a2a_peer_health WHERE peer=?',(peer,)).fetchone()
        return dict(row) if row else None
    def probe_peer(self,peer,task_id,timeout=8,max_age=15):
        now=int(self.engine.clock());known=self.peer_health(peer)
        if known and known['status']=='responding' and now-known['observed']<=max_age:
            self.engine.set_progress(task_id,'peer-ready',f"The other agent replied recently. Its last response took {known['rtt_ms']} ms.")
            return known
        self.engine.set_progress(task_id,'peer-preflight','Checking that the other agent is online before doing any paid work.')
        started=time.monotonic();bucket=now//5
        request=self.protocol.request(peer,'GetExtendedAgentCard',{},id='preflight-'+task_id+'-'+str(bucket))
        try:card=self.protocol.await_response(request,timeout=timeout)
        except Rejected as error:
            if str(error)!='A2A_RESPONSE_PENDING':raise
            with self.engine.tx() as db:db.execute("INSERT INTO a2a_peer_health(peer,status,observed,rtt_ms) VALUES (?, 'no-reply', ?, NULL) ON CONFLICT(peer) DO UPDATE SET status='no-reply',observed=excluded.observed,rtt_ms=NULL",(peer,now))
            self.engine.set_progress(task_id,'peer-unresponsive',f'The other agent did not answer within {timeout} seconds. Nothing was paid.')
            return None
        self.protocol.remember_card(peer,card);rtt=int((time.monotonic()-started)*1000);observed=int(self.engine.clock())
        with self.engine.tx() as db:db.execute("INSERT INTO a2a_peer_health(peer,status,observed,rtt_ms) VALUES (?, 'responding', ?, ?) ON CONFLICT(peer) DO UPDATE SET status='responding',observed=excluded.observed,rtt_ms=excluded.rtt_ms",(peer,observed,rtt))
        self.engine.set_progress(task_id,'peer-ready',f'The other agent replied in {rtt} ms. Sending the task now.')
        return {'status':'responding','observed':observed,'rtt_ms':rtt}
    def prepare(self,task):
        skill=task['skill'];args=task['arguments'];id=task['id'];max_spend=amount(task['maximum_spend'])
        if skill not in ['agent.discover','agent.ping','agent.task','agent.subscribe','agent.cancel']:raise Rejected('A2A_SKILL_UNAVAILABLE')
        self.service.get_controller().start()
        peer=args.get('agent_address')
        with self.engine.tx() as db:
            old=db.execute('SELECT * FROM a2a_effects WHERE task_id=?',(id,)).fetchone()
            if old and (old['skill']!=skill or old['args']!=canonical(args).decode()):raise Rejected('A2A_EFFECT_REUSED')
            db.execute('INSERT OR IGNORE INTO a2a_effects(task_id,skill,args,peer) VALUES (?,?,?,?)',(id,skill,canonical(args).decode(),peer))
        if skill=='agent.discover':
            self.engine.set_progress(id,'discovering','Looking for services on this topic. Public providers reply with signed listings.')
            found=self.protocol.discover(args['topic'],wait_seconds=5)
            visible=[]
            for item in found:
                if len(canonical({'agents':visible+[item]}))>12000:break
                visible.append(item)
            self.save(id,result=canonical({'agents':visible,'topic':args['topic'],'more_available':len(found)>len(visible),'total_found':len(found)}).decode());return Prepared('a2a-read:'+id,0,id)
        self.protocol.mailbox.get_contact(peer)
        if skill=='agent.ping':
            if max_spend!=0:raise Rejected('A2A_PING_CANNOT_SPEND')
            if self.row(id)['result']:
                return Prepared('a2a-ping:'+id,0,id)
            health=self.probe_peer(peer,id,timeout=8,max_age=0)
            if health:
                result={'agent_address':peer,'reachable':True,'status':'responding','round_trip_ms':health['rtt_ms'],
                        'observed_at':health['observed'],'paid_amount':'0'}
            else:
                result={'agent_address':peer,'reachable':None,'status':'no-reply',
                        'detail':'No reply within 8 seconds. The other agent may be offline or temporarily unreachable.',
                        'observed_at':int(self.engine.clock()),'paid_amount':'0'}
            self.save(id,result=canonical(result).decode())
            detail=(f"The other agent replied in {health['rtt_ms']} ms." if health else
                    'No reply within 8 seconds. The other agent may be offline or temporarily unreachable.')
            self.engine.set_progress(id,'completed',detail)
            return Prepared('a2a-ping:'+id,0,id)
        if skill!='agent.task':return Prepared('a2a-control:'+id,0,id)
        if not self.probe_peer(peer,id,timeout=8,max_age=15):raise Rejected('DOWNSTREAM_PEER_UNRESPONSIVE')
        row=self.row(id)
        mode=args.get('payment_mode','private')
        if row['refund_address']:refund=json.loads(row['refund_address'])
        else:
            refund=(self.protocol.public_receiver() if mode=='public' else self.protocol.fresh_receiver()) if max_spend else None
            self.save(id,refund_address=canonical(refund).decode())
        if row['remote_task']:
            remote=self.protocol.remote_task(peer,row['remote_task'])
            if remote is None:raise Rejected('REMOTE_TASK_CACHE_MISSING')
        else:
            data={'skill':args['skill'],'arguments':args['params'],'refundAddress':refund}
            if mode=='public':data['paymentMode']='public'
            params={'message':{'messageId':'task-'+id,'role':'ROLE_USER','parts':[{'data':data,'mediaType':'application/json'}],
                  'extensions':[PAYMENT_EXTENSION]},'configuration':{'returnImmediately':True,'acceptedOutputModes':['application/json']}}
            self.engine.set_progress(id,'remote-handshake','The other agent is online. Asking it to accept this task before preparing any payment.')
            request=self.protocol.request(peer,'SendMessage',params,id='task-'+id)
            handshake_seconds=30 if mode=='public' else 12
            try:remote=self.protocol.await_response(request,timeout=handshake_seconds)['task']
            except Rejected as error:
                if str(error)=='A2A_RESPONSE_PENDING':
                    self.engine.set_progress(id,'remote-handshake-timeout',f'The other agent replied but did not accept the task within {handshake_seconds} seconds. Nothing was paid.')
                    raise Rejected('DOWNSTREAM_TASK_HANDSHAKE_TIMEOUT') from None
                raise
            self.protocol.remember_task(peer,remote);self.save(id,remote_task=remote['id'],context_id=remote['contextId'])
        metadata=remote.get('metadata',{}).get(PAYMENT_EXTENSION,{})
        quote=metadata.get('quote');self.verify_quote(task,remote,quote,refund)
        self.save(id,quote=canonical(quote).decode())
        price=amount(quote['amount'])
        self.protocol.request(peer,'SubscribeToTask',{'id':remote['id']},id='subscribe-'+id)
        if price==0:
            self.engine.set_progress(id,'remote-execution','The other agent accepted the task. No payment is needed. Waiting for its result.')
            return Prepared('a2a-free:'+id,0,id)
        self.engine.set_progress(id,'payment-public' if mode=='public' else 'payment-proving',
            'Preparing your explicitly public payment. Sender, recipient and amount will be visible on-chain.' if mode=='public' else f'The other agent accepted the task at {price} testnet units. Preparing the private payment on this device. Nothing has been sent yet.')
        # The engine reservation was made before any model or network operation.
        # Only the agreed amount, exact receiver and immutable task quote enter
        # the wallet transaction. A higher returned price cannot enlarge it.
        intent={'kind':'transfer-'+mode,'arguments':{'recipient':quote['recipient']['account_id'] if mode=='public' else quote['recipient'],'amount':str(price)},'expires_at':min(task['deadline'],quote['expiresAt'])}
        prepared=self.wallet.invoke('prepare',{'operation_id':'pay-'+id,'intent':intent},timeout=7100)
        if prepared.get('state') not in ['prepared','confirmed'] or prepared.get('maximum_spend')!=str(price) or prepared.get('private') is not (mode=='private'):raise Rejected('A2A_PAYMENT_PREPARATION_MISMATCH')
        txhash=prepared.get('tx_hash')
        if not isinstance(txhash,str) or len(txhash)!=64:raise Rejected('A2A_PAYMENT_HASH_MISSING')
        self.save(id,payment_hash=txhash)
        self.engine.set_progress(id,'payment-prepared','The payment is ready on this device. Sending it is the next step.')
        return Prepared(txhash,price,id)
    def verify_quote(self,task,remote,quote,refund):
        args=task['arguments']
        expected={'id','taskId','contextId','provider','client','skill','argumentsHash','asset','network','amount','recipient','expiresAt','refund','refundAddressHash'}
        mode=args.get('payment_mode','private')
        if mode=='public':expected=expected|{'paymentMode'}
        if not isinstance(quote,dict) or set(quote)!=expected or quote.get('paymentMode','private')!=mode:raise Rejected('PAYMENT_QUOTE_REQUIRED')
        if quote['taskId']!=remote['id'] or quote['contextId']!=remote['contextId'] or quote['provider']!=args['agent_address'] or quote['client']!=self.protocol.mailbox.local_address_for(args['agent_address']) or quote['skill']!=args['skill'] or quote['argumentsHash']!=digest(args['params']) or quote['refundAddressHash']!=digest(refund):raise Rejected('PAYMENT_QUOTE_BINDING_MISMATCH')
        if quote['asset']!='LEZ-testnet' or quote['network']!='https://testnet.lez.logos.co/':raise Rejected('PAYMENT_WRONG_NETWORK_OR_ASSET')
        if amount(quote['amount'])!=amount(task['maximum_spend']):raise Rejected('PAYMENT_PRICE_CHANGED_AFTER_AUTHORIZATION')
        if type(quote['expiresAt'])is not int or quote['expiresAt']<=int(self.engine.clock()):raise Rejected('PAYMENT_QUOTE_EXPIRED')
        if amount(quote['amount']):
            self.protocol.validate_public_receiver(quote['recipient']) if mode=='public' else self.protocol.validate_receiver(quote['recipient'])
    def announce_payment(self,row):
        if not row['payment_hash']:return
        quote=json.loads(row['quote']);payment={'quoteId':quote['id'],'transactionHash':row['payment_hash']}
        if quote.get('paymentMode','private')=='public':
            payment['payerProof']=self.wallet.invoke('public-payment-proof',{'operation_id':'pay-'+row['task_id'],'quote_hash':digest(quote),'purpose':'payment'})
        params={'message':{'messageId':'payment-'+row['task_id'],'taskId':row['remote_task'],'contextId':row['context_id'],'role':'ROLE_USER',
            'parts':[{'data':{'payment':payment},'mediaType':'application/json'}],
            'extensions':[PAYMENT_EXTENSION]},'configuration':{'returnImmediately':True,'acceptedOutputModes':['application/json']}}
        self.protocol.request(row['peer'],'SendMessage',params,id='payment-'+row['task_id'])
    def resolve_follow_target(self,peer,task_id,subscribe_task_id,timeout=20):
        deadline=time.monotonic()+timeout
        seen_local=False
        while time.monotonic()<deadline:
            with self.engine.tx() as db:
                local=db.execute('SELECT remote_task,peer FROM a2a_effects WHERE task_id=?',(task_id,)).fetchone()
            if local is None:
                return task_id
            seen_local=True
            if local['peer']!=peer:
                raise Rejected('FOLLOW_TARGET_PEER_MISMATCH')
            if local['remote_task']:
                return local['remote_task']
            self.engine.set_progress(subscribe_task_id,'remote-handshake','Waiting for the other agent to accept the task before following it.')
            time.sleep(.25)
        if seen_local:
            raise Rejected('REMOTE_TASK_NOT_READY_TO_FOLLOW')
        return task_id
    def broadcast(self,effect):
        row=self.row(effect.opaque_handle);args=json.loads(row['args'])
        if row['skill'] in ['agent.discover','agent.ping']:return
        if row['skill'] in ['agent.cancel','agent.subscribe']:
            method='CancelTask' if row['skill']=='agent.cancel' else 'SubscribeToTask'
            target=args['task_id']
            if row['skill']=='agent.subscribe':
                self.engine.set_progress(row['task_id'],'remote-handshake','Asking the other agent for live updates on that task.')
                target=self.resolve_follow_target(row['peer'],target,row['task_id'])
            request=self.protocol.request(row['peer'],method,{'id':target},id='control-'+row['task_id'])
            try:
                result=self.protocol.await_response(request)
            except Rejected as error:
                if row['skill']!='agent.subscribe' or str(error)!='A2A_REMOTE_ERROR_32004':
                    raise
                snapshot_id=self.protocol.request(row['peer'],'GetTask',{'id':target},id='get-'+row['task_id'])
                snapshot=self.protocol.await_response(snapshot_id)
                result={'subscribed':False,'already_finished':True,'task':snapshot}
            else:
                if row['skill']=='agent.subscribe':
                    result={'subscribed':True,'already_finished':False,'task':result.get('task',result)}
            self.save(row['task_id'],result=canonical(result).decode());return
        if row['payment_hash']:
            if effect.reference!=row['payment_hash']:raise Rejected('A2A_PAYMENT_HASH_CHANGED')
            self.engine.set_progress(row['task_id'],'payment-broadcasting','Sending the approved payment now. If the network result becomes unclear, Relay will check this payment instead of sending another one.')
            result=self.wallet.invoke('broadcast',{'operation_id':'pay-'+row['task_id']},timeout=120)
            if result.get('tx_hash')!=effect.reference:raise Rejected('A2A_PAYMENT_BROADCAST_MISMATCH')
            if result.get('state')=='confirmed':
                self.announce_payment(row);self.engine.set_progress(row['task_id'],'remote-execution','Payment confirmed. Waiting for the other agent’s result.')
            else:self.engine.set_progress(row['task_id'],'payment-confirmation','The network has not confirmed the payment yet. Relay is checking the same payment and will not send another one.')
        elif row['skill']=='agent.task':
            self.engine.set_progress(row['task_id'],'remote-execution','The other agent accepted the task. No payment is needed. Waiting for its result.')
    def lookup(self,effect):
        row=self.row(effect.opaque_handle)
        if row['skill']!='agent.task':return Receipt(effect.reference,'confirmed',0,json.loads(row['result'])) if row['result'] else Receipt(effect.reference,'pending')
        saved_quote=json.loads(row['quote']);price=amount(saved_quote['amount']);payment_block=None
        if row['payment_hash']:
            self.engine.set_progress(row['task_id'],'payment-confirmation','Checking whether the original payment was confirmed. No second payment will be created.')
            payment=self.wallet.invoke('reconcile',{'operation_id':'pay-'+row['task_id']},timeout=120)
            if payment.get('state')!='confirmed':return Receipt(effect.reference,'pending')
            if payment.get('tx_hash')!=effect.reference:raise Rejected('A2A_PAYMENT_RECEIPT_MISMATCH')
            payment_block=payment.get('block_id')
            self.announce_payment(row)
        remote=self.protocol.remote_task(row['peer'],row['remote_task'])
        now=int(self.engine.clock())
        if now-row['last_poll']>=10:
            rid='poll-'+row['task_id']+'-'+str(now)
            self.protocol.request(row['peer'],'GetTask',{'id':row['remote_task']},id=rid);self.save(row['task_id'],last_poll=now)
        if not remote:
            self.engine.set_progress(row['task_id'],'remote-result-pending','The other agent accepted the task, but its latest status has not arrived yet. Relay is checking the same task.')
            return Receipt(effect.reference,'pending')
        state=remote['status']['state'];payment=remote.get('metadata',{}).get(PAYMENT_EXTENSION,{})
        if payment.get('quote')!=saved_quote:raise Rejected('REMOTE_QUOTE_CHANGED')
        if state not in ['TASK_STATE_COMPLETED','TASK_STATE_FAILED','TASK_STATE_REJECTED','TASK_STATE_CANCELED']:
            self.engine.set_progress(row['task_id'],'remote-execution',f'The other agent says the task is {state.replace("TASK_STATE_", "").lower().replace("_", " ")}. Waiting for its next update.')
        if state=='TASK_STATE_COMPLETED' and remote.get('artifacts'):
            result={'remote_task_id':row['remote_task'],'provider':row['peer'],'artifacts':remote['artifacts'],'payment_transaction':row['payment_hash'],'paid_amount':str(price),'payment_mode':saved_quote.get('paymentMode','private')}
            self.save(row['task_id'],result=canonical(result).decode());self.engine.set_progress(row['task_id'],'completed','The other agent finished the task. Its result is saved.')
            return Receipt(effect.reference,'confirmed',price,result)
        if state in ['TASK_STATE_FAILED','TASK_STATE_REJECTED','TASK_STATE_CANCELED']:
            self.engine.set_progress(row['task_id'],'remote-failed',f'The other agent ended the task as {state.replace("TASK_STATE_", "").lower()}. Checking whether any payment needs to be returned.')
            if price==0:return Receipt(effect.reference,'rejected',0,{'remote_task_id':row['remote_task'],'state':state})
            if payment.get('paymentState')=='refunded' and payment.get('refundTransaction'):
                refund=json.loads(row['refund_address']);refund_hash=payment['refundTransaction']
                if saved_quote.get('paymentMode','private')=='public':
                    if type(payment_block) is not int:raise Rejected('PAYMENT_CONFIRMATION_BLOCK_MISSING')
                    receipt=self.protocol.public_receipt(refund,saved_quote['recipient'],saved_quote,refund_hash,payment.get('refundProof'),'refund',minimum_block=payment_block)
                else:
                    receipt=self.wallet.invoke('check-payment',{'account':refund['account_id'],'tx_hash':refund_hash,'amount':str(price)})
                if receipt.get('confirmed') is True and receipt.get('amount')==str(price) and receipt.get('receiver_account')==refund['account_id'] and receipt.get('transaction_hash')==refund_hash:
                    with self.engine.tx() as db:
                        used=db.execute('SELECT task_id FROM a2a_verified_refunds WHERE tx_hash=?',(refund_hash,)).fetchone()
                        if used and used[0]!=row['task_id']:raise Rejected('REFUND_TRANSACTION_ALREADY_USED')
                        db.execute('INSERT OR IGNORE INTO a2a_verified_refunds VALUES (?,?)',(refund_hash,row['task_id']))
                    return Receipt(effect.reference,'rejected',0,{'remote_task_id':row['remote_task'],'state':state,'refund_transaction':refund_hash,'refund_verified':True})
        return Receipt(effect.reference,'pending')
