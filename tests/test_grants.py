import unittest
import test_engine as fixtures
from commons_relay.engine import GRANT_DOMAIN,DELEGATED_DOMAIN
from commons_relay.codec import Rejected
from commons_relay.signing import sign_envelope,key_id

class GrantTests(unittest.TestCase):
    setUp = fixtures.EngineTests.setUp
    tearDown = fixtures.EngineTests.tearDown
    restart = fixtures.EngineTests.restart
    def grant(self,maximum='200',steps=5,skills=None,grant_id='goal1'):
        body={'domain':GRANT_DOMAIN,'agent_id':'fixture-agent','grant_id':grant_id,'delegate_key_id':key_id(self.peer),
              'goal':'A general owner-authorized workflow, not a preselected demo','allowed_skills':skills or ['wallet.send'],
              'maximum_spend':maximum,'max_steps':steps,'expires_at':self.clock.value+500,'policy_version':1}
        self.engine.register_grant(sign_envelope(body,self.key,self.crypto));return body
    def proposed(self,grant='goal1',value='50',request_id='step1',skill='wallet.send',arguments=None):
        body={'domain':DELEGATED_DOMAIN,'agent_id':'fixture-agent','grant_id':grant,'request_id':request_id,'skill':skill,
              'arguments':arguments if arguments is not None else {'recipient':'testnet-recipient','amount':value},'expires_at':self.clock.value+300}
        return self.engine.submit(sign_envelope(body,self.other_key,self.crypto),self.peer)
    def test_delegate_under_grant_can_propose_general_tool(self):
        self.grant();self.assertEqual(self.proposed()['state'],'submitted')
    def test_no_grant_no_effect(self):
        with self.assertRaises(Rejected):self.proposed()
    def test_goal_limit_not_global_limit(self):
        self.grant('60');self.proposed()
        with self.assertRaises(Rejected):self.proposed(request_id='step2',value='11')
    def test_step_cap(self):
        self.grant(steps=1);self.proposed()
        with self.assertRaises(Rejected):self.proposed(request_id='step2')
    def test_scope_limits(self):
        self.grant()
        with self.assertRaises(Rejected):self.proposed(skill='wallet.balance',arguments={})
    def test_replay_does_not_consume_second_step(self):
        self.grant(steps=1);a=self.proposed();self.assertEqual(a['id'],self.proposed()['id'])
    def test_delegate_still_needs_owner_above_per_tx(self):
        self.grant();self.assertEqual(self.proposed(value='150')['state'],'input-required')
    def test_revoke_cancels_queued_reservation(self):
        self.grant();t=self.proposed();body={'domain':'commons/commons_relay/revoke/v1','agent_id':'fixture-agent','grant_id':'goal1','expires_at':self.clock.value+50}
        self.engine.revoke_grant(sign_envelope(body,self.key,self.crypto));self.assertEqual(self.engine.get(t['id'])['state'],'canceled');self.assertEqual(self.engine.usage(),0)
    def test_delegate_cannot_create_grants(self):
        body=self.grant(grant_id='old');body['grant_id']='forged'
        with self.assertRaises(Rejected):self.engine.register_grant(sign_envelope(body,self.other_key,self.crypto))
    def test_restart_preserves_goal_budget(self):
        self.grant(steps=1);self.proposed();self.restart()
        with self.assertRaises(Rejected):self.proposed(request_id='step2')
    def test_grant_expires_before_broadcast(self):
        from commons_relay.engine import Prepared
        self.grant();t=self.proposed();_,lease=self.engine.start(t['id'],'w');self.engine.record_prepared(t['id'],lease,Prepared('r',50,'h'))
        self.clock.value+=501
        with self.assertRaises(Rejected):self.engine.mark_broadcasting(t['id'],lease)
    def test_revocation_checked_before_broadcast(self):
        from commons_relay.engine import Prepared
        self.grant();t=self.proposed();_,lease=self.engine.start(t['id'],'w');self.engine.record_prepared(t['id'],lease,Prepared('r',50,'h'))
        body={'domain':'commons/commons_relay/revoke/v1','agent_id':'fixture-agent','grant_id':'goal1','expires_at':self.clock.value+50}
        self.engine.revoke_grant(sign_envelope(body,self.key,self.crypto))
        with self.assertRaises(Rejected):self.engine.mark_broadcasting(t['id'],lease)
