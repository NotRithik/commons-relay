"""Actual policy engine with explicit no-network wallet/storage test doubles."""
from types import SimpleNamespace
import json
import unittest
import test_engine as fixtures
from commons_relay.codec import Rejected, canonical
from commons_relay.meta_adapter import MetaAdapter
from commons_relay.status_views import observed_status, local_status

class StatusTests(unittest.TestCase):
    setUp = fixtures.EngineTests.setUp
    tearDown = fixtures.EngineTests.tearDown
    task = fixtures.EngineTests.task
    request = fixtures.EngineTests.request

    def service(self, wallet=None, error=None):
        calls = []
        def invoke(mode, timeout):
            calls.append((mode, timeout))
            if error: raise Rejected(error)
            return wallet if wallet is not None else {'balance':'7','block':100,'unused_private_field':'never returned'}
        service = SimpleNamespace(engine=self.engine, planner=None,
            get_wallet=lambda:SimpleNamespace(invoke=invoke),
            storage_adapter=lambda:None,
            vault=SimpleNamespace(usage=lambda:{'file_count':2,'plaintext_bytes':'20','ciphertext_reference_bytes':'90'}))
        return service, calls

    def test_meta_status_reports_observed_balance_usage_and_task_counts(self):
        service,calls=self.service()
        task=self.task(skill='meta.status',args={})
        value=self.engine.execute(task['id'],MetaAdapter(service))
        self.assertEqual(value['state'],'completed')
        result=value['result']
        self.assertEqual(result['wallet']['balance'],'7')
        self.assertEqual(result['storage_usage']['file_count'],2)
        self.assertGreaterEqual(result['active_task_count'],1)
        self.assertEqual(calls,[('balance',20)])
        self.assertNotIn('unused_private_field',canonical(result).decode())

    def test_network_reset_is_unknown_balance_not_a_fake_zero(self):
        service,calls=self.service(error='TESTNET_HISTORY_CHANGED')
        result=observed_status(service)
        self.assertIsNone(result['wallet']['balance'])
        self.assertEqual(result['wallet']['error'],'TESTNET_HISTORY_CHANGED')
        self.assertEqual(result['storage_usage']['status'],'observed')

    def test_malformed_wallet_values_are_not_accepted(self):
        for wallet in [{'balance':7,'block':100},{'balance':'7','block':True},{'balance':'-1','block':100}]:
            service,_=self.service(wallet=wallet)
            result=observed_status(service)
            self.assertEqual(result['wallet']['status'],'unavailable')
            self.assertIsNone(result['wallet']['balance'])

    def test_unavailable_storage_does_not_hide_a_verified_balance(self):
        service,_=self.service()
        def unavailable():raise Rejected('STORAGE_UNAVAILABLE')
        service.storage_adapter=unavailable
        result=observed_status(service)
        self.assertEqual(result['wallet']['balance'],'7')
        self.assertEqual(result['storage_usage']['status'],'unavailable')
        self.assertNotIn('file_count',result['storage_usage'])

    def test_large_historical_results_are_not_recursive_status_payloads(self):
        for index in range(12): self.task(skill='storage.list',args={},request_id='status-'+str(index))
        with self.engine.tx() as db:db.execute('UPDATE tasks SET result=?',(json.dumps({'huge':'x'*25000}),))
        service,_=self.service()
        value=observed_status(service)
        self.assertEqual(value['task_count'],12)
        self.assertEqual(len(value['tasks']),8)
        self.assertTrue(value['has_more'])
        self.assertNotIn('huge',canonical(value).decode())
        self.assertLess(len(canonical(value)),14000)

    def test_connection_status_starts_no_wallet_or_model_request(self):
        service,calls=self.service()
        value=local_status(service)
        self.assertEqual(calls,[])
        self.assertIsNone(value['wallet_funded'])
        self.assertEqual(value['wallet_status'],'not-queried')
        self.assertFalse(value['inference_enabled'])

if __name__=='__main__':unittest.main()
