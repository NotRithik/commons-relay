from dataclasses import asdict
from types import SimpleNamespace
import unittest
import test_engine as fixtures
from commons_relay.meta_adapter import MetaAdapter
from commons_relay.engine import Policy,GRANT_DOMAIN,DELEGATED_DOMAIN
from commons_relay.codec import Rejected
from commons_relay.signing import sign_envelope,key_id
class MetaTests(unittest.TestCase):
    setUp=fixtures.EngineTests.setUp
    tearDown=fixtures.EngineTests.tearDown
    request=fixtures.EngineTests.request
    task=fixtures.EngineTests.task
    restart=fixtures.EngineTests.restart
    def adapter(self):return MetaAdapter(SimpleNamespace(engine=self.engine,controller=None))
    def test_registry_contains_required_meta_configuration_skill(self):self.assertIn('meta.configure',[s['id'] for s in self.engine.registry.describe()])
    def test_meta_configure_prize_signature(self):
        skill=next(s for s in self.engine.registry.describe() if s['id']=='meta.configure');self.assertEqual(skill['argument_names'],['key','value'])
    def test_owner_configuration_applies_and_survives_restart(self):
        task=self.task(skill='meta.configure',args={'key':'spending_limit','value':'5'})
        result=self.engine.execute(task['id'],self.adapter());self.assertEqual(result['state'],'completed');self.assertEqual(self.engine.policy.per_transaction,5)
        self.restart();self.assertEqual(self.engine.policy.per_transaction,5);self.assertEqual(self.engine.policy.version,2)
    def test_pending_old_policy_tasks_canceled(self):
        old=self.task();config=self.task(skill='meta.configure',args={'key':'spending_limit','value':'5'})
        self.engine.execute(config['id'],self.adapter());self.assertEqual(self.engine.get(old['id'])['state'],'canceled');self.assertEqual(self.engine.usage(),0)
    def test_unsigned_peer_cannot_configure(self):
        with self.assertRaises(Rejected):self.engine.submit(self.request(skill='meta.configure',args={'key':'spending_limit','value':'500'},key=self.other_key),self.peer)
    def test_delegate_cannot_configure_even_with_generic_goal_grant(self):
        body={'domain':GRANT_DOMAIN,'agent_id':'fixture-agent','grant_id':'g','delegate_key_id':key_id(self.peer),'goal':'fixture','allowed_skills':['meta.configure'],'maximum_spend':'0','max_steps':1,'expires_at':self.clock.value+500,'policy_version':1}
        self.engine.register_grant(sign_envelope(body,self.key,self.crypto))
        body={'domain':DELEGATED_DOMAIN,'agent_id':'fixture-agent','grant_id':'g','request_id':'r','skill':'meta.configure','arguments':{'key':'spending_limit','value':'500'},'expires_at':self.clock.value+300}
        with self.assertRaises(Rejected):self.engine.submit(sign_envelope(body,self.other_key,self.crypto),self.peer)
    def test_unknown_configuration_keys_fail_before_effect(self):
        task=self.task(skill='meta.configure',args={'key':'api_key','value':'never accepted'})
        self.assertEqual(self.engine.execute(task['id'],self.adapter())['state'],'failed')
    def test_invalid_limit_relationship_rejected(self):
        task=self.task(skill='meta.configure',args={'key':'spending_limit','value':'10001'})
        self.assertEqual(self.engine.execute(task['id'],self.adapter())['state'],'failed')
    def test_meta_skills_returns_real_registry(self):
        task=self.task(skill='meta.skills',args={});result=self.engine.execute(task['id'],self.adapter())
        self.assertEqual(result['state'],'completed');self.assertEqual(len(result['result']['skills']),22)
        self.assertIn('agent.ping',{item['id'] for item in result['result']['skills']})
    def test_config_cannot_modify_an_in_flight_effect(self):
        old=self.task();self.engine.start(old['id'],'worker');config=self.task(skill='meta.configure',args={'key':'spending_limit','value':'5'})
        result=self.engine.execute(config['id'],self.adapter());self.assertNotEqual(result['state'],'completed');self.assertEqual(self.engine.policy.version,1)
if __name__=='__main__':unittest.main()
