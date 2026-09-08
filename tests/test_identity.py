from pathlib import Path
import tempfile
import os
import json
import unittest
from commons_relay.identity import create_from_wallet_export
from commons_relay.file_crypto import Sodium
from commons_relay.vault import Vault
from commons_relay.codec import Rejected
class IdentityTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.crypto=Sodium(os.environ.get('COMMONS_RELAY_TEST_SODIUM'))
    def tearDown(self):self.tmp.cleanup()
    def export(self,name='export',seed='01'):
        path=self.root/name;path.write_text(json.dumps({'schema':1,'root_account':'02'*32,'root_npk':'03'*32,'address':'lez-'+'03'*32,'signing_seed':seed*32,'encryption_seed':'04'*32}));path.chmod(0o600);return path
    def test_import_matches_vault_identity_and_roundtrips_signatures(self):
        result=create_from_wallet_export(self.root/'vault',self.export(),self.crypto)
        (self.root/'in').mkdir();(self.root/'out').mkdir();vault=Vault(self.root/'vault',self.root/'in',self.root/'out',self.crypto)
        try:
            from commons_relay.codec import b64
            self.assertEqual(b64(vault.signing_public),result['signing_key']);self.assertEqual(b64(vault.box_public),result['box_key'])
            signature=vault.signer.sign(vault.signing_private,b'fixture');self.assertTrue(vault.signer.verify(vault.signing_public,b'fixture',signature))
        finally:vault.close()
    def test_deterministic_child_seed_import(self):
        a=create_from_wallet_export(self.root/'a',self.export('ea'),self.crypto);b=create_from_wallet_export(self.root/'b',self.export('eb'),self.crypto);self.assertEqual(a,b)
    def test_distinct_signing_seeds_separate_identities(self):
        a=create_from_wallet_export(self.root/'a',self.export('ea'),self.crypto);b=create_from_wallet_export(self.root/'b',self.export('eb','05'),self.crypto);self.assertNotEqual(a['signing_key'],b['signing_key'])
    def test_existing_identity_is_not_overwritten(self):
        export=self.export();create_from_wallet_export(self.root/'a',export,self.crypto)
        with self.assertRaises(Rejected):create_from_wallet_export(self.root/'a',export,self.crypto)
    def test_public_export_has_no_child_seeds(self):
        result=create_from_wallet_export(self.root/'a',self.export(),self.crypto);self.assertNotIn('signing_seed',result);self.assertNotIn('encryption_seed',result)
    def test_insecure_export_rejected(self):
        path=self.export();path.chmod(0o644)
        with self.assertRaises(Rejected):create_from_wallet_export(self.root/'a',path,self.crypto)
    def test_address_binding_rejected(self):
        path=self.export();value=json.loads(path.read_text());value['address']='other';path.write_text(json.dumps(value))
        with self.assertRaises(Rejected):create_from_wallet_export(self.root/'a',path,self.crypto)
    def test_no_wallet_key_field_accepted(self):
        path=self.export();value=json.loads(path.read_text());value['root_secret']='not copied';path.write_text(json.dumps(value))
        with self.assertRaises(Rejected):create_from_wallet_export(self.root/'a',path,self.crypto)
if __name__=='__main__':unittest.main()
