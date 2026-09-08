"""Behavioral checks for the standalone demo's real genesis-vault bootstrap."""
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('relay_local_demo', ROOT / 'scripts/demo-local.py')
DEMO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEMO)


class LocalGenesisFlowTests(unittest.TestCase):
    def test_vault_claim_precedes_independent_payer_balance_check(self):
        calls = []
        def wallet(*args, **kwargs):
            calls.append(args)
            if args[0] == 'prepare':
                body = json.loads(args[3].read_text())
                self.assertEqual(body['kind'], 'claim-public-vault')
                self.assertEqual(body['arguments'], {'amount': '100'})
                self.assertEqual(stat.S_IMODE(args[3].stat().st_mode), 0o600)
                return {'state': 'prepared', 'private': False}
            if args[0] == 'broadcast':
                return {'state': 'confirmed', 'tx_hash': 'test-only-hash', 'block_id': 3}
            return {'balance': '100'}
        with tempfile.TemporaryDirectory() as tmp:
            receipt = DEMO.claim_genesis_funds(wallet, Path(tmp), 'test-payer', 12345)
        self.assertEqual([call[0] for call in calls], ['prepare', 'broadcast', 'query'])
        self.assertEqual(receipt['verified_payer_balance'], '100')
        self.assertEqual(calls[-1][2], 'test-payer')

    def test_empty_payer_cannot_be_reported_as_success(self):
        def wallet(*args, **kwargs):
            return {'prepare': {'state': 'prepared', 'private': False},
                    'broadcast': {'state': 'confirmed', 'tx_hash': 'h', 'block_id': 3},
                    'query': {'balance': '0'}}[args[0]]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, 'did not fund the payer'):
                DEMO.claim_genesis_funds(wallet, Path(tmp), 'payer', 12345)

    def test_unprepared_claim_is_never_broadcast(self):
        calls = []
        def wallet(*args, **kwargs):
            calls.append(args[0])
            return {'state': 'unknown', 'private': False}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, 'was not prepared'):
                DEMO.claim_genesis_funds(wallet, Path(tmp), 'payer', 12345)
        self.assertEqual(calls, ['prepare'])

    def test_ambiguous_broadcast_reconciles_without_sending_again(self):
        calls = []
        def wallet(*args, **kwargs):
            calls.append(args[0])
            return {'state': 'unknown' if len(calls) == 1 else 'confirmed'}
        with patch.object(DEMO.time, 'sleep'):
            self.assertEqual(DEMO.confirm_operation(wallet, Path('/unused'), 'operation')['state'], 'confirmed')
        self.assertEqual(calls, ['broadcast', 'reconcile'])

    def test_only_allowlisted_failure_codes_are_published(self):
        self.assertEqual(DEMO.wallet_failure_code('private data\nRelay wallet: INSUFFICIENT_PUBLIC_BALANCE'),
                         'INSUFFICIENT_PUBLIC_BALANCE')
        self.assertEqual(DEMO.wallet_failure_code('Relay wallet: PRIVATE_SECRET_VALUE'), 'WALLET_STAGE_FAILED')
        self.assertEqual(DEMO.wallet_failure_code('Relay wallet: sk-example-secret'), 'WALLET_STAGE_FAILED')
        self.assertEqual(DEMO.wallet_failure_code('Traceback with a private path'), 'WALLET_STAGE_FAILED')

    def test_genesis_step_is_before_the_private_proof(self):
        source = (ROOT / 'scripts/demo-local.py').read_text()
        self.assertLess(source.index("report['genesis_claim'] ="), source.index("prepared=call('prepare',run/'wallet','local-real-shield'"))
        self.assertIn("'RISC0_DEV_MODE':'0'", source)


if __name__ == '__main__':
    unittest.main()
