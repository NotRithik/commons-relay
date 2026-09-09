import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from a2a_fixtures import ProtocolFixture
from commons_relay.a2a_protocol import RpcError
from commons_relay.a2a_types import PAYMENT_EXTENSION,BINDING_EXTENSION,verify_card
from commons_relay.codec import Rejected
from commons_relay.engine import Prepared,Receipt

class ReadFixture:
    def prepare(self,task):return Prepared('read:'+task['id'],0,task['id'])
    def broadcast(self,effect):pass
    def lookup(self,effect):return Receipt(effect.reference,'confirmed',0,{'value':42})

class A2AProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();base=Path(self.tmp.name)
        self.provider=ProtocolFixture(base,'provider');self.client=ProtocolFixture(base,'client');self.other=ProtocolFixture(base,'other')
        for a in [self.provider,self.client,self.other]:
            for b in [self.provider,self.client,self.other]:
                if a is not b:a.link(b)
        self.p=self.provider.protocol
    def tearDown(self):
        for a in [self.provider,self.client,self.other]:a.close()
        self.tmp.cleanup()
    def create(self,params=None,peer='client'):
        doc=self.p.send_message(peer,params or self.provider.params());return doc['task']
    def payment(self,task,hash='f'*64):
        quote=task['metadata'][PAYMENT_EXTENSION]['quote'];row=self.p._row(task['id']);self.provider.pay(row,hash)
        return {'message':{'messageId':'payment-'+hash[:4],'taskId':task['id'],'contextId':task['contextId'],'role':'ROLE_USER','parts':[{'data':{'payment':{'quoteId':quote['id'],'transactionHash':hash}}}]},'configuration':{'returnImmediately':True}}
    def rpc(self,method,params,id='r1',peer='client'):
        return self.p.handle_request(peer,{'jsonrpc':'2.0','id':id,'method':method,'params':params})
    def test_card_is_signed_and_has_prices_and_input_schemas(self):
        card=self.p.card();plain=verify_card(card,self.provider.mailbox.contact().signing_key,self.provider.vault.signer)
        self.assertEqual(plain['supportedInterfaces'][0]['protocolVersion'],'1.0');self.assertEqual(plain['capabilities']['extensions'][1]['params']['prices']['program.query'],'3')
    def test_card_cache_checks_known_peer_key(self):
        value=self.client.protocol.remember_card('provider',self.p.card());self.assertEqual(value['name'],'provider');self.assertEqual(len(self.client.protocol.cards()),1)
    def test_card_address_mismatch_rejected(self):
        with self.assertRaises(Rejected):self.client.protocol.remember_card('other',self.p.card())
    def test_task_starts_payment_required_without_execution(self):
        task=self.create();self.assertEqual(task['status']['state'],'TASK_STATE_INPUT_REQUIRED');self.assertEqual(self.provider.scheduled,[])
        quote=task['metadata'][PAYMENT_EXTENSION]['quote'];self.assertEqual(quote['amount'],'3');self.assertEqual(quote['client'],'client')
    def test_receiver_is_fresh_per_task(self):
        a=self.create();b=self.create(self.provider.params(message_id='msg2'))
        self.assertNotEqual(a['metadata'][PAYMENT_EXTENSION]['quote']['recipient']['account_id'],b['metadata'][PAYMENT_EXTENSION]['quote']['recipient']['account_id'])
    def test_duplicate_message_id_returns_same_invoice(self):
        a=self.create();b=self.create();self.assertEqual(a,b);self.assertEqual(self.provider.wallet.n,1)
    def test_reused_message_id_with_different_args_rejected(self):
        self.create();params=self.provider.params();params['message']['parts'][0]['data']['arguments']['program_id']='ff'*32
        with self.assertRaises(RpcError):self.create(params)
    def test_unexported_owner_wallet_send_rejected(self):
        params=self.provider.params(skill='wallet.send')
        with self.assertRaises(RpcError):self.create(params)
    def test_bad_argument_types_rejected_before_invoice(self):
        params=self.provider.params();params['message']['parts'][0]['data']['arguments']['program_id']=False
        with self.assertRaises(Rejected):self.create(params)
        self.assertEqual(self.provider.wallet.n,0)
    def test_wrong_message_role_rejected(self):
        params=self.provider.params();params['message']['role']='user'
        with self.assertRaises(RpcError):self.create(params)
    def test_missing_refund_descriptor_rejected(self):
        params=self.provider.params();del params['message']['parts'][0]['data']['refundAddress']
        with self.assertRaises(RpcError):self.create(params)
    def test_payment_verified_before_work_is_scheduled(self):
        task=self.create();out=self.p.send_message('client',self.payment(task));self.assertEqual(out['task']['status']['state'],'TASK_STATE_WORKING');self.assertEqual(len(self.provider.scheduled),1)
    def test_unconfirmed_payment_cannot_unlock_work(self):
        task=self.create();p=self.payment(task);self.provider.wallet.receipts.clear()
        with self.assertRaises(RpcError):self.p.send_message('client',p)
        self.assertEqual(self.provider.scheduled,[])
    def test_wrong_amount_does_not_unlock_service(self):
        task=self.create();p=self.payment(task);self.provider.wallet.receipts['f'*64]['amount']='2'
        with self.assertRaises(Rejected):self.p.send_message('client',p)
    def test_wrong_receiver_does_not_unlock_service(self):
        task=self.create();p=self.payment(task);self.provider.wallet.receipts['f'*64]['receiver_account']='ee'*32
        with self.assertRaises(Rejected):self.p.send_message('client',p)
    def test_wrong_quote_id_rejected(self):
        task=self.create();p=self.payment(task);p['message']['parts'][0]['data']['payment']['quoteId']='other'
        with self.assertRaises(RpcError):self.p.send_message('client',p)
    def test_payment_bound_to_original_peer(self):
        task=self.create();p=self.payment(task)
        with self.assertRaises(RpcError):self.p.send_message('other',p)
    def test_duplicate_payment_schedules_once(self):
        task=self.create();p=self.payment(task);self.p.send_message('client',p);self.p.send_message('client',p);self.assertEqual(len(self.provider.scheduled),1)
    def test_hash_cannot_change_after_payment(self):
        task=self.create();p=self.payment(task);self.p.send_message('client',p);p['message']['parts'][0]['data']['payment']['transactionHash']='a'*64
        with self.assertRaises(RpcError):self.p.send_message('client',p)
    def test_fulfillment_populates_v1_artifact_and_terminal_state(self):
        task=self.create();self.p.send_message('client',self.payment(task));local=self.provider.scheduled[0];self.provider.engine.execute(local,ReadFixture());self.p.tick()
        doc=self.p.document(task['id']);self.assertEqual(doc['status']['state'],'TASK_STATE_COMPLETED');self.assertEqual(doc['artifacts'][0]['parts'][0]['data'],{'value':42})
    def test_cancel_before_payment_does_not_start_refund(self):
        task=self.create();out=self.rpc('CancelTask',{'id':task['id']});self.assertEqual(out['result']['status']['state'],'TASK_STATE_CANCELED');self.assertEqual(self.provider.engine.db.execute('SELECT COUNT(*) FROM a2a_refunds').fetchone()[0],0)
    def test_late_payment_after_cancel_queues_refund_not_work(self):
        task=self.create();self.rpc('CancelTask',{'id':task['id']});self.p.send_message('client',self.payment(task));row=self.p._row(task['id']);self.assertEqual(row['payment_state'],'refund-pending');self.assertEqual(self.provider.scheduled,[])
    def test_cancel_paid_queued_task_queues_full_refund(self):
        task=self.create();self.p.send_message('client',self.payment(task));self.rpc('CancelTask',{'id':task['id']});self.assertEqual(self.p._row(task['id'])['payment_state'],'refund-pending')
    def test_cannot_cancel_working_local_effect(self):
        task=self.create();self.p.send_message('client',self.payment(task));self.provider.engine.start(self.provider.scheduled[0],'worker');result=self.rpc('CancelTask',{'id':task['id']});self.assertEqual(result['error']['code'],-32002)
    def test_refund_uses_durable_hash_then_confirmed_receipt(self):
        task=self.create();self.p.send_message('client',self.payment(task));self.rpc('CancelTask',{'id':task['id']});self.p._refund(task['id']);row=self.p._row(task['id']);self.assertEqual(row['payment_state'],'refunded');self.assertEqual(len(row['refund_hash']),64)
    def test_refund_replay_reuses_wallet_operation_id(self):
        task=self.create();self.p.send_message('client',self.payment(task));self.rpc('CancelTask',{'id':task['id']});self.p._refund(task['id']);self.p._refund(task['id']);self.assertEqual(len(self.provider.wallet.operations),1)
    def test_get_task_other_peer_is_not_found(self):
        task=self.create();result=self.rpc('GetTask',{'id':task['id']},peer='other');self.assertEqual(result['error']['code'],-32001)
    def test_subscribe_ongoing_task_returns_initial_task(self):
        task=self.create();r=self.rpc('SubscribeToTask',{'id':task['id']});self.assertEqual(r['result']['task']['id'],task['id'])
    def test_subscribe_terminal_task_rejected(self):
        task=self.create();self.rpc('CancelTask',{'id':task['id']});r=self.rpc('SubscribeToTask',{'id':task['id']},id='r2');self.assertEqual(r['error']['code'],-32004)
    def test_status_stream_updates_are_durable_and_deduplicated(self):
        task=self.create();self.rpc('SubscribeToTask',{'id':task['id']})
        self.p.send_message('client',self.payment(task));self.p.tick()
        self.assertEqual(self.provider.mailbox.db.execute("SELECT COUNT(*) FROM outbox WHERE kind='a2a-event'").fetchone()[0],0)
        # Simulate the transport acknowledgment of the initial Task response.
        with self.provider.mailbox.tx() as db:db.execute("UPDATE outbox SET state='acknowledged' WHERE kind='a2a-response'")
        self.p.tick();count=self.provider.mailbox.db.execute("SELECT COUNT(*) FROM outbox WHERE kind='a2a-event'").fetchone()[0]
        self.p.tick();self.assertEqual(self.provider.mailbox.db.execute("SELECT COUNT(*) FROM outbox WHERE kind='a2a-event'").fetchone()[0],count)
        self.assertGreater(count,0)
    def test_rpc_replay_returns_original_response(self):
        params=self.provider.params();a=self.rpc('SendMessage',params);b=self.rpc('SendMessage',params);self.assertEqual(a,b)
    def test_rpc_id_cannot_change_method(self):
        self.rpc('GetExtendedAgentCard',{})
        with self.assertRaises(Rejected):self.rpc('SendMessage',self.provider.params())
    def test_unknown_method_is_json_rpc_error(self):
        self.assertEqual(self.rpc('exec',{})['error']['code'],-32601)
    def test_bad_json_rpc_version_is_rejected(self):
        with self.assertRaises(Rejected):self.p.handle_request('client',{'jsonrpc':'1.0','id':'r','method':'GetTask','params':{}})
    def test_unknown_rpc_response_cannot_change_local_task(self):
        with self.assertRaises(Rejected):self.client.protocol.handle_message({'kind':'a2a-response','sender':'provider','payload':{'jsonrpc':'2.0','id':'unknown','result':{}}})
    def test_response_must_be_from_requested_peer(self):
        self.client.protocol.request('provider','GetExtendedAgentCard',{},id='r')
        with self.assertRaises(Rejected):self.client.protocol.handle_message({'kind':'a2a-response','sender':'other','payload':{'jsonrpc':'2.0','id':'r','result':{}}})
    def test_free_task_uses_same_engine_without_payment(self):
        self.p.config['exports']['program.query']['price']='0';task=self.create();self.assertEqual(task['status']['state'],'TASK_STATE_WORKING');self.assertEqual(len(self.provider.scheduled),1)
    def test_blocking_free_send_waits_for_terminal_response(self):
        self.p.config['exports']['program.query']['price']='0';params=self.provider.params();params['configuration']['returnImmediately']=False
        self.assertIsNone(self.rpc('SendMessage',params));self.assertEqual(len(self.provider.scheduled),1)
        self.provider.engine.execute(self.provider.scheduled[0],ReadFixture());self.p.tick();self.p.tick()
        row=self.provider.engine.db.execute('SELECT response FROM a2a_rpc_requests WHERE id=?',('r1',)).fetchone();self.assertEqual(json.loads(row[0])['result']['task']['status']['state'],'TASK_STATE_COMPLETED')
    def test_payment_required_reaches_interrupted_state_for_blocking_send(self):
        params=self.provider.params();params['configuration']['returnImmediately']=False
        task=self.create(params);self.assertEqual(task['status']['state'],'TASK_STATE_INPUT_REQUIRED')
    def test_task_state_survives_protocol_restart(self):
        task=self.create();self.p.close();self.provider.protocol=self.p=type(self.p)(self.provider.service);self.assertEqual(self.p.document(task['id'])['status']['state'],'TASK_STATE_INPUT_REQUIRED')
    def test_invoice_binds_refund_account_hash(self):
        task=self.create();from commons_relay.codec import digest
        expected=digest(self.provider.params()['message']['parts'][0]['data']['refundAddress']);self.assertEqual(task['metadata'][PAYMENT_EXTENSION]['quote']['refundAddressHash'],expected)
    def test_out_of_order_status_does_not_rewind(self):
        task=self.create();self.client.protocol.remember_task('provider',task);later=json.loads(json.dumps(task));later['metadata'][BINDING_EXTENSION]['sequence']='5';later['status']['state']='TASK_STATE_WORKING';self.client.protocol.remember_task('provider',later);self.client.protocol.remember_task('provider',task);self.assertEqual(self.client.protocol.remote_task('provider',task['id'])['status']['state'],'TASK_STATE_WORKING')
if __name__=='__main__':unittest.main()
