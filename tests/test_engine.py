from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from commons_relay.engine import Engine,Policy,Prepared,Receipt,REQUEST_DOMAIN
from commons_relay.codec import Rejected
from commons_relay.signing import Ed25519,key_id,sign_envelope

class Clock:
    def __init__(self):self.value=10000
    def __call__(self):return self.value
class FixtureAdapter:
    """Local fixture only. No blockchain, network or model."""
    def __init__(self,engine=None,mode='ok'):
        self.calls=0;self.mode=mode;self.engine=engine;self.task=None
    def prepare(self,task):
        self.task=task
        if self.mode=='prepare-error':raise RuntimeError('synthetic/private-context')
        return Prepared('fixture-'+task['id'],int(task['maximum_spend']),task['id'])
    def broadcast(self,effect):
        self.calls+=1
        if self.engine:assert self.engine.get(self.task['id'])['phase']=='broadcasting'
        if self.mode=='lost-response':raise TimeoutError('synthetic timeout after effect')
    def lookup(self,effect):
        if self.mode=='pending':return Receipt(effect.reference,'pending')
        if self.mode=='rejected':return Receipt(effect.reference,'rejected')
        return Receipt(effect.reference,'confirmed',effect.maximum_spend,{'fixture':True})

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.crypto=Ed25519(self.root/'crypto')
        self.key=self.crypto.scratch/'owner.pem';self.owner=self.crypto.generate(self.key)
        self.other_key=self.crypto.scratch/'peer.pem';self.peer=self.crypto.generate(self.other_key)
        self.clock=Clock();self.engine=Engine(self.root/'agent','fixture-agent',self.owner,self.crypto,clock=self.clock)
        self.counter=0
    def tearDown(self):self.engine.close();self.temp.cleanup()
    def request(self,amount='50',request_id=None,skill='wallet.send',args=None,key=None,expires=None):
        self.counter+=1
        body={'domain':REQUEST_DOMAIN,'agent_id':'fixture-agent','request_id':request_id or str(self.counter),
              'skill':skill,'arguments':args if args is not None else {'recipient':'testnet-recipient','amount':amount},
              'expires_at':expires if expires is not None else self.clock.value+500}
        return sign_envelope(body,key or self.key,self.crypto)
    def task(self,*args,**kwargs):return self.engine.submit(self.request(*args,**kwargs))
    def approve(self,task,nonce='approval-1',expires=None,key=None):
        body=self.engine.approval_body(task['id'],nonce,expires or self.clock.value+100)
        return self.engine.approve(sign_envelope(body,key or self.key,self.crypto))
    def restart(self):
        self.engine.close();self.engine=Engine(self.root/'agent','fixture-agent',self.owner,self.crypto,clock=self.clock)
    def test_below_limit_reserved(self):t=self.task('50');self.assertEqual(t['state'],'submitted');self.assertEqual(self.engine.usage(),50)
    def test_above_per_tx_waits_owner(self):t=self.task('101');self.assertEqual(t['state'],'input-required');self.assertEqual(self.engine.usage(),0)

    def test_agent_task_below_threshold_is_autonomous(self):
        args={'agent_address':'peer-agent','skill':'meta.skills','params':{}}
        from commons_relay.skills import Quote
        self.engine.quote_provider=lambda name,args,base: Quote('LEZ-testnet',50,True) if name=='agent.task' else base
        t=self.task(skill='agent.task',args=args)
        self.assertEqual(t['state'],'submitted');self.assertEqual(self.engine.usage(),50)
    def test_agent_task_above_threshold_requires_owner(self):
        args={'agent_address':'peer-agent','skill':'meta.skills','params':{}}
        from commons_relay.skills import Quote
        self.engine.quote_provider=lambda name,args,base: Quote('LEZ-testnet',101,True) if name=='agent.task' else base
        t=self.task(skill='agent.task',args=args)
        self.assertEqual(t['state'],'input-required');self.assertEqual(self.engine.usage(),0)
    def test_period_reservation_counts(self):
        for _ in range(3):self.task('100')
        t=self.task('1');self.assertEqual(t['state'],'input-required');self.assertEqual(self.engine.usage(),300)
    def test_owner_approval_bound_to_exact_task(self):
        t=self.task('101');approved=self.approve(t);self.assertEqual(approved['state'],'submitted');self.assertEqual(self.engine.usage(),101)
    def test_wrong_signer_cannot_approve(self):
        t=self.task('101')
        with self.assertRaises(Rejected):self.approve(t,key=self.other_key)
        self.assertEqual(self.engine.get(t['id'])['state'],'input-required')
    def test_mutated_approval_intent_rejected(self):
        t=self.task('101');body=self.engine.approval_body(t['id'],'nonce',10100);body['intent_hash']='0'*64
        with self.assertRaises(Rejected):self.engine.approve(sign_envelope(body,self.key,self.crypto))
    def test_expired_approval_rejected(self):
        t=self.task('101')
        with self.assertRaises(Rejected):self.approve(t,expires=self.clock.value)
    def test_approval_cannot_extend_request_lifetime(self):
        t=self.task('101')
        with self.assertRaises(Rejected):self.approve(t,expires=self.clock.value+501)
    def test_approval_replay_is_idempotent(self):
        t=self.task('101');self.approve(t);self.approve(t);self.assertEqual(self.engine.usage(),101)
    def test_approval_nonce_cannot_approve_another_task(self):
        a=self.task('101');b=self.task('102');self.approve(a)
        with self.assertRaises(Rejected):self.approve(b)
    def test_request_replay_is_idempotent(self):
        request=self.request();a=self.engine.submit(request);b=self.engine.submit(request)
        self.assertEqual(a['id'],b['id']);self.assertEqual(self.engine.usage(),50)
    def test_request_id_cannot_change_amount(self):
        self.task('50',request_id='same')
        with self.assertRaises(Rejected):self.task('51',request_id='same')
    def test_wrong_agent_cannot_replay_request(self):
        env=self.request();env['body']['agent_id']='other-agent';env=sign_envelope(env['body'],self.key,self.crypto)
        with self.assertRaises(Rejected):self.engine.submit(env)
    def test_peer_cannot_spend_agent_wallet(self):
        with self.assertRaises(Rejected):self.engine.submit(self.request(key=self.other_key),self.peer)
    def test_peer_can_only_read_public_skill(self):
        req=self.request(skill='meta.skills',args={},key=self.other_key)
        self.assertEqual(self.engine.submit(req,self.peer)['state'],'submitted')
    def test_agent_task_prize_signature(self):
        skill=next(s for s in self.engine.registry.describe() if s['id']=='agent.task')
        self.assertEqual(skill['argument_names'],['agent_address','skill','params'])
    def test_agent_subscribe_prize_signature(self):
        skill=next(s for s in self.engine.registry.describe() if s['id']=='agent.subscribe')
        self.assertEqual(skill['argument_names'],['agent_address','task_id'])
    def test_program_default_skill_signatures(self):
        skills={s['id']:s for s in self.engine.registry.describe()}
        self.assertEqual(skills['program.call']['argument_names'],['program_id','instruction','params'])
        self.assertEqual(skills['program.deploy']['argument_names'],['binary_path'])
    def test_unknown_skill_has_no_dispatch(self):
        with self.assertRaises(Rejected):self.task(skill='shell.exec',args={'command':'anything'})
    def test_unknown_extra_argument_rejected(self):
        with self.assertRaises(Rejected):self.task(args={'recipient':'a','amount':'1','bypass':True})
    def test_hard_limit_never_becomes_pending_approval(self):
        with self.assertRaises(Rejected):self.task('10001')
    def test_program_calls_always_require_specific_approval(self):
        t=self.task(skill='program.call',args={'program_id':'00'*32,'instruction':'00000000','params':{'accounts':[{'account_id':'00'*32,'signer':True}]}});self.assertEqual(t['state'],'input-required')
    def test_restart_keeps_reservation(self):
        task=self.task('70');self.restart();self.assertEqual(self.engine.usage(),70);self.assertEqual(self.engine.get(task['id'])['state'],'submitted')
    def test_restart_cannot_replace_policy(self):
        with self.assertRaises(Rejected):Engine(self.root/'agent','fixture-agent',self.owner,self.crypto,policy=Policy(per_transaction=99),clock=self.clock)
    def test_complete_settles_once(self):
        t=self.task('60');adapter=FixtureAdapter(self.engine);result=self.engine.execute(t['id'],adapter)
        self.assertEqual(result['state'],'completed');self.assertEqual(adapter.calls,1);self.assertEqual(self.engine.usage(),60)
        self.engine.settle(t['id'],Receipt(result['effect_reference'],'confirmed',60));self.assertEqual(self.engine.usage(),60)
    def test_preparation_failure_releases_reservation(self):
        t=self.task('30');adapter=FixtureAdapter(mode='prepare-error');r=self.engine.execute(t['id'],adapter)
        self.assertEqual(r['state'],'failed');self.assertEqual(self.engine.usage(),0);self.assertEqual(adapter.calls,0);self.assertNotIn('synthetic/private',str(r))
    def test_response_loss_keeps_reservation_and_no_retry(self):
        t=self.task('50');a=FixtureAdapter(mode='lost-response');r=self.engine.execute(t['id'],a)
        self.assertEqual(r['state'],'unknown');self.assertEqual(self.engine.usage(),50)
        with self.assertRaises(Rejected):self.engine.execute(t['id'],a)
        self.assertEqual(a.calls,1)
    def test_reconcile_after_restart_does_not_broadcast_again(self):
        t=self.task('70');a=FixtureAdapter(mode='lost-response');self.engine.execute(t['id'],a);self.restart()
        a.mode='ok';self.assertEqual(self.engine.reconcile(t['id'],a)['state'],'completed');self.assertEqual(a.calls,1)
    def test_pending_receipt_stays_unknown(self):
        t=self.task('70');r=self.engine.execute(t['id'],FixtureAdapter(mode='pending'));self.assertEqual(r['state'],'unknown');self.assertEqual(self.engine.usage(),70)
    def test_network_rejection_releases(self):
        t=self.task('70');r=self.engine.execute(t['id'],FixtureAdapter(mode='rejected'));self.assertEqual(r['state'],'failed');self.assertEqual(self.engine.usage(),0)
    def test_cannot_cancel_potentially_broadcast_effect(self):
        t=self.task('70');self.engine.execute(t['id'],FixtureAdapter(mode='pending'))
        with self.assertRaises(Rejected):self.engine.cancel(t['id'],self.engine.owner)
        self.assertEqual(self.engine.usage(),70)
    def test_cancel_before_dispatch_releases(self):
        t=self.task('70');r=self.engine.cancel(t['id'],self.engine.owner);self.assertEqual(r['state'],'canceled');self.assertEqual(self.engine.usage(),0)
    def test_expiry_never_turns_into_approval(self):
        t=self.task('101');self.clock.value+=700;self.assertEqual(self.engine.get(t['id'])['state'],'rejected');self.assertEqual(self.engine.notices(),[])
    def test_unanswered_notice_retries_then_expires(self):
        t=self.task('101');first=self.engine.notices();self.assertEqual(len(first),1);self.assertEqual(self.engine.notices(),[])
        self.clock.value+=3;second=self.engine.notices();self.assertEqual(second[0]['id'],first[0]['id']);self.assertEqual(second[0]['attempt'],2)
        self.clock.value+=600;self.assertEqual(self.engine.notices(),[]);self.assertEqual(self.engine.get(t['id'])['state'],'rejected')
    def test_past_budget_window_does_not_release_unknown(self):
        t=self.task('80');self.engine.execute(t['id'],FixtureAdapter(mode='pending'));self.clock.value+=90000;self.assertEqual(self.engine.usage(),80)
    def test_rolling_window_settled_spending_expires(self):
        t=self.task('80');self.engine.execute(t['id'],FixtureAdapter());self.clock.value+=86401;self.assertEqual(self.engine.usage(),0)
    def test_clock_rollback_cannot_reset_budget(self):
        t=self.task('80');self.engine.execute(t['id'],FixtureAdapter());self.clock.value-=8000;self.assertEqual(self.engine.usage(),80)
    def test_wrong_lease_cannot_change_task(self):
        t=self.task();self.engine.start(t['id'],'worker')
        with self.assertRaises(Rejected):self.engine.record_prepared(t['id'],'wrong',Prepared('x',1,'x'))
    def test_prepared_spend_cannot_exceed_bound(self):
        t=self.task();_,lease=self.engine.start(t['id'],'worker')
        with self.assertRaises(Rejected):self.engine.record_prepared(t['id'],lease,Prepared('x',51,'x'))
    def test_only_prepared_effect_can_broadcast(self):
        t=self.task();_,lease=self.engine.start(t['id'],'worker')
        with self.assertRaises(Rejected):self.engine.mark_broadcasting(t['id'],lease)
    def test_receipt_must_match_effect(self):
        t=self.task();self.engine.execute(t['id'],FixtureAdapter(mode='pending'))
        with self.assertRaises(Rejected):self.engine.settle(t['id'],Receipt('wrong-reference','confirmed',50))
    def test_private_task_cannot_be_read_by_peer(self):
        t=self.task()
        with self.assertRaises(Rejected):self.engine.get(t['id'],key_id(self.peer))
    def test_events_resume_without_duplicates(self):
        t=self.task();self.engine.execute(t['id'],FixtureAdapter());rows=self.engine.events(t['id'],self.engine.owner)
        self.assertTrue(len(rows)>=4);self.assertEqual(self.engine.events(t['id'],self.engine.owner,rows[1]['seq']),rows[2:])
    def test_concurrent_reservations_are_atomic(self):
        other=Engine(self.root/'agent','fixture-agent',self.owner,self.crypto,clock=self.clock);requests=[self.request('100') for _ in range(5)]
        try:
            def go(i):return [self.engine,other][i%2].submit(requests[i])['state']
            with ThreadPoolExecutor(max_workers=5) as pool:states=list(pool.map(go,range(5)))
            self.assertEqual(states.count('submitted'),3);self.assertEqual(states.count('input-required'),2);self.assertEqual(self.engine.usage(),300)
        finally:other.close()

if __name__=='__main__':unittest.main()
