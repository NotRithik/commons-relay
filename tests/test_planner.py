"""No model API calls: real signatures and a clearly labelled local runner fixture."""
from dataclasses import asdict
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from commons_relay.codec import canonical, b64, Rejected
from commons_relay.engine import Policy, GRANT_DOMAIN, DELEGATED_DOMAIN, REQUEST_DOMAIN
from commons_relay.planner import Planner, Configuration, READ_SKILLS
from commons_relay.service import Service
from commons_relay.signing import Ed25519, sign_envelope

class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.owner=Ed25519(self.root/'owner')
        self.key=self.owner.scratch/'owner-signing.pem';self.public=self.owner.generate(self.key)
        self.profile=self.root/'agent';self.profile.mkdir(mode=0o700)
        settings={'schema_version':1,'agent_id':'planner-fixture','owner_public_key':b64(self.public),
                  'policy':asdict(Policy(per_transaction=5,per_period=30,hard_maximum=50,approval_ttl=7200)),'network':'testnet'}
        self.write_private(self.profile/'settings.json',settings)
        self.service=Service(self.profile);self.planner=self.service.get_planner()
        self.key_file=self.root/'api.env';self.key_file.write_text('API_KEY=sk-fixture-not-a-real-credential\n');self.key_file.chmod(0o600)
        self.budget_file=self.root/'budget.json'
        self.write_private(self.budget_file,{'version':1,'maximum_micro_usd':1000000,'settled_micro_usd':0,'reservations':{},'requests':[]})
        self.runner=self.root/'labelled-fixture.py'
        self.runner.write_text('''import json,sys,pathlib
value=json.loads(sys.stdin.readline())
path=pathlib.Path('fixture-starts.txt')
with path.open('a') as out:out.write('explicit fixture invocation\\n')
pathlib.Path('fixture-input.json').write_text(json.dumps(value))
print(json.dumps({'kind':'done','text':'Labelled test fixture, not a language-model answer.','waiting':False,'error':None}),flush=True)
''')
        self.config={'schema_version':1,'node':str(Path(sys.executable).resolve()),'runner':str(self.runner),
            'runner_sha256':hashlib.sha256(self.runner.read_bytes()).hexdigest(),'model':'gpt-fixture',
            'api_key_file':str(self.key_file),'budget_file':str(self.budget_file),'budget_micro_usd':1000000,
            'input_micro_per_million':250000,'output_micro_per_million':1200000}
    def tearDown(self):
        self.service.close();self.temp.cleanup()
    def write_private(self,path,value):
        path.write_bytes(canonical(value));path.chmod(0o600)
    def enable(self):self.write_private(self.profile/'planner.json',self.config)
    def grant(self,**changes):
        try:
            config = self.planner.configuration()
            reviewed = config.configuration_hash if config else '0' * 64
        except Rejected:
            reviewed = '0' * 64  # negative configuration cases are rejected by start(), not the fixture
        body={'domain':GRANT_DOMAIN,'agent_id':'planner-fixture','grant_id':'chat-fixture',
            'delegate_key_id':self.planner.delegate_id,'goal':'List my saved files.',
            'allowed_skills':['storage.list'],'maximum_spend':'0','max_steps':4,
            'expires_at':int(time.time())+600,'policy_version':1, 'inference_hash':reviewed}
        body.update(changes);return sign_envelope(body,self.key,self.owner)
    def wait(self):
        self.planner.thread.join(timeout=5)
        self.assertFalse(self.planner.thread.is_alive())
    def completed_goal(self,**changes):
        self.enable();envelope=self.grant(**changes);self.planner.start(envelope);self.wait();return envelope
    def delegated(self,grant,skill='storage.list',arguments=None,request_id='request-one'):
        body={'domain':DELEGATED_DOMAIN,'agent_id':'planner-fixture','grant_id':grant['body']['grant_id'],
              'request_id':request_id,'skill':skill,'arguments':arguments or {},'expires_at':int(time.time())+300}
        return {'method':'submit','params':{'envelope':sign_envelope(body,self.planner.key_path,self.planner.crypto),
                'public_key':b64(self.planner.public)}}
    def test_disabled_status_starts_no_model(self):
        status=self.planner.status()
        self.assertFalse(status['enabled']);self.assertIsNone(self.planner.thread)
        self.assertNotEqual(self.planner.public,self.public)
        self.assertFalse((self.profile/'owner-signing.pem').exists())
    def test_unconfigured_start_is_an_explicit_error(self):
        with self.assertRaisesRegex(Rejected,'PLANNER_NOT_CONFIGURED'):self.planner.start(self.grant())
    def test_real_fixture_process_never_receives_owner_key_or_api_secret_in_input(self):
        envelope=self.completed_goal()
        result=self.planner.view(envelope['body']['grant_id'])
        self.assertEqual(result['goal']['state'],'completed')
        self.assertIn('Labelled test fixture',result['goal']['reply'])
        raw=(self.planner.root/'fixture-input.json').read_text()
        self.assertNotIn('owner-signing.pem',raw)
        self.assertNotIn('sk-fixture-not-a-real-credential',raw)
        self.assertIn('delegate.pem',raw)
    def test_duplicate_start_returns_receipt_without_repeating_runner_or_charge(self):
        envelope=self.completed_goal()
        first=self.planner.view('chat-fixture')
        second=self.planner.start(envelope)
        self.assertEqual(first,second)
        self.assertEqual(len((self.planner.root/'fixture-starts.txt').read_text().splitlines()),1)
    def test_same_goal_identifier_cannot_change_intent(self):
        self.completed_goal()
        with self.assertRaisesRegex(Rejected,'GRANT_ID_REUSED'):self.planner.start(self.grant(goal='A different goal'))
    def test_unsigned_or_changed_grant_cannot_start_runner(self):
        self.enable();bad=self.grant();bad['body']['goal']='Changed after signing'
        with self.assertRaises(Rejected):self.planner.start(bad)
        self.assertIsNone(self.planner.thread)
    def test_delegate_must_match_this_agent(self):
        with self.assertRaisesRegex(Rejected,'PLANNER_DELEGATE_CHANGED'):
            self.planner.start(self.grant(delegate_key_id='ed25519:'+'0'*64))
    def test_read_only_grant_cannot_reserve_money(self):
        with self.assertRaisesRegex(Rejected,'READ_ONLY_GRANT_MUST_NOT_SPEND'):
            self.planner.start(self.grant(maximum_spend='1'))
    def test_planner_never_gets_policy_configuration_skill(self):
        with self.assertRaisesRegex(Rejected,'PLANNER_CANNOT_CONFIGURE_OWNER_POLICY'):
            self.planner.start(self.grant(allowed_skills=['meta.configure']))
    def test_expiry_step_and_amount_limits_are_checked_before_process_start(self):
        for changed in [{'expires_at':int(time.time())-1},{'max_steps':9},
                        {'maximum_spend':'51','allowed_skills':['wallet.send']}]:
            with self.assertRaises(Rejected):self.planner.start(self.grant(**changed))
        self.assertIsNone(self.planner.thread)
    def test_runner_file_hash_is_checked(self):
        self.enable();self.runner.write_text('raise SystemExit(0)')
        with self.assertRaisesRegex(Rejected,'PLANNER_RUNNER_CHANGED'):self.planner.start(self.grant())
    def test_secret_permissions_are_checked_without_disclosing_secret(self):
        self.enable();self.key_file.chmod(0o644)
        status=self.planner.status();self.assertFalse(status['enabled'])
        self.assertEqual(status['error'],'PLANNER_SECRET_PERMISSIONS')
        self.assertNotIn('sk-fixture',json.dumps(status))
    def test_symlinked_runner_is_rejected(self):
        alias=self.root/'alias.py';alias.symlink_to(self.runner);self.config['runner']=str(alias);self.enable()
        with self.assertRaisesRegex(Rejected,'INVALID_PLANNER_PATH'):self.planner.start(self.grant())
    def test_exhausted_shared_budget_refuses_a_new_goal(self):
        self.enable();self.write_private(self.budget_file,{'version':1,'maximum_micro_usd':1000000,
            'settled_micro_usd':1000000,'reservations':{},'requests':[]})
        with self.assertRaisesRegex(Rejected,'TEST_BUDGET_EXHAUSTED'):self.planner.start(self.grant())
    def test_delegate_submission_uses_the_actual_permission_engine(self):
        grant=self.completed_goal();result=self.planner.request(grant['body'],self.delegated(grant))
        self.assertEqual(result['skill'],'storage.list');self.assertEqual(result['state'],'submitted')
        self.assertEqual(self.planner.view('chat-fixture')['goal']['task_ids'],[result['id']])
        with self.service.engine.tx() as db:
            self.assertEqual(db.execute('SELECT grant_id FROM grant_tasks WHERE task_id=?',(result['id'],)).fetchone()[0],'chat-fixture')
    def test_delegate_cannot_use_a_skill_outside_the_signed_scope(self):
        grant=self.completed_goal()
        with self.assertRaisesRegex(Rejected,'GOAL_SCOPE_VIOLATION'):
            self.planner.request(grant['body'],self.delegated(grant,'wallet.send',{'recipient':'someone','amount':'1'}))
    def test_runner_cannot_call_shell_or_approve(self):
        grant=self.completed_goal()
        for method in ['shell','exec','approve','grant','owner.send']:
            with self.assertRaisesRegex(Rejected,'PLANNER_METHOD_NOT_ALLOWED'):
                self.planner.request(grant['body'],{'method':method,'params':{}})
    def test_runner_cannot_read_or_run_a_task_from_another_goal(self):
        grant=self.completed_goal()
        request={'domain':REQUEST_DOMAIN,'agent_id':self.service.engine.agent,'request_id':'owner-task',
                 'skill':'storage.list','arguments':{},'expires_at':int(time.time())+300}
        task=self.service.engine.submit(sign_envelope(request,self.key,self.owner),self.public)
        for method in ['run','task']:
            with self.assertRaisesRegex(Rejected,'PLANNER_TASK_OUTSIDE_GRANT'):
                self.planner.request(grant['body'],{'method':method,'params':{'task_id':task['id']}})
    def test_signed_cancel_revokes_future_delegate_actions(self):
        grant=self.completed_goal()
        body={'domain':'commons/commons_relay/revoke/v1','agent_id':self.service.engine.agent,
              'grant_id':'chat-fixture','expires_at':int(time.time())+300}
        self.planner.cancel('chat-fixture',sign_envelope(body,self.key,self.owner))
        self.assertEqual(self.planner.view('chat-fixture')['goal']['state'],'cancelled')
        with self.assertRaisesRegex(Rejected,'GOAL_GRANT_NOT_ACTIVE'):
            self.planner.request(grant['body'],self.delegated(grant))
    def test_restart_marks_interrupted_turn_without_repeating_model(self):
        self.completed_goal();self.planner._set('chat-fixture',state='thinking')
        self.planner.close();self.service.planner=None
        self.planner=self.service.get_planner()
        self.assertEqual(self.planner.view('chat-fixture')['goal']['state'],'interrupted')
        self.assertIsNone(self.planner.thread)
        self.assertEqual(len((self.planner.root/'fixture-starts.txt').read_text().splitlines()),1)
    def test_history_is_bounded_and_truthfully_marks_previews(self):
        self.completed_goal();self.planner._set('chat-fixture',reply='x'*5000)
        result=self.planner.history();self.assertTrue(result['goals'][0]['preview'])
        self.assertLess(len(canonical(result)),12000)
        self.assertEqual(len(self.planner.view('chat-fixture')['goal']['reply']),5000)
    def test_byte_bound_refuses_large_escaped_prompt(self):
        with self.assertRaisesRegex(Rejected,'INVALID_PLANNER_PROMPT'):
            self.planner.start(self.grant(goal='\u2603'*2000))
    def test_status_never_contains_operator_file_paths(self):
        self.enable();text=json.dumps(self.planner.status())
        self.assertNotIn(str(self.root),text);self.assertNotIn('api_key_file',text)
        self.assertIn('remaining_micro_usd',text)

if __name__=='__main__':unittest.main()
