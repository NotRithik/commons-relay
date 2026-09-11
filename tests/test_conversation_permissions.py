"""Real signatures and task ledger, with a non-executing scheduler; no network."""
from dataclasses import asdict, replace
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from commons_relay.codec import Rejected, canonical, b64
from commons_relay.conversation_permissions import compose_decision
from commons_relay.engine import Policy
from commons_relay.service import Service
from commons_relay.signing import Ed25519, sign_envelope

class Scheduler:
    def __init__(self):self.ids=set();self.starts=0;self.fail=False
    def start(self):self.starts+=1
    def schedule(self,task):
        if self.fail:raise RuntimeError('synthetic interruption before schedule')
        self.ids.add(task)
    def stop(self):pass

class ConversationPermissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.signer=Ed25519(root/'owner')
        self.key=self.signer.scratch/'owner.pem';self.public=self.signer.generate(self.key)
        profile=root/'agent';profile.mkdir(mode=0o700)
        config={'schema_version':1,'agent_id':'permission-fixture','owner_public_key':b64(self.public),'network':'testnet','policy':asdict(Policy(per_transaction=5,per_period=30,hard_maximum=50,approval_ttl=7200))}
        (profile/'settings.json').write_bytes(canonical(config));(profile/'settings.json').chmod(0o600)
        self.service=Service(profile);self.addCleanup(lambda: self.service.close())
        self.scheduler=Scheduler();self.service.controller=self.scheduler
        self.planner=self.service.get_planner();self.engine=self.service.engine
        self.now=int(time.time());self.grant={'grant_id':'chat-permission-fixture','allowed_skills':['storage.list'],'expires_at':self.now+600}
        self.planner.db.execute('INSERT INTO conversations(id,grant_hash,prompt,state,created,updated,mode) VALUES (?,?,?,?,?,?,?)',
            (self.grant['grant_id'],'a'*64,'Store the demo file.','thinking',self.now,self.now,'read'))
        self.params={'skill':'storage.upload','arguments':{'path':'demo.txt','label':'demo.txt'},'reason':'I need to upload this file to fulfil your request.'}
    def propose(self):
        result=self.planner.permissions.propose(self.grant,self.params)
        self.planner._set(self.grant['grant_id'],state='waiting')
        return result
    def decision(self,request,decision='approve'):
        return compose_decision({'kind':'planner_permission','goal_id':self.grant['grant_id'],'permission':request,'decision':decision},
            self.engine.agent,self.signer,self.key,int(self.engine.clock()))['params']['envelope']
    def task_count(self):return self.engine.db.execute('SELECT count(*) FROM tasks').fetchone()[0]
    def test_request_is_persisted_without_task_or_execution(self):
        result=self.propose();self.assertTrue(result['permission_required']);self.assertEqual(self.task_count(),0);self.assertFalse(self.scheduler.ids)
        view=self.planner.view(self.grant['grant_id'])['goal']['permission'];self.assertEqual(view['request'],result['request']);self.assertEqual(view['decision'],'pending')
    def test_valid_approval_submits_exactly_one_owner_reviewed_action(self):
        request=self.propose()['request'];envelope=self.decision(request)
        self.planner.permissions.decide(envelope);self.planner.permissions.decide(envelope)
        self.assertEqual(self.task_count(),1);self.assertEqual(len(self.scheduler.ids),1)
        task=self.engine.get(next(iter(self.scheduler.ids)));self.assertEqual(task['skill'],'storage.upload');self.assertEqual(task['arguments'],self.params['arguments']);self.assertEqual(task['maximum_spend'],'0')
    def test_decline_never_creates_a_task(self):
        request=self.propose()['request'];self.planner.permissions.decide(self.decision(request,'decline'))
        self.assertEqual(self.task_count(),0);self.assertFalse(self.scheduler.ids)
        self.assertEqual(self.planner.permissions.view(self.grant['grant_id'])['decision'],'decline')
        with self.assertRaisesRegex(Rejected,'ALREADY_DECIDED'):self.planner.permissions.decide(self.decision(request))
    def test_approval_recovers_crash_between_submission_and_schedule(self):
        request=self.propose()['request'];envelope=self.decision(request);self.scheduler.fail=True
        with self.assertRaisesRegex(RuntimeError,'synthetic interruption'):self.planner.permissions.decide(envelope)
        self.assertEqual(self.task_count(),1);self.scheduler.fail=False
        self.planner.permissions.decide(envelope);self.assertEqual(self.task_count(),1);self.assertEqual(len(self.scheduler.ids),1)
    def test_crash_after_task_creation_before_permission_record_is_idempotent(self):
        request=self.propose()['request'];envelope=self.decision(request)
        self.engine.submit(envelope['body']['task_envelope'],self.public)
        self.assertEqual(self.task_count(),1);self.planner.permissions.decide(envelope)
        self.assertEqual(self.task_count(),1);self.assertEqual(len(self.scheduler.ids),1)
    def test_unsigned_or_tampered_approval_cannot_run(self):
        request=self.propose()['request'];envelope=self.decision(request);envelope['body']['permission_hash']='b'*64
        with self.assertRaises(Rejected):self.planner.permissions.decide(envelope)
        self.assertEqual(self.task_count(),0)
    def test_review_cannot_change_target_inputs(self):
        request=self.propose()['request'];envelope=self.decision(request)
        task=copy.deepcopy(envelope['body']['task_envelope']['body']);task['arguments']['path']='other.txt'
        body=copy.deepcopy(envelope['body']);body['task_envelope']=sign_envelope(task,self.key,self.signer)
        with self.assertRaisesRegex(Rejected,'ACTION_MISMATCH'):self.planner.permissions.decide(sign_envelope(body,self.key,self.signer))
        self.assertEqual(self.task_count(),0)
    def test_policy_change_requires_a_new_review(self):
        request=self.propose()['request'];self.engine.policy=replace(self.engine.policy,version=self.engine.policy.version+1)
        with self.assertRaisesRegex(Rejected,'POLICY_CHANGED'):self.planner.permissions.decide(self.decision(request))
        self.assertEqual(self.task_count(),0)
    def test_expired_permission_cannot_be_approved(self):
        request=self.propose()['request'];envelope=self.decision(request)
        self.engine.clock=lambda: self.now+3600
        with self.assertRaisesRegex(Rejected,'EXPIRED'):self.planner.permissions.decide(envelope)
        self.assertEqual(self.task_count(),0)
    def test_model_cannot_ask_to_configure_policy_or_repeat_allowed_skill(self):
        for name,args in [('meta.configure',{}),('storage.list',{})]:
            with self.assertRaisesRegex(Rejected,'FORBIDDEN'):
                self.planner.permissions.propose(self.grant,{'skill':name,'arguments':args,'reason':'fixture'})
        self.assertEqual(self.task_count(),0)
    def test_one_unchanged_permission_per_model_turn(self):
        first=self.propose();second=self.planner.permissions.propose(self.grant,self.params);self.assertEqual(first,second)
        changed=copy.deepcopy(self.params);changed['arguments']['path']='changed.txt'
        with self.assertRaisesRegex(Rejected,'ONE_PERMISSION'):self.planner.permissions.propose(self.grant,changed)
    def test_cancellation_stops_pending_permission(self):
        request=self.propose()['request'];self.planner._set(self.grant['grant_id'],state='cancelled')
        with self.assertRaisesRegex(Rejected,'NOT_ACTIVE'):self.planner.permissions.decide(self.decision(request))
        self.assertEqual(self.task_count(),0)
    def test_wait_for_model_turn_to_end_before_executing_permission(self):
        request=self.propose()['request'];self.planner.active=self.grant['grant_id']
        class Running:
            def is_alive(self):return True
            def join(self,timeout=None):pass
        self.planner.thread=Running()
        with self.assertRaisesRegex(Rejected,'TURN_STILL_RUNNING'):self.planner.permissions.decide(self.decision(request))
        self.planner.thread=None;self.planner.active=None;self.assertEqual(self.task_count(),0)

    def test_decline_after_crash_cannot_claim_an_accepted_action_never_existed(self):
        request=self.propose()['request'];envelope=self.decision(request)
        self.engine.submit(envelope['body']['task_envelope'],self.public)
        with self.assertRaisesRegex(Rejected,'ALREADY_SUBMITTED'):
            self.planner.permissions.decide(self.decision(request,'decline'))
        self.assertEqual(self.task_count(),1)
        recovered=self.planner.permissions.view(self.grant['grant_id'])
        self.assertEqual(recovered['decision'],'approve')
        self.assertIsNotNone(recovered['task_id'])
    def test_dedicated_review_returns_all_inputs_without_loading_old_chat(self):
        request=self.propose()['request']
        self.planner._set(self.grant['grant_id'],reply='long synthetic reply '*300)
        result=self.planner.permission_review(self.grant['grant_id'])
        self.assertEqual(result['permission']['request'],request)
        self.assertNotIn('prompt',result);self.assertNotIn('reply',result)
        self.assertLess(len(canonical(result)),8000)

    def settle_fixture_task(self, task_id, state='completed', error=None):
        with self.engine.tx() as db:
            db.execute('UPDATE tasks SET state=?,phase=?,result=?,error=?,updated=? WHERE id=?',
                       (state,'settled',canonical({'fixture':True}).decode() if state=='completed' else None,error,self.now+10,task_id))
            db.execute('DELETE FROM reservations WHERE task_id=?',(task_id,))
    def test_completed_permission_action_is_not_displayed_as_waiting(self):
        request=self.propose()['request'];self.planner.permissions.decide(self.decision(request))
        task_id=next(iter(self.scheduler.ids));self.settle_fixture_task(task_id)
        goal=self.planner.view(self.grant['grant_id'])['goal']
        self.assertEqual(goal['state'],'completed');self.assertEqual(goal['permission']['task_state'],'completed')
        self.assertIn('completed',goal['reply']);self.assertEqual(self.task_count(),1)
    def test_failed_permission_action_is_not_displayed_as_success(self):
        request=self.propose()['request'];self.planner.permissions.decide(self.decision(request))
        task_id=next(iter(self.scheduler.ids));self.settle_fixture_task(task_id,'failed','FIXTURE_TRANSPORT_FAILED')
        goal=self.planner.view(self.grant['grant_id'])['goal']
        self.assertEqual(goal['state'],'failed');self.assertEqual(goal['error'],'FIXTURE_TRANSPORT_FAILED')
        self.assertIn('did not complete',goal['reply'])
    def test_completed_action_and_pending_request_survive_service_restart(self):
        request=self.propose()['request'];self.planner.permissions.decide(self.decision(request))
        task_id=next(iter(self.scheduler.ids));self.settle_fixture_task(task_id)
        root=self.service.root;self.service.close();self.service=Service(root)
        self.planner=self.service.get_planner();self.engine=self.service.engine
        goal=self.planner.view(self.grant['grant_id'])['goal']
        self.assertEqual(goal['state'],'completed');self.assertEqual(goal['permission']['task_id'],task_id)
        self.assertEqual(self.task_count(),1);self.assertIsNone(self.planner.thread)
    def test_unapproved_request_survives_restart_without_task_creation(self):
        request=self.propose()['request'];root=self.service.root
        self.service.close();self.service=Service(root);self.planner=self.service.get_planner();self.engine=self.service.engine
        view=self.planner.permission_review(self.grant['grant_id'])
        self.assertEqual(view['permission']['request'],request);self.assertEqual(view['permission']['decision'],'pending')
        self.assertEqual(self.task_count(),0);self.assertIsNone(self.planner.thread)

    def test_changed_quote_during_engine_submission_cannot_create_unreviewed_task(self):
        from commons_relay.skills import Quote
        request=self.propose()['request'];envelope=self.decision(request)
        def race(envelope,public_key=None,**kwargs):
            self.engine.quote_provider=lambda name,args,base:Quote('LEZ-testnet',1,True)
            return original(envelope,public_key,**kwargs)
        original=self.engine.submit
        with patch.object(self.engine,'submit',side_effect=race):
            with self.assertRaisesRegex(Rejected,'REVIEWED_INTENT_CHANGED'):
                self.planner.permissions.decide(envelope)
        self.assertEqual(self.task_count(),0)
        self.assertEqual(self.scheduler.ids,set())

    def test_accepted_task_is_adopted_after_price_and_policy_change(self):
        from dataclasses import replace
        request=self.propose()['request'];envelope=self.decision(request)
        task=self.engine.submit(envelope['body']['task_envelope'],self.public)
        self.engine.policy=replace(self.engine.policy,version=self.engine.policy.version+1)
        self.engine.quote_provider=lambda *args: (_ for _ in ()).throw(AssertionError('Must not re-quote an accepted task'))
        with patch.object(self.engine,'submit',side_effect=AssertionError('Must not submit twice')):
            goal=self.planner.permissions.decide(envelope)['goal']
        self.assertEqual(goal['permission']['task_id'],task['id']);self.assertEqual(goal['task_ids'],[task['id']])
        self.assertEqual(self.task_count(),1);self.assertEqual(self.scheduler.ids,{task['id']})
    def test_expired_accepted_task_is_linked_without_execution(self):
        request=self.propose()['request'];envelope=self.decision(request)
        task=self.engine.submit(envelope['body']['task_envelope'],self.public)
        self.engine.clock=lambda:self.now+1900
        with patch.object(self.engine,'submit',side_effect=AssertionError('Expired task must not be recreated')):
            goal=self.planner.permissions.decide(envelope)['goal']
        self.assertEqual(goal['permission']['task_id'],task['id']);self.assertEqual(goal['task_ids'],[task['id']])
        self.assertIn(goal['state'],['failed','cancelled']);self.assertEqual(self.scheduler.ids,set());self.assertEqual(self.task_count(),1)
    def test_approved_row_repairs_missing_conversation_bookkeeping(self):
        request=self.propose()['request'];envelope=self.decision(request)
        task=self.engine.submit(envelope['body']['task_envelope'],self.public)
        self.planner.db.execute("UPDATE conversation_permissions SET decision='approve',task_id=? WHERE goal_id=?",(task['id'],self.grant['grant_id']))
        with patch.object(self.engine,'submit',side_effect=AssertionError('Already accepted')):
            goal=self.planner.permissions.decide(envelope)['goal']
        self.assertEqual(goal['task_ids'],[task['id']]);self.assertIn('approved',goal['reply']);self.assertEqual(self.task_count(),1)
    def test_read_only_reconciliation_repairs_links_without_scheduling(self):
        request=self.propose()['request'];envelope=self.decision(request)
        task=self.engine.submit(envelope['body']['task_envelope'],self.public)
        with patch.object(self.engine,'submit',side_effect=AssertionError('No submit on reads')):
            goal=self.planner.view(self.grant['grant_id'])['goal']
        self.assertEqual(goal['task_ids'],[task['id']]);self.assertEqual(goal['permission']['decision'],'approve')
        self.assertEqual(self.scheduler.ids,set())
    def test_accepted_task_with_wrong_saved_intent_cannot_be_adopted(self):
        request=self.propose()['request'];envelope=self.decision(request)
        task=self.engine.submit(envelope['body']['task_envelope'],self.public)
        with self.engine.tx() as db:db.execute("UPDATE tasks SET amount='1' WHERE id=?",(task['id'],))
        with self.assertRaisesRegex(Rejected,'TASK_BINDING_MISMATCH'):self.planner.permissions.decide(envelope)
        self.assertEqual(self.scheduler.ids,set())
    def test_signed_attempt_deadline_is_checked_during_recovery(self):
        request=self.propose()['request'];envelope=self.decision(request)
        self.planner.db.execute('UPDATE conversation_permissions SET attempt=? WHERE goal_id=?',(canonical(envelope).decode(),self.grant['grant_id']))
        task=self.engine.submit(envelope['body']['task_envelope'],self.public)
        with self.engine.tx() as db:db.execute('UPDATE tasks SET deadline=deadline+1 WHERE id=?',(task['id'],))
        with self.assertRaisesRegex(Rejected,'TASK_BINDING_MISMATCH'):self.planner.permissions.decide(envelope)
        self.assertEqual(self.scheduler.ids,set())

    def waiting_task_receipt(self,state='completed'):
        self.planner._set(self.grant['grant_id'],state='waiting',task_ids=json.dumps(['fixture-linked-task']),reply='Still waiting.')
        return {'id':'fixture-linked-task','state':state,'updated':self.now+50,'error':'FIXTURE_FAILED' if state=='failed' else None}
    def test_regular_delegation_completion_updates_waiting_chat_without_model_call(self):
        task=self.waiting_task_receipt()
        with patch.object(self.engine,'get',return_value=task):goal=self.planner.view(self.grant['grant_id'])['goal']
        self.assertEqual(goal['state'],'completed');self.assertTrue(goal['outcome_from_receipts'])
        self.assertIsNone(self.planner.thread)
    def test_failed_delegation_updates_chat_from_task_not_model_claim(self):
        task=self.waiting_task_receipt('failed')
        with patch.object(self.engine,'get',return_value=task):goal=self.planner.view(self.grant['grant_id'])['goal']
        self.assertEqual(goal['state'],'failed');self.assertEqual(goal['error'],'FIXTURE_FAILED')
    def test_unresolved_paid_task_is_not_mislabeled_completed(self):
        task=self.waiting_task_receipt('unknown')
        with patch.object(self.engine,'get',return_value=task):goal=self.planner.view(self.grant['grant_id'])['goal']
        self.assertEqual(goal['state'],'waiting');self.assertEqual(goal['reply'],'Still waiting.')
    def test_pending_permission_still_waits_when_earlier_read_task_finished(self):
        self.propose();task=self.waiting_task_receipt()
        with patch.object(self.engine,'get',return_value=task):goal=self.planner.view(self.grant['grant_id'])['goal']
        self.assertEqual(goal['state'],'waiting');self.assertEqual(goal['permission']['decision'],'pending')

    def test_explicit_agent_resume_schedules_only_existing_valid_accepted_task(self):
        request=self.propose()['request'];envelope=self.decision(request)
        task=self.engine.submit(envelope['body']['task_envelope'],self.public)
        with patch.object(self.engine,'submit',side_effect=AssertionError('No second task on resume')):
            resumed=self.planner.permissions.resume_accepted(self.scheduler)
            self.planner.permissions.resume_accepted(self.scheduler)
        self.assertEqual(resumed,[task['id']]);self.assertEqual(self.scheduler.ids,{task['id']})
        self.assertEqual(self.task_count(),1)
    def test_explicit_agent_resume_never_executes_expired_or_unapproved_request(self):
        self.propose();self.assertEqual(self.planner.permissions.resume_accepted(self.scheduler),[])
        self.assertEqual(self.task_count(),0)
        request=self.planner.permissions.view(self.grant['grant_id'])['request'];envelope=self.decision(request)
        task=self.engine.submit(envelope['body']['task_envelope'],self.public)
        self.engine.clock=lambda:self.now+1900
        self.assertEqual(self.planner.permissions.resume_accepted(self.scheduler),[])
        self.assertEqual(self.scheduler.ids,set());self.assertEqual(self.task_count(),1)

    def test_long_chat_keeps_full_reply_without_overflowing_owner_channel(self):
        self.params['skill']='messaging.send'
        self.params['arguments']={'recipient':'fixture-peer','message':'x'*3600}
        request=self.propose()['request']
        self.planner.db.execute('UPDATE conversations SET prompt=?,reply=? WHERE id=?',('p'*4000,'r'*5800,self.grant['grant_id']))
        result=self.planner.view(self.grant['grant_id'])
        self.assertEqual(result['goal']['prompt'],'p'*4000);self.assertEqual(result['goal']['reply'],'r'*5800)
        self.assertTrue(result['goal']['permission_preview']);self.assertLess(len(canonical(result)),13500)
        self.assertEqual(self.planner.permission_review(self.grant['grant_id'])['permission']['request'],request)

    def test_paid_action_has_separate_review_and_execution_windows(self):
        self.params={'skill':'wallet.send','arguments':{'recipient':'fixture-peer','amount':'3'},'reason':'Pay the requested service.'}
        request=self.propose()['request']
        self.assertGreater(request['execution_expires_at'],self.now+3600)
        envelope=self.decision(request)
        self.assertLessEqual(envelope['body']['expires_at'],self.now+600)
        self.assertEqual(envelope['body']['task_envelope']['body']['expires_at'],request['execution_expires_at'])
        self.planner.permissions.decide(envelope)
        goal=self.planner.view(self.grant['grant_id'])['goal']
        task=self.engine.get(goal['permission']['task_id'])
        self.assertEqual(task['deadline'],request['execution_expires_at'])
        self.engine.clock=lambda:self.now+1200
        self.assertEqual(self.planner.permissions.reconcile_existing(self.grant['grant_id'])['id'],task['id'])

    def test_paid_execution_window_cannot_be_changed_after_review(self):
        self.params={'skill':'wallet.send','arguments':{'recipient':'fixture-peer','amount':'3'},'reason':'Pay the requested service.'}
        request=self.propose()['request'];request['execution_expires_at']+=1
        with self.assertRaises(Rejected):self.decision(request)
        self.assertEqual(self.task_count(),0)
