"""Protocol fixtures only. They do not represent a network or paid-chain run."""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import os
from commons_relay.engine import Engine,Policy
from commons_relay.file_crypto import Sodium
from commons_relay.vault import Vault
from commons_relay.messaging import Mailbox
from commons_relay.signing import Ed25519
from commons_relay.a2a_protocol import AgentProtocol
from commons_relay.codec import Rejected

class WalletFixture:
    def __init__(self):self.n=0;self.operations={};self.receipts={};self.calls=[]
    def invoke(self,mode,params=None,timeout=120):
        self.calls.append((mode,params));params=params or {}
        if mode=='receive-address':
            self.n+=1;return {'account_id':f'{self.n:064x}','npk':'ab'*32,'vpk_borsh':'00'*32,'identifier':str(self.n)}
        if mode=='check-payment':
            receipt=self.receipts.get(params['tx_hash'])
            if not receipt:return {'confirmed':False}
            if receipt['receiver_account']!=params['account'] or receipt['amount']!=params['amount']:raise Rejected('PAYMENT_AMOUNT_OR_PROGRAM_MISMATCH')
            return receipt
        if mode=='prepare':
            id=params['operation_id'];old=self.operations.get(id)
            if old:return old
            op={'state':'prepared','maximum_spend':params['intent']['arguments']['amount'],'tx_hash':hashlib.sha256(id.encode()).hexdigest()}
            self.operations[id]=op;return op
        if mode=='broadcast':self.operations[params['operation_id']]['state']='confirmed';return self.operations[params['operation_id']]
        if mode=='reconcile':return self.operations[params['operation_id']]
        raise AssertionError(mode)

class ProtocolFixture:
    def __init__(self,base:Path,name,price='3',exports=None):
        self.root=base/name;self.root.mkdir();self.now=10000
        for p in ['inputs','outputs']:(self.root/p).mkdir()
        self.crypto=Sodium(os.environ.get('COMMONS_RELAY_TEST_SODIUM'))
        self.vault=Vault(self.root/'vault',self.root/'inputs',self.root/'outputs',self.crypto)
        self.mailbox=Mailbox(self.root/'mail',self.vault,name,clock=lambda:self.now)
        signer=Ed25519(self.root/'owner');self.owner_key=signer.scratch/'owner.pem';self.owner=signer.generate(self.owner_key)
        self.engine=Engine(self.root/'ledger',name,self.owner,signer,policy=Policy(per_transaction=5,per_period=30,hard_maximum=50,approval_ttl=7200),clock=lambda:self.now)
        cfg={'name':name,'description':'Fixture service','exports':exports if exports is not None else {'program.query':{'price':price,'description':'Read a public program account'}},'discovery_topic':'fixture'}
        (self.root/'services.json').write_text(json.dumps(cfg));(self.root/'services.json').chmod(0o600)
        self.wallet=WalletFixture();self.scheduled=[]
        self.runtime=SimpleNamespace(mailbox=self.mailbox,start=lambda:None,discovery_handlers={})
        self.controller=SimpleNamespace(schedule=lambda id:self.scheduled.append(id),start=lambda:None)
        self.service=SimpleNamespace(root=self.root,engine=self.engine,get_messaging=lambda:self.runtime,get_wallet=lambda:self.wallet,get_controller=lambda:self.controller)
        self.protocol=AgentProtocol(self.service);self.service.get_agent_protocol=lambda:self.protocol
    def link(self,other):self.mailbox.add_contact(other.mailbox.contact())
    def close(self):self.protocol.close();self.mailbox.close();self.vault.close();self.engine.close()
    def pay(self,task,hash='f'*64):
        quote=json.loads(task['quote']);self.wallet.receipts[hash]={'confirmed':True,'transaction_hash':hash,'block_id':9,'receiver_account':quote['recipient']['account_id'],'amount':quote['amount'],'private':True}
        return hash
    def params(self,peer='client',message_id='msg1',skill='program.query',price_free=False):
        return {'message':{'messageId':message_id,'role':'ROLE_USER','parts':[{'data':{'skill':skill,'arguments':{'program_id':'ab'*32,'params':{'account':'cd'*32}} if skill=='program.query' else {},'refundAddress':{'account_id':'de'*32,'npk':'ef'*32,'vpk_borsh':'00'*32,'identifier':'1'}}}]},'configuration':{'returnImmediately':True}}
