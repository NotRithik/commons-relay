import json
from pathlib import Path
import unittest
import test_engine as fixtures
from commons_relay.wallet_adapter import WalletAdapter
from commons_relay.codec import Rejected
from commons_relay.engine import Prepared

class WalletWire:
    def __init__(self):self.calls=[];self.hash='a'*64;self.state='prepared';self.block=42;self.amount='50';self.reply_hash=None
    def call(self,action,params,timeout=100):
        self.calls.append((action,params))
        if action=='wallet.init':return True
        mode=params['mode']
        if mode=='prepare':return {'state':self.state,'maximum_spend':self.amount,'tx_hash':self.reply_hash or self.hash}
        if mode=='broadcast':self.state='confirmed';return {'state':self.state,'tx_hash':self.reply_hash or self.hash}
        if mode=='reconcile':return {'state':self.state,'tx_hash':self.reply_hash or self.hash,'block_id':self.block}
        if mode=='balance':return {'balance':'50','shielded':True}
        if mode=='history':return {'operations':[]}
        if mode=='query':return {'program_owner':[1]*8,'data_borsh_hex':'00'}
        raise AssertionError(mode)

class WalletAdapterTests(unittest.TestCase):
    setUp=fixtures.EngineTests.setUp
    tearDown=fixtures.EngineTests.tearDown
    request=fixtures.EngineTests.request
    task=fixtures.EngineTests.task
    def adapter(self):
        wire=WalletWire();p=self.root/'payment-addresses.json';p.write_text(json.dumps({'testnet-recipient':{'account_id':'b'*64,'npk':'c'*64,'vpk_borsh':'aa','identifier':'0'}}))
        return WalletAdapter(self.root,wire,self.engine),wire
    def task_for_adapter(self,task):
        task=self.engine.get(task['id']);task['arguments']=json.loads(self.engine.db.execute('SELECT args FROM tasks WHERE id=?',(task['id'],)).fetchone()[0]);return task
    def test_send_prepares_known_hash_before_broadcast(self):
        adapter,wire=self.adapter();task=self.task_for_adapter(self.task());prepared=adapter.prepare(task)
        self.assertEqual(prepared.reference,'a'*64);self.assertEqual(prepared.maximum_spend,50);self.assertFalse(any(p.get('mode')=='broadcast' for a,p in wire.calls))
        adapter.broadcast(prepared);receipt=adapter.lookup(prepared);self.assertEqual(receipt.state,'confirmed');self.assertEqual(receipt.actual_spend,50)
    def test_unknown_recipient_blocked(self):
        adapter,_=self.adapter();task=self.task_for_adapter(self.task(args={'recipient':'unknown','amount':'50'}))
        with self.assertRaises(Rejected):adapter.prepare(task)
    def test_quote_mismatch_blocked(self):
        adapter,wire=self.adapter();wire.amount='51'
        with self.assertRaises(Rejected):adapter.prepare(self.task_for_adapter(self.task()))
    def test_missing_hash_blocked(self):
        adapter,wire=self.adapter();wire.reply_hash='not-a-hash'
        with self.assertRaises(Rejected):adapter.prepare(self.task_for_adapter(self.task()))
    def test_bad_block_not_confirmed(self):
        adapter,wire=self.adapter();effect=adapter.prepare(self.task_for_adapter(self.task()));wire.state='confirmed';wire.block=None
        with self.assertRaises(Rejected):adapter.lookup(effect)
    def test_wrong_receipt_hash_not_confirmed(self):
        adapter,wire=self.adapter();effect=adapter.prepare(self.task_for_adapter(self.task()));wire.state='confirmed';wire.reply_hash='b'*64
        with self.assertRaises(Rejected):adapter.lookup(effect)
    def test_pending_receipt_does_not_claim_spending(self):
        adapter,wire=self.adapter();effect=adapter.prepare(self.task_for_adapter(self.task()));self.assertEqual(adapter.lookup(effect).state,'pending')
    def test_corrupted_effect_reference_blocked(self):
        adapter,_=self.adapter();effect=adapter.prepare(self.task_for_adapter(self.task()))
        with self.assertRaises(Rejected):adapter.broadcast(Prepared('b'*64,50,effect.opaque_handle))
    def test_balance_uses_zero_spend_and_real_adapter_result(self):
        adapter,wire=self.adapter();effect=adapter.prepare(self.task_for_adapter(self.task(skill='wallet.balance',args={})));adapter.broadcast(effect)
        self.assertEqual(adapter.lookup(effect).result['balance'],'50');self.assertFalse(any(p.get('mode')=='broadcast' for a,p in wire.calls))
    def test_history_is_read_only(self):
        adapter,_=self.adapter();effect=adapter.prepare(self.task_for_adapter(self.task(skill='wallet.history',args={})));self.assertEqual(adapter.lookup(effect).result,{'operations':[]})
    def test_unknown_skill_not_silently_substituted(self):
        adapter,_=self.adapter();task=self.task_for_adapter(self.task(skill='storage.list',args={}))
        with self.assertRaises(Rejected):adapter.prepare(task)
    def test_query_must_match_program(self):
        adapter,_=self.adapter();task=self.task_for_adapter(self.task(skill='program.query',args={'program_id':'ff'*32,'params':{'account':'bb'*32}}))
        with self.assertRaises(Rejected):adapter.prepare(task)
    def test_recipient_file_symlink_refused(self):
        adapter,_=self.adapter();path=self.root/'payment-addresses.json';path.rename(self.root/'other');path.symlink_to(self.root/'other')
        with self.assertRaises(Rejected):adapter.prepare(self.task_for_adapter(self.task()))
    def test_restart_does_not_rebroadcast_known_effect(self):
        adapter,wire=self.adapter();effect=adapter.prepare(self.task_for_adapter(self.task()));adapter.broadcast(effect)
        replacement=WalletAdapter(self.root,wire,self.engine);self.assertEqual(replacement.lookup(effect).state,'confirmed');self.assertEqual(sum(p.get('mode')=='broadcast' for a,p in wire.calls),1)
if __name__=='__main__':unittest.main()
