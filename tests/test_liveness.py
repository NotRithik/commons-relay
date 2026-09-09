"""Offline liveness, queue-isolation and paid-input regressions; no network/proofs."""
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import test_controller as controls
import test_engine as engines
import test_planner as planners
from commons_relay.codec import Rejected, canonical
from commons_relay.liveness import observe, pong, is_status_question
from commons_relay.service import Service
from commons_relay.skills import default_registry, Quote
from commons_relay.a2a_types import PAYMENT_EXTENSION


class LivenessTests(unittest.TestCase):
    def setUp(self):
        self.f = controls.ControllerTests(); self.f.setUp(); self.f.init()
        self.addCleanup(self.f.tearDown)
        self.f.service.controller = self.f.controller
        self.f.service.get_wallet = Mock(side_effect=AssertionError('health must not touch wallet'))

    def test_challenge_echo_is_small_and_creates_no_task(self):
        result = pong(self.f.service, 'a' * 32)
        self.assertEqual(result['nonce'], 'a' * 32)
        self.assertTrue(result['owner_ping']); self.assertFalse(result['model_called'])
        self.assertFalse(result['wallet_queried'])
        self.assertLess(len(canonical(result)), 7000)
        self.assertEqual(self.f.engine.db.execute('SELECT count(*) FROM tasks').fetchone()[0], 0)

    def test_invalid_challenge_rejected(self):
        for value in [None, True, '', 'a' * 31, 'G' * 32, {'shell': 'x'}]:
            with self.assertRaises(Rejected): pong(self.f.service, value)

    def test_observation_distinguishes_phase_progress_from_supervision(self):
        t = self.f.engine.submit(self.f.request('3'))
        self.f.engine.start(t['id'], 'controller')
        with self.f.engine.tx() as db:
            db.execute("UPDATE tasks SET phase='preparing',updated=?,heartbeat=? WHERE id=?", (self.f.clock.value - 120, self.f.clock.value - 2, t['id']))
        item = observe(self.f.service)['tasks'][0]
        self.assertEqual(item['phase_age_seconds'], 120)
        self.assertEqual(item['heartbeat_age_seconds'], 2)
        self.assertIn('not confirmed', item['detail'])
        self.assertIn('unknown', item['supervision'])

    def test_stale_heartbeat_does_not_claim_prover_is_healthy(self):
        t = self.f.engine.submit(self.f.request('3')); self.f.engine.start(t['id'], 'controller')
        self.f.clock.value += 60
        self.assertEqual(observe(self.f.service)['tasks'][0]['supervision'], 'stale')

    def test_queue_reports_blocking_task_without_executing_it(self):
        self.f.controller.active = 'existing-paid-task'
        self.f.engine.submit(self.f.request(skill='wallet.balance', args={}))
        item = observe(self.f.service)['tasks'][0]
        self.assertEqual(item['blocked_by'], 'existing-paid-task')
        self.assertEqual(item['state'], 'submitted')
        self.assertEqual(self.f.engine.usage(), 0)

    def test_ping_still_requires_owner_channel_and_owner_signature(self):
        self.f.service.handle = lambda req: pong(self.f.service, req['params']['nonce'])
        cmd = {'method': 'owner.ping', 'params': {'nonce': 'c' * 32}}
        self.assertTrue(self.f.controller.handle_owner(self.f.command(cmd))['success'])
        with self.assertRaises(Rejected): self.f.controller.handle_owner(self.f.command(cmd, sender='other'))
        with self.assertRaises(Rejected): self.f.controller.handle_owner(self.f.command(cmd, key=self.f.other_key))

    def test_bounded_reads_complete_while_payment_worker_is_blocked(self):
        entered, release = threading.Event(), threading.Event()
        class SlowPayment(engines.FixtureAdapter):
            def prepare(self, task):
                entered.set()
                if not release.wait(3): raise RuntimeError('test timeout')
                return super().prepare(task)
        slow, fast = SlowPayment(), engines.FixtureAdapter()
        self.f.service.adapter_for = lambda skill: slow if skill == 'wallet.send' else fast
        paid = self.f.engine.submit(self.f.request('3'))
        read = self.f.engine.submit(self.f.request(skill='meta.skills', args={}))
        c = self.f.controller; c.schedule(paid['id']); c.schedule(read['id'])
        c.work()
        try:
            self.assertTrue(entered.wait(1))
            c.read_work(); c.read_future.result(timeout=1)
            self.assertEqual(self.f.engine.get(read['id'])['state'], 'completed')
            self.assertEqual(self.f.engine.get(paid['id'])['state'], 'working')
            self.assertEqual(fast.calls, 1)
        finally:
            release.set(); c.future.result(timeout=2)

    def test_wallet_reads_cannot_enter_observation_lane(self):
        t = self.f.engine.submit(self.f.request(skill='wallet.balance', args={}))
        self.f.controller.schedule(t['id']); self.f.controller.read_work()
        self.assertIsNone(self.f.controller.read_future)
        self.assertEqual(self.f.engine.get(t['id'])['state'], 'submitted')


class FastStatusTests(unittest.TestCase):
    def setUp(self):
        self.f = planners.PlannerTests(); self.f.setUp(); self.addCleanup(self.f.tearDown)

    def test_short_status_is_local_even_without_model_configuration(self):
        grant = self.f.grant(goal='?? status?')
        before = self.f.service.engine.db.execute('SELECT count(*) FROM tasks').fetchone()[0]
        goal = self.f.planner.start(grant)['goal']
        self.assertEqual(goal['state'], 'completed'); self.assertIn('no model call', goal['reply'])
        self.assertIsNone(self.f.planner.thread)
        self.assertEqual(self.f.service.engine.db.execute('SELECT count(*) FROM tasks').fetchone()[0], before)
        self.assertEqual(self.f.planner.start(grant)['goal']['id'], goal['id'])
        self.assertFalse((self.f.planner.root/'fixture-starts.txt').exists())

    def test_substantive_status_questions_are_not_intercepted(self):
        for prompt in ['search Exa for status pages', 'what is the status of bitcoin?', 'status then send 3 tokens']:
            self.assertFalse(is_status_question(prompt))

    def test_unsigned_status_cannot_read_agent_state(self):
        grant = self.f.grant(goal='status'); grant['body']['goal'] = 'ping'
        with self.assertRaises(Rejected): self.f.planner.start(grant)


class PaidInputValidationTests(unittest.TestCase):
    def service(self, schema):
        card = {'skills': [{'id': 'program.query'}], 'capabilities': {'extensions': [{
            'uri': PAYMENT_EXTENSION, 'params': {'prices': {'program.query': '3'},
            'inputSchemas': {'program.query': schema}}}]}}
        return SimpleNamespace(engine=SimpleNamespace(registry=default_registry()),
            get_agent_protocol=lambda: SimpleNamespace(verified_card=lambda _: card))

    def test_placeholder_and_missing_account_fail_before_pricing(self):
        # Even the old permissive peer card must not authorize a placeholder.
        old = {'type': 'object'}
        for params in [{'program_id': 'list_programs', 'params': {}},
                       {'program_id': 'a'*64, 'params': {}},
                       {'program_id': 'a'*64, 'params': {'account': 'not-an-account'}}]:
            with self.assertRaises(Rejected):
                Service.quote_for_skill(self.service(old), 'agent.task',
                    {'agent_address': 'peer', 'skill': 'program.query', 'params': params}, Quote('LEZ-testnet', 0, True))

    def test_valid_query_keeps_advertised_price(self):
        schema = default_registry().get('program.query').input_schema
        quote = Service.quote_for_skill(self.service(schema), 'agent.task',
            {'agent_address': 'peer', 'skill': 'program.query', 'params': {
                'program_id': 'a'*64, 'params': {'account': 'b'*64}}}, Quote('LEZ-testnet', 0, True))
        self.assertEqual(quote.maximum, 3)

    def test_missing_remote_schema_is_not_permission_to_spend(self):
        with self.assertRaisesRegex(Rejected, 'REMOTE_INPUT_SCHEMA_REQUIRED'):
            Service.quote_for_skill(self.service(None), 'agent.task',
                {'agent_address': 'peer', 'skill': 'program.query', 'params': {}}, Quote('LEZ-testnet', 0, True))
