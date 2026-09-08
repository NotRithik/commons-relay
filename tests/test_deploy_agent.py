import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from commons_relay.codec import Rejected
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('deploy_agent',ROOT/'scripts/deploy-agent.py');deploy=importlib.util.module_from_spec(spec);spec.loader.exec_module(deploy)
class DeployAgentTests(unittest.TestCase):
    def test_role_exports_do_not_publish_spend_or_configuration_skills(self):
        forbidden={'wallet.send','program.call','program.deploy','meta.configure'}
        for role in deploy.ROLES.values():self.assertFalse(forbidden & set(role['exports']))
    def test_bundled_storage_snapshot_is_pinned_testnet_records(self):
        value=deploy.storage_snapshot(ROOT/'config/logos-storage-testnet.json');self.assertEqual(value['name'],'logos.test');self.assertEqual(len(value['records']),6);self.assertTrue(all(x.startswith('spr:') for x in value['records']))
    def test_bad_storage_record_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'p.json';p.write_text(json.dumps({'schema':1,'name':'logos.test','records':['http://not-allowed']}))
            with self.assertRaises(Rejected):deploy.storage_snapshot(p)
    def test_trusted_input_symlink_is_refused_before_resolution(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);real=d/'real';real.write_text('x');link=d/'link';link.symlink_to(real)
            with self.assertRaises(Rejected):deploy.owned_file(link)
    def test_trusted_directory_symlink_is_refused_before_resolution(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d);real=d/'real';real.mkdir();link=d/'link';link.symlink_to(real,target_is_directory=True)
            with self.assertRaises(Rejected):deploy.owned_directory(link)
    def test_role_ports_are_distinct_and_non_privileged(self):
        values=[34363,34364,34365];self.assertEqual(len(set(values)),3);self.assertTrue(all(1024<=p<=65535 for p in values))
    def test_new_agent_delivery_uses_built_in_logos_dev_preset(self):
        source=(ROOT/'scripts/deploy-agent.py').read_text()
        self.assertIn("{'mode':'logos.dev','tcpPort':delivery_port}",source)
        self.assertNotIn("'entryNodes':[]",source)
    def test_deployment_script_pins_core_tmpdir(self):
        source=(ROOT/'scripts/deploy-agent.py').read_text()
        self.assertIn("env['TMPDIR']=str(core_tmp)+'/'",source)
        self.assertIn("'core_tmpdir':str(core_tmp)+'/'",source)
    def test_daemon_environment_pins_testnet_and_real_prover_mode(self):
        class A:pass
        a=A();a.wallet_binary=Path('/tmp/wallet');a.sodium_library=Path('/tmp/sodium')
        env=deploy.daemon_env(a,Path('/tmp/agents'),Path('/tmp/wallets'))
        self.assertEqual(env['COMMONS_ALLOW_PUBLIC_TESTNET'],'1');self.assertEqual(env['RISC0_DEV_MODE'],'0');self.assertEqual(env['RISC0_PROVER'],'ipc')
if __name__=='__main__':unittest.main()
