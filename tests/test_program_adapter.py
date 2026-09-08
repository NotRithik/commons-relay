import json
from pathlib import Path
import unittest
import test_engine as fixtures
from commons_relay.program_adapter import ProgramAdapter
from commons_relay.engine import Prepared
from commons_relay.codec import Rejected

class WalletFixture:
    def __init__(self):self.calls=[];self.state='prepared';self.tx='a'*64;self.program='b'*64
    def invoke(self,mode,params,timeout=120):
        self.calls.append((mode,params,timeout))
        if mode=='program-account':return {'account_id':'03'*32,'wallet_owned':True,'public':True}
        if mode=='prepare-program':
            return {'state':self.state,'maximum_spend':'0','tx_hash':self.tx,'program_id':self.program if params['intent']['kind']=='program-deploy' else None}
        if mode=='broadcast':return {'state':'confirmed','maximum_spend':'0','tx_hash':self.tx,'program_id':self.program,'block_id':7}
        if mode=='reconcile':return {'state':self.state,'maximum_spend':'0','tx_hash':self.tx,'program_id':self.program,'block_id':7 if self.state=='confirmed' else None}
        raise AssertionError(mode)

class ProgramAdapterTests(unittest.TestCase):
    setUp=fixtures.EngineTests.setUp
    tearDown=fixtures.EngineTests.tearDown
    request=fixtures.EngineTests.request
    task=fixtures.EngineTests.task
    def adapter(self):
        (self.root/'inputs').mkdir(exist_ok=True);wallet=WalletFixture();return ProgramAdapter(self.root,wallet,self.engine),wallet
    def full(self,task):return self.engine.get(task['id'])
    def call_args(self):return {'program_id':'01'*32,'instruction':'00000000','params':{'accounts':[{'account_id':'02'*32,'signer':True}]}}
    def test_call_is_owner_gated_before_adapter(self):
        task=self.task(skill='program.call',args=self.call_args());self.assertEqual(task['state'],'input-required')
    def test_deploy_is_owner_gated_before_adapter(self):
        (self.root/'inputs').mkdir();(self.root/'inputs/p.elf').write_bytes(b'ELF')
        task=self.task(skill='program.deploy',args={'binary_path':'p.elf'});self.assertEqual(task['state'],'input-required')
    def test_valid_public_call_prepares_zero_spend_bound_hash(self):
        adapter,wallet=self.adapter();task=self.full(self.task(skill='program.call',args=self.call_args()));prepared=adapter.prepare(task)
        self.assertEqual(prepared.reference,'a'*64);self.assertEqual(prepared.maximum_spend,0);self.assertEqual(wallet.calls[0][1]['intent']['kind'],'program-call-public')
    def test_self_account_is_resolved_inside_wallet_boundary(self):
        adapter,wallet=self.adapter();args=self.call_args();args['params']['accounts'][0]['account_id']='self';adapter.prepare(self.full(self.task(skill='program.call',args=args)))
        intent=next(params['intent'] for mode,params,_ in wallet.calls if mode=='prepare-program')
        self.assertEqual(intent['arguments']['params']['accounts'][0]['account_id'],'03'*32)
        self.assertTrue(any(mode=='program-account' for mode,_,_ in wallet.calls))
    def test_call_requires_at_least_one_wallet_signer(self):
        adapter,_=self.adapter();args=self.call_args();args['params']['accounts'][0]['signer']=False
        with self.assertRaises(Rejected):adapter.prepare(self.full(self.task(skill='program.call',args=args)))
    def test_call_rejects_unaligned_instruction_words(self):
        adapter,_=self.adapter();args=self.call_args();args['instruction']='00'
        with self.assertRaises(Rejected):adapter.prepare(self.full(self.task(skill='program.call',args=args)))
    def test_call_rejects_too_many_accounts(self):
        adapter,_=self.adapter();args=self.call_args();args['params']['accounts']*=17
        with self.assertRaises(Rejected):adapter.prepare(self.full(self.task(skill='program.call',args=args)))
    def test_deploy_path_cannot_escape_input_root(self):
        adapter,_=self.adapter();outside=self.root/'outside';outside.write_bytes(b'x')
        with self.assertRaises(Rejected):adapter.prepare(self.full(self.task(skill='program.deploy',args={'binary_path':'../outside'})))
    def test_deploy_symlink_is_refused(self):
        adapter,_=self.adapter();outside=self.root/'outside';outside.write_bytes(b'x');(self.root/'inputs/link').symlink_to(outside)
        with self.assertRaises(Rejected):adapter.prepare(self.full(self.task(skill='program.deploy',args={'binary_path':'link'})))
    def test_deploy_returns_program_id_bound_to_effect(self):
        adapter,_=self.adapter();(self.root/'inputs/p.elf').write_bytes(b'elf-fixture');effect=adapter.prepare(self.full(self.task(skill='program.deploy',args={'binary_path':'p.elf'})))
        self.assertEqual(adapter.row(effect.opaque_handle)['program_id'],'b'*64)
    def test_bad_deployment_program_id_rejected(self):
        adapter,wallet=self.adapter();wallet.program='bad';(self.root/'inputs/p.elf').write_bytes(b'elf-fixture')
        with self.assertRaises(Rejected):adapter.prepare(self.full(self.task(skill='program.deploy',args={'binary_path':'p.elf'})))
    def test_broadcast_cannot_change_transaction_hash(self):
        adapter,wallet=self.adapter();effect=adapter.prepare(self.full(self.task(skill='program.call',args=self.call_args())));wallet.tx='c'*64
        with self.assertRaises(Rejected):adapter.broadcast(effect)
    def test_pending_receipt_does_not_claim_confirmation(self):
        adapter,wallet=self.adapter();effect=adapter.prepare(self.full(self.task(skill='program.call',args=self.call_args())));self.assertEqual(adapter.lookup(effect).state,'pending')
    def test_confirmed_receipt_is_zero_spend_and_has_block(self):
        adapter,wallet=self.adapter();effect=adapter.prepare(self.full(self.task(skill='program.call',args=self.call_args())));wallet.state='confirmed';receipt=adapter.lookup(effect)
        self.assertEqual(receipt.state,'confirmed');self.assertEqual(receipt.actual_spend,0);self.assertEqual(receipt.result['block_id'],7)
    def test_effect_reference_is_bound_before_broadcast(self):
        adapter,_=self.adapter();effect=adapter.prepare(self.full(self.task(skill='program.call',args=self.call_args())))
        with self.assertRaises(Rejected):adapter.broadcast(Prepared('c'*64,0,effect.opaque_handle))
if __name__=='__main__':unittest.main()
