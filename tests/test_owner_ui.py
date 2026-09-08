"""Real-signature tests for the owner-side form boundary; no network or model."""
from pathlib import Path
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from commons_relay.codec import Rejected, b64, canonical
from commons_relay.engine import Engine, Policy
from commons_relay.owner_ui import OwnerUi
from commons_relay.signing import Ed25519, verify_envelope
from commons_relay import owner_views

ROOT = Path(__file__).resolve().parents[1]


class OwnerUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.profiles = self.root / 'owners'
        self.profiles.mkdir(mode=0o700)
        self.owner = self.profiles / 'storage'
        self.crypto = Ed25519(self.owner)
        self.key = self.owner / 'owner-signing.pem'
        self.public = self.crypto.generate(self.key)
        self.info = self.owner / 'agent.json'
        self.info.write_bytes(canonical({'agent_id': 'test-storage-agent', 'owner_public_key': b64(self.public)}))
        self.info.chmod(0o600)
        self.clock = lambda: 1800000000
        self.ui = OwnerUi(self.profiles, clock=self.clock)
        self.engine = Engine(self.root / 'ledger', 'test-storage-agent', self.public, self.crypto,
                             policy=Policy(per_transaction=5, per_period=30, approval_ttl=7200),
                             clock=self.clock)

    def tearDown(self):
        self.engine.close()
        self.temp.cleanup()

    def compose(self, command, profile='storage'):
        return self.ui.handle({'action': 'compose', 'profile': profile, 'command': command})

    def inner(self, result):
        outer = verify_envelope(result['request']['params']['envelope'], self.public, self.crypto)
        self.assertEqual(outer['agent_id'], self.engine.agent)
        self.assertEqual(result['request']['params']['recipient'], self.engine.agent)
        self.assertEqual(outer['request_id'], result['command_id'])
        return outer['command']

    def planner_command(self, **overrides):
        value = {'kind': 'planner_start', 'goal': 'Explain what you can do.',
                 'delegate_key_id': 'ed25519:' + 'a' * 64, 'mode': 'read',
                 'allowed_skills': ['meta.status', 'storage.list'],
                 'maximum_spend': '0', 'max_steps': 6, 'expires_in': 600,
                 'policy_version': 1}
        value.update(overrides)
        return value

    def test_planner_read_commands_reach_the_signed_owner_channel(self):
        cases = [({'kind': 'planner_status'}, 'planner.status', {}),
                 ({'kind': 'planner_history', 'offset': 0}, 'planner.history', {'offset': 0}),
                 ({'kind': 'planner_goal', 'goal_id': 'chat-fixture'}, 'planner.goal', {'goal_id': 'chat-fixture'})]
        for command, method, params in cases:
            self.assertEqual(self.inner(self.compose(command)), {'method': method, 'params': params})

    def test_planner_start_is_a_real_scope_bound_owner_grant(self):
        response = self.compose(self.planner_command())
        inner = self.inner(response)
        self.assertEqual(inner['method'], 'planner.start')
        grant = verify_envelope(inner['params']['envelope'], self.public, self.crypto)
        self.assertEqual(response['conversation_id'], grant['grant_id'])
        self.assertEqual(grant['expires_at'], self.clock() + 600)
        self.assertEqual(grant['allowed_skills'], ['meta.status', 'storage.list'])
        self.assertEqual(grant['maximum_spend'], '0')
        self.engine.register_grant(inner['params']['envelope'])
        self.assertEqual(self.engine.db.execute('SELECT COUNT(*) FROM grants').fetchone()[0], 1)
        self.assertNotIn('PRIVATE KEY', canonical(response).decode())

    def test_planner_cancel_carries_a_separate_owner_revocation(self):
        response = self.compose({'kind': 'planner_cancel', 'goal_id': 'chat-fixture'})
        inner = self.inner(response)
        self.assertEqual(inner['method'], 'planner.cancel')
        body = verify_envelope(inner['params']['envelope'], self.public, self.crypto)
        self.assertEqual(body['grant_id'], 'chat-fixture')
        self.assertEqual(body['agent_id'], self.engine.agent)
        self.assertEqual(body['domain'], 'commons/commons_relay/revoke/v1')

    def test_read_only_chat_cannot_sign_wallet_spending_or_policy_changes(self):
        for changes in ({'allowed_skills': ['wallet.send']}, {'maximum_spend': '1'},
                        {'mode': 'actions', 'allowed_skills': ['meta.configure']}):
            with self.assertRaises(Rejected):
                self.compose(self.planner_command(**changes))

    def test_chat_rejects_boolean_limits_and_invalid_delegates_before_signing(self):
        for changes in ({'max_steps': True}, {'expires_in': True}, {'policy_version': True},
                        {'delegate_key_id': 'not-a-key'}, {'goal': ' '}, {'max_steps': 9}):
            with self.assertRaises(Rejected):
                self.compose(self.planner_command(**changes))

    def test_separate_chat_reviews_get_distinct_reconcilable_goal_ids(self):
        first = self.compose(self.planner_command())
        second = self.compose(self.planner_command())
        self.assertNotEqual(first['conversation_id'], second['conversation_id'])
        self.assertEqual(first['conversation_id'], self.inner(first)['params']['envelope']['body']['grant_id'])

    def test_catalog_does_not_export_paths_keys_or_seeds(self):
        result = self.ui.handle({'action': 'catalog'})
        self.assertEqual(result['profiles'], [{'name': 'storage', 'label': 'Storage', 'agent_id': self.engine.agent}])
        text = canonical(result).decode()
        self.assertNotIn(str(self.root), text)
        self.assertNotIn('PRIVATE KEY', text)
        self.assertEqual(result['network_requests'], 0)

    def test_status_request_is_signed_for_the_selected_agent(self):
        result = self.compose({'kind': 'snapshot', 'offset': 0})
        self.assertEqual(self.inner(result), {'method': 'owner.snapshot', 'params': {'offset': 0}})

    def test_zero_spend_task_roundtrips_through_the_real_engine(self):
        command = self.inner(self.compose({'kind': 'submit', 'skill': 'storage.list',
                                          'arguments': {}, 'expires_in': 600}))
        task = self.engine.submit(command['params']['envelope'], self.public)
        self.assertEqual(task['state'], 'submitted')
        self.assertEqual(task['maximum_spend'], '0')
        self.assertEqual(task['skill'], 'storage.list')

    def test_above_threshold_form_does_not_silently_approve(self):
        command = self.inner(self.compose({'kind': 'submit', 'skill': 'wallet.send',
                                          'arguments': {'recipient': 'test-recipient', 'amount': '6'},
                                          'expires_in': 600}))
        task = self.engine.submit(command['params']['envelope'], self.public)
        self.assertEqual(task['state'], 'input-required')
        self.assertEqual(task['phase'], 'awaiting-owner')
        self.assertEqual(self.engine.db.execute('SELECT COUNT(*) FROM spending').fetchone()[0], 0)

    def test_approval_is_bound_to_the_exact_intent(self):
        command = self.inner(self.compose({'kind': 'submit', 'skill': 'wallet.send',
                                          'arguments': {'recipient': 'test-recipient', 'amount': '6'},
                                          'expires_in': 600}))
        task = self.engine.submit(command['params']['envelope'], self.public)
        approval = self.inner(self.compose({'kind': 'approve', 'task_id': task['id'],
                                          'intent_hash': task['intent_hash'], 'policy_version': 1,
                                          'expires_in': 300}))
        approved = self.engine.approve(approval['params']['envelope'])
        self.assertEqual(approved['state'], 'submitted')
        changed = copy.deepcopy(approval['params']['envelope'])
        changed['body']['intent_hash'] = '0' * 64
        with self.assertRaises(Rejected):
            self.engine.approve(changed)

    def test_profile_path_escape_and_symlink_are_rejected(self):
        for name in ('../storage', '/tmp/owner', 'a/b', '', 'storage..'):
            with self.assertRaises(Rejected):
                self.compose({'kind': 'snapshot', 'offset': 0}, profile=name)
        (self.profiles / 'alias').symlink_to(self.owner, target_is_directory=True)
        with self.assertRaises(Rejected):
            self.compose({'kind': 'snapshot', 'offset': 0}, profile='alias')

    def test_wrong_owner_public_binding_fails_closed(self):
        self.info.write_bytes(canonical({'agent_id': self.engine.agent, 'owner_public_key': b64(bytes(32))}))
        with self.assertRaisesRegex(Rejected, 'OWNER_KEY_BINDING_CHANGED'):
            self.compose({'kind': 'snapshot', 'offset': 0})

    def test_private_profile_and_key_permissions_are_required(self):
        self.key.chmod(0o644)
        with self.assertRaises(Rejected):
            self.compose({'kind': 'snapshot', 'offset': 0})
        self.key.chmod(0o600)
        self.info.chmod(0o644)
        with self.assertRaises(Rejected):
            self.compose({'kind': 'snapshot', 'offset': 0})

    def test_profile_metadata_symlink_is_rejected(self):
        original = self.owner / 'real-agent.json'
        self.info.rename(original)
        self.info.symlink_to(original)
        with self.assertRaises(Rejected):
            self.compose({'kind': 'snapshot', 'offset': 0})

    def test_invalid_amount_is_rejected_before_signing(self):
        with self.assertRaises(Rejected):
            self.compose({'kind': 'submit', 'skill': 'wallet.send',
                          'arguments': {'recipient': 'test-recipient', 'amount': '1e3'},
                          'expires_in': 600})

    def test_unknown_extension_signs_intent_but_does_not_bypass_remote_registry(self):
        command = self.inner(self.compose({'kind': 'submit', 'skill': 'custom.preview',
                                          'arguments': {'value': 'test'}, 'expires_in': 600}))
        with self.assertRaisesRegex(Rejected, 'UNKNOWN_SKILL'):
            self.engine.submit(command['params']['envelope'], self.public)

    def test_shell_and_extra_fields_have_no_signing_route(self):
        for command in ({'kind': 'shell', 'expires_in': 600, 'command': 'unused'},
                        {'kind': 'snapshot', 'offset': 0, 'command': 'unused'},
                        {'kind': 'snapshot', 'offset': True}):
            with self.assertRaises(Rejected):
                self.compose(command)

    def test_isolated_helper_process_returns_one_typed_frame(self):
        env = {'PATH': os.environ['PATH'], 'COMMONS_RELAY_OWNER_ROOT': str(self.profiles)}
        result = subprocess.run([sys.executable, '-I', str(ROOT / 'commons_relay_owner_ui.py')],
                                input=canonical({'action': 'catalog'}) + b'\n',
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=15)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertTrue(json.loads(result.stdout)['success'])
        self.assertEqual(result.stderr, b'')

    def test_oversized_helper_frame_is_rejected_without_echo(self):
        env = {'PATH': os.environ['PATH'], 'COMMONS_RELAY_OWNER_ROOT': str(self.profiles)}
        result = subprocess.run([sys.executable, '-I', str(ROOT / 'commons_relay_owner_ui.py')],
                                input=b'x' * 60001 + b'\n', stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env, timeout=15)
        self.assertEqual(json.loads(result.stdout)['error'], 'INVALID_OWNER_UI_FRAME')
        self.assertLess(len(result.stdout), 150)

    def test_owner_snapshot_excludes_large_results_and_paginates(self):
        task_ids = []
        for _ in range(10):
            command = self.inner(self.compose({'kind': 'submit', 'skill': 'storage.list',
                                              'arguments': {}, 'expires_in': 600}))
            task_ids.append(self.engine.submit(command['params']['envelope'], self.public)['id'])
        with self.engine.tx() as db:
            db.execute('UPDATE tasks SET result=?', (json.dumps({'large': 'x' * 20000}),))
        first = owner_views.snapshot(self.engine)
        second = owner_views.snapshot(self.engine, first['next_offset'])
        self.assertEqual(len(first['tasks']), 8)
        self.assertEqual(len(second['tasks']), 2)
        self.assertTrue(first['has_more'])
        self.assertFalse(second['has_more'])
        self.assertEqual(first['task_count'], 10)
        self.assertLess(len(canonical(first)), 12000)
        self.assertNotIn('large', canonical(first).decode())
        detail = owner_views.task_details(self.engine, task_ids[0])['task']
        self.assertTrue(detail['arguments_complete'])
        self.assertFalse(detail['result_complete'])
        self.assertIn('Preview truncated', detail['result_preview'])

    def test_skill_pages_preserve_all_default_skills(self):
        first = owner_views.skills_page(self.engine)
        second = owner_views.skills_page(self.engine, first['next_offset'])
        ids = [row['id'] for row in first['skills'] + second['skills']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), {row['id'] for row in self.engine.registry.describe()})
        self.assertFalse(second['has_more'])
        detail = owner_views.skill_details(self.engine, 'wallet.send')['skill']
        self.assertEqual(detail['input_schema']['required'], ['recipient', 'amount'])

    def test_invalid_pages_are_rejected(self):
        for value in (-1, True, '0', 100001):
            with self.assertRaises(Rejected):
                owner_views.snapshot(self.engine, value)


if __name__ == '__main__':
    unittest.main()
