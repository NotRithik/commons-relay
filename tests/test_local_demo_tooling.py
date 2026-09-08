from pathlib import Path
import json
import re
import unittest

ROOT=Path(__file__).resolve().parents[1]
PIN='47eba256479f6f785acbd138834340703cd03401'

class LocalDemoToolingTests(unittest.TestCase):
    def test_prerequisites_match_wallet_source_pin(self):
        prereq=json.loads((ROOT/'integration/prerequisites.json').read_text())
        self.assertEqual(prereq['lez_revision'],PIN)
        cargo=(ROOT/'wallet/Cargo.toml').read_text()
        self.assertIn(PIN,cargo)
        source=(ROOT/'wallet/src/main.rs').read_text()
        self.assertIn(f'const PIN:&str="{PIN}"',source)

    def test_proof_archives_are_hash_and_size_pinned_https_github_assets(self):
        prereq=json.loads((ROOT/'integration/prerequisites.json').read_text())
        for platform,items in prereq['platforms'].items():
            for name in ['prover','circuits','rapidsnark']:
                item=items[name]
                self.assertTrue(item['url'].startswith('https://github.com/'),(platform,name))
                self.assertRegex(item['sha256'],r'^[0-9a-f]{64}$')
                self.assertGreater(item['bytes'],0)
        fetch=(ROOT/'scripts/fetch-proof-deps.py').read_text()
        self.assertIn("for kind in ['prover','circuits','rapidsnark']",fetch)
        self.assertIn("sha256(temporary)!=item['sha256']",fetch)
        self.assertNotIn("shell=True",fetch)

    def test_fetch_and_offline_build_are_separate(self):
        text=(ROOT/'scripts/prepare-local.sh').read_text()
        self.assertIn('fetch|build|all',text)
        self.assertIn('CARGO_NET_OFFLINE=true',text)
        self.assertIn('cargo +1.94.0 build --locked --offline',text)
        self.assertIn('cargo +1.94.0 fetch --locked',text)
        self.assertIn('--features standalone -p sequencer_service',text)

    def test_demo_is_loopback_only_and_forces_real_proofs(self):
        text=(ROOT/'scripts/demo-local.py').read_text()
        self.assertIn("URL='http://127.0.0.1:34341'",text)
        self.assertIn("'RISC0_DEV_MODE':'0'",text)
        self.assertIn("'RISC0_PROVER':'ipc'",text)
        self.assertIn("'network':'local-standalone-only'",text)
        self.assertNotIn('testnet.lez.logos.co',text)
        self.assertNotIn('API_KEY',text)
        self.assertNotIn('OPENAI',text.upper())

    def test_offline_wallet_creation_precedes_local_node(self):
        demo=(ROOT/'scripts/demo-local.py').read_text()
        self.assertLess(demo.index("'init-local-offline'"),demo.index('subprocess.Popen([str(node)'))
        wallet=(ROOT/'wallet/src/main.rs').read_text()
        branch=wallet.split('if mode=="init-local-offline"',1)[1].split('if mode=="init"',1)[0]
        self.assertIn('http://127.0.0.1:34341/',branch)
        self.assertIn('network_transactions',branch)
        self.assertNotIn('SequencerClientBuilder',branch)

if __name__=='__main__':unittest.main()
