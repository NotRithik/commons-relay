"""Peer pings never enter the invoicing or payment path. Fixtures are not network evidence."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from a2a_fixtures import ProtocolFixture
from commons_relay.a2a_adapter import A2AAdapter
from commons_relay.codec import Rejected
from commons_relay.engine import REQUEST_DOMAIN
from commons_relay.signing import sign_envelope

class PeerPingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.client=ProtocolFixture(root,'client');self.provider=ProtocolFixture(root,'provider')
        self.client.link(self.provider);self.provider.link(self.client)
        self.adapter=A2AAdapter(self.client.service)
        self.calls=[]
        def response(request_id,timeout):
            self.calls.append((request_id,timeout))
            row=self.client.engine.db.execute('SELECT request FROM a2a_client_requests WHERE id=?',(request_id,)).fetchone()
            rpc=json.loads(row[0])
            self.assertEqual(rpc['method'],'GetExtendedAgentCard')
            return self.provider.protocol.handle_request('client',rpc)['result']
        self.client.protocol.await_response=response
        self.task={'id':'ping-fixture','skill':'agent.ping','arguments':{'agent_address':'provider'},'maximum_spend':'0'}
    def tearDown(self):
        self.client.close();self.provider.close();self.tmp.cleanup()
    def test_live_response_is_verified_without_wallet_or_invoice(self):
        effect=self.adapter.prepare(self.task);self.adapter.broadcast(effect)
        receipt=self.adapter.lookup(effect)
        self.assertEqual(receipt.state,'confirmed');self.assertTrue(receipt.result['reachable'])
        self.assertEqual(receipt.actual_spend,0);self.assertEqual(self.client.wallet.calls,[])
        self.assertEqual(self.provider.wallet.calls,[])
        self.assertEqual(self.provider.engine.db.execute('SELECT count(*) FROM a2a_tasks').fetchone()[0],0)
    def test_timeout_is_unknown_not_a_claim_of_offline(self):
        self.client.protocol.await_response=Mock(side_effect=Rejected('A2A_RESPONSE_PENDING'))
        effect=self.adapter.prepare(self.task);receipt=self.adapter.lookup(effect)
        self.assertIsNone(receipt.result['reachable']);self.assertEqual(receipt.result['status'],'no-reply')
        self.assertEqual(self.client.wallet.calls,[])
    def test_wrongly_signed_card_cannot_claim_peer_is_reachable(self):
        card=self.provider.protocol.card();card['description']='tampered'
        self.client.protocol.await_response=lambda *a,**kw:card
        with self.assertRaises(Rejected):self.adapter.prepare(self.task)
    def test_replaying_task_returns_original_observation_time(self):
        effect=self.adapter.prepare(self.task);original=self.adapter.lookup(effect).result
        self.client.now+=40
        self.adapter.prepare(self.task)
        self.assertEqual(self.adapter.lookup(effect).result,original)
        self.assertEqual(len(self.calls),1)
    def test_old_probe_after_interrupted_prepare_does_not_renew_freshness(self):
        self.client.protocol.request('provider','GetExtendedAgentCard',{},id='ping-ping-fixture')
        self.client.now+=11
        effect=self.adapter.prepare(self.task)
        self.assertEqual(self.adapter.lookup(effect).result['status'],'stale-probe')
        self.assertEqual(self.calls,[])
    def test_ping_cannot_authorize_payment(self):
        task={**self.task,'maximum_spend':'3'}
        with self.assertRaisesRegex(Rejected,'A2A_PING_CANNOT_SPEND'):self.adapter.prepare(task)
        self.assertEqual(self.client.wallet.calls,[])
