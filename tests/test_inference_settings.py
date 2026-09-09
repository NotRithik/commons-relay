"""Real owner signatures / libsodium, synthetic credentials, no model requests."""
import hashlib
import json
from pathlib import Path
import subprocess
import time
import unittest
from unittest.mock import patch
import test_planner as fixtures
from commons_relay.codec import Rejected, b64, canonical
from commons_relay.inference_settings import InferenceStore, endpoint, settings, seal_update
from commons_relay.planner import Configuration
from commons_relay.signing import sign_envelope

class EndpointTests(unittest.TestCase):
    def test_https_and_exact_loopback(self):
        cases = {'https://api.example/v1/': 'https://api.example/v1',
                 'http://localhost:8080/v1': 'http://localhost:8080/v1',
                 'http://127.0.0.1:11434/v1': 'http://127.0.0.1:11434/v1',
                 'http://[::1]:8080/v1': 'http://[::1]:8080/v1'}
        for raw, expected in cases.items(): self.assertEqual(endpoint(raw), expected)
    def test_bad_authority_path_scheme_or_local_alias(self):
        for raw in ['http://api.example/v1', 'http://127.1/v1', 'http://2130706433/v1',
                    'http://localhost.evil.test/v1', 'https://u:p@example.test/v1',
                    'https://api.example/v1?key=x', 'https://api.example/#x',
                    'https://api.example/a/../v1', 'https://api.example/%2e/v1',
                    'file:///tmp/model', 'https://api.example\\x', 'https://api.example/ v1',
                    'https://api.example/v1/responses', 'https://api.example/v1/chat/completions',
                    'http://[::2]:8080/v1', 'https://api.example:0/v1', None]:
            with self.subTest(raw=raw), self.assertRaises(Rejected): endpoint(raw)

class SharedEndpointFixtureTests(unittest.TestCase):
    def test_reviewed_worker_fixture_cases(self):
        from commons_relay.inference_settings import MODEL
        doc = json.loads((Path(__file__).parent/'fixtures/inference-endpoint-cases.json').read_text())
        for item in doc['base_url_cases']:
            with self.subTest(case=item['id']):
                if item['valid']: self.assertEqual(endpoint(item['input']), item['canonical'])
                else:
                    with self.assertRaises(Rejected): endpoint(item['input'])
        for item in doc['model_id_cases']:
            generated = item.get('generate', {})
            value = item.get('input', generated.get('prefix', '') + generated.get('repeat', '') * generated.get('repeat_count', 0))
            with self.subTest(case=item['id']): self.assertEqual(bool(MODEL.fullmatch(value)), item['valid'])

class InferenceSettingsTests(unittest.TestCase):
    setUp = fixtures.PlannerTests.setUp
    tearDown = fixtures.PlannerTests.tearDown
    write_private = fixtures.PlannerTests.write_private
    enable = fixtures.PlannerTests.enable
    grant = fixtures.PlannerTests.grant
    wait = fixtures.PlannerTests.wait

    def chosen(self, **changes):
        value = {'api': 'responses', 'endpoint': 'https://api.openai.com/v1', 'model': 'gpt-fixture',
                 'credential_mode': 'keep', 'max_output_tokens': 1536,
                 'input_micro_per_million': 250000, 'output_micro_per_million': 1200000}
        value.update(changes); return value
    def update(self, chosen=None, key='', expected=None, recipient=None):
        self.enable()
        status = self.planner.status()
        public = recipient or status['configuration_box_key']
        command = {'kind': 'planner_configure', 'settings': chosen or self.chosen(),
                   'expected_hash': expected or status['configuration_hash'], 'box_key': public, 'api_key': key,
                   'credential_digest': status['credential_digest']}
        result = seal_update(command, self.service.engine.agent, self.owner, self.key, int(time.time()))
        return result['params']['envelope']
    def apply(self, envelope):
        return self.service.handle({'id': 'settings-fixture', 'method': 'planner.configure', 'params': {'envelope': envelope}})

    def test_model_can_be_custom_without_changing_endpoint_key(self):
        result = self.apply(self.update(self.chosen(model='provider/model:quantized')))
        self.assertTrue(result['inference_updated'])
        config = self.planner.configuration()
        self.assertEqual(config.model, 'provider/model:quantized')
        self.assertEqual(config.api_key_file, str(self.key_file))
    def test_existing_key_cannot_follow_endpoint_change(self):
        envelope = self.update(self.chosen(endpoint='https://another.example/v1'))
        with self.assertRaisesRegex(Rejected, 'KEY_DESTINATION_CHANGED'): self.apply(envelope)
        self.assertFalse((self.profile/'inference.json').exists())
    def test_anonymous_loopback_uses_no_prior_key(self):
        result = self.apply(self.update(self.chosen(api='chat-completions', endpoint='http://localhost:11434/v1',
            model='local-model:latest', credential_mode='none', input_micro_per_million=0, output_micro_per_million=0)))
        self.assertFalse(result['planner']['credential_configured'])
        self.assertEqual(self.planner.configuration().api_key_file, '')
        self.assertEqual(result['planner']['provider'], 'localhost:11434')
    def test_replace_seals_credential_and_saves_only_private_key_file(self):
        secret = 'synthetic-provider-token-not-a-real-key'
        envelope = self.update(self.chosen(endpoint='https://provider.example/v1', model='provider/model', credential_mode='replace'), key=secret)
        self.assertNotIn(secret, canonical(envelope).decode())
        result = self.apply(envelope)
        self.assertNotIn(secret, canonical(result).decode())
        self.assertNotIn(secret, (self.profile/'inference.json').read_text())
        path = Path(self.planner.configuration().api_key_file)
        self.assertEqual(path.read_text(), secret)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
    def test_wrong_recipient_cannot_decrypt_key(self):
        store = InferenceStore(self.planner); crypto, _, _ = store.box_pair()
        another, _ = crypto.box_keypair()
        envelope = self.update(self.chosen(credential_mode='replace'), key='synthetic-token', recipient=b64(another))
        with self.assertRaises(Rejected): self.apply(envelope)
        self.assertFalse((self.profile/'inference.json').exists())
    def test_modified_signed_settings_rejected(self):
        envelope = self.update(); envelope['body']['settings']['endpoint'] = 'https://attacker.example/v1'
        with self.assertRaises(Rejected): self.apply(envelope)
    def test_same_request_returns_receipt_without_rewrite(self):
        envelope = self.update(); self.apply(envelope)
        before = (self.profile/'inference.json').stat().st_mtime_ns
        result = self.apply(envelope)
        self.assertTrue(result['replayed'])
        self.assertEqual(before, (self.profile/'inference.json').stat().st_mtime_ns)
    def test_changed_intent_cannot_reuse_request_id(self):
        envelope = self.update(); self.apply(envelope)
        body = dict(envelope['body']); body['settings'] = self.chosen(model='different-model')
        bad = sign_envelope(body, self.key, self.owner)
        with self.assertRaisesRegex(Rejected,'REQUEST_ID_REUSED'): self.apply(bad)
    def test_stale_review_cannot_overwrite_new_configuration(self):
        first = self.update(self.chosen(model='first-owner-change')); second = self.update(self.chosen(model='new-model'))
        self.apply(first)
        with self.assertRaisesRegex(Rejected,'CHANGED_REVIEW_AGAIN'): self.apply(second)
    def test_busy_planner_refuses_settings_changes(self):
        envelope = self.update(); self.planner.active = 'synthetic-running-goal'
        try:
            with self.assertRaisesRegex(Rejected,'WHILE_BUSY'): self.apply(envelope)
        finally: self.planner.active = None
    def test_settings_expiry_and_agent_scope(self):
        for changes in [{'expires_at':int(time.time())-1}, {'agent_id':'some-other-agent'}]:
            envelope = self.update(); body = {**envelope['body'], **changes}
            with self.assertRaises(Rejected): self.apply(sign_envelope(body,self.key,self.owner))
    def test_no_plaintext_key_in_status_or_operator_path_disclosure(self):
        self.enable(); status = json.dumps(self.planner.status())
        for text in ['sk-fixture-not-a-real-credential', 'api.env', 'delegate.pem', str(self.root)]:
            self.assertNotIn(text, status)
    def test_custom_settings_require_signed_inference_hash_in_goal(self):
        self.apply(self.update(self.chosen(endpoint='http://localhost:8080/v1',model='local:model',credential_mode='none')))
        envelope = self.grant(); body = dict(envelope['body']); body.pop('inference_hash')
        with self.assertRaisesRegex(Rejected,'INVALID_GOAL_GRANT'): self.planner.start(sign_envelope(body,self.key,self.owner))
        self.planner.start(self.grant(inference_hash=self.planner.configuration().configuration_hash)); self.wait()
        self.assertEqual(self.planner.view('chat-fixture')['goal']['state'], 'completed')
    def test_config_snapshot_survives_mutable_base_file_change_before_child_start(self):
        self.runner.write_text(self.runner.read_text()+"\nimport os\npathlib.Path('snapshot-observed.json').write_text(pathlib.Path(os.environ['COMMONS_PLANNER_CONFIG']).read_text())\n")
        self.config['runner_sha256'] = hashlib.sha256(self.runner.read_bytes()).hexdigest()
        self.enable(); original = subprocess.Popen
        def change_base_before_child(*args, **kwargs):
            command = args[0] if args else kwargs.get('args', [])
            if str(self.runner) in command:
                changed = {**self.config, 'model': 'gpt-changed-after-validation'}
                self.write_private(self.profile/'planner.json', changed)
            return original(*args, **kwargs)
        with patch('commons_relay.planner.subprocess.Popen', side_effect=change_base_before_child):
            self.planner.start(self.grant()); self.wait()
        snapshot = json.loads((self.planner.root/'snapshot-observed.json').read_text())
        self.assertEqual(snapshot['model'], 'gpt-fixture')
        self.assertEqual(snapshot['credential_sha256'], hashlib.sha256(self.key_file.read_bytes()).hexdigest())
    def test_invalid_model_price_limit_and_plaintext_action_fail_before_signing(self):
        for change in [{'model':'model\nINJECT'},{'max_output_tokens':True},{'max_output_tokens':9000},
                       {'input_micro_per_million':-1},{'api':'arbitrary-plugin'},{'credential_mode':'unknown'}]:
            with self.subTest(change=change), self.assertRaises(Rejected): self.update(self.chosen(**change))
        with self.assertRaises(Rejected): self.update(self.chosen(), key='must-not-be-sent')

    def test_saved_destination_tamper_cannot_reuse_signed_key(self):
        self.apply(self.update(self.chosen(endpoint='https://provider-a.example/v1', credential_mode='replace'), key='synthetic-only-key'))
        path = self.profile/'inference.json'; saved = json.loads(path.read_text())
        saved['settings']['endpoint'] = 'https://provider-b.example/v1'
        path.write_text(json.dumps(saved)); path.chmod(0o600)
        with self.assertRaises(Rejected): self.planner.configuration()

    def test_same_path_replaced_credential_is_not_the_reviewed_credential(self):
        self.apply(self.update(self.chosen(credential_mode='replace'), key='synthetic-first-key'))
        path = Path(self.planner.configuration().api_key_file)
        path.write_text('synthetic-replacement-key')
        with self.assertRaisesRegex(Rejected, 'INFERENCE_CREDENTIAL_CHANGED'): self.planner.configuration()

    def test_unsigned_or_altered_saved_envelope_fails_closed(self):
        self.apply(self.update(self.chosen(model='new-model')))
        path = self.profile/'inference.json'; saved = json.loads(path.read_text())
        saved['owner_envelope']['body']['settings']['model'] = 'tampered-model'
        path.write_text(json.dumps(saved)); path.chmod(0o600)
        with self.assertRaises(Rejected): self.planner.configuration()

    def test_legacy_model_start_without_reviewed_hash_is_rejected(self):
        self.enable(); envelope = self.grant(); body = dict(envelope['body']); body.pop('inference_hash')
        with self.assertRaisesRegex(Rejected, 'INVALID_GOAL_GRANT'):
            self.planner.start(sign_envelope(body,self.key,self.owner))
        self.assertIsNone(self.planner.thread)
        self.assertEqual(self.planner.db.execute('SELECT count(*) FROM conversations').fetchone()[0], 0)

    def test_key_change_after_start_review_fails_before_fixture_runner(self):
        self.enable(); original = self.planner._run
        def mutate(grant, config):
            self.key_file.write_text('synthetic-key-changed-after-review')
            return original(grant, config)
        with patch.object(self.planner, '_run', side_effect=mutate):
            self.planner.start(self.grant()); self.wait()
        goal = self.planner.view('chat-fixture')['goal']
        self.assertEqual(goal['state'], 'failed')
        self.assertEqual(goal['error'], 'INFERENCE_CREDENTIAL_CHANGED')
        self.assertFalse((self.planner.root/'snapshot-observed.json').exists())

    def test_stale_legacy_review_rejected_before_any_conversation(self):
        self.enable(); envelope = self.grant()
        self.config['model'] = 'different-model'; self.enable()
        with self.assertRaisesRegex(Rejected, 'CHANGED_REVIEW_AGAIN'): self.planner.start(envelope)
        self.assertEqual(self.planner.db.execute('SELECT count(*) FROM conversations').fetchone()[0], 0)
