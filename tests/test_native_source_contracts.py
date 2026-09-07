from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
class NativeContracts(unittest.TestCase):
    def test_timeout_uses_strong_sdk_type_not_an_extra_argument(self):
        text=(ROOT/'native/core/commons_relay_module.cpp').read_text()
        self.assertIn('invokeRemoteMethod(module,method,args,Timeout(15000))',text)
        self.assertNotIn('invokeRemoteMethod(module,method,args,15000)',text)
    def test_bridge_can_upload_only_encrypted_vault_files(self):
        text=(ROOT/'native/core/commons_relay_module.cpp').read_text()
        self.assertIn('/vault/blobs',text);self.assertIn('CSTVLT1',text)
        self.assertIn('UPLOAD_MUST_BE_ENCRYPTED',text)
    def test_no_raw_upstream_error_forwarding(self):
        text=(ROOT/'native/core/commons_relay_module.cpp').read_text()
        self.assertIn('MODULE_OPERATION_FAILED',text)
        self.assertNotIn('getError()',text)
    def test_core_child_does_not_inherit_credentials(self):
        text=(ROOT/'native/core/commons_relay_module.cpp').read_text()
        self.assertIn('QProcessEnvironment env;',text)
        self.assertNotIn('systemEnvironment()',text)
    def test_storage_config_matches_pinned_actual_library(self):
        text=(ROOT/'native/core/commons_relay_module.cpp').read_text()
        self.assertIn('"listen-ip","127.0.0.1"',text)
        self.assertIn('"listen-port",0',text)
        self.assertNotIn('"listen-addrs"',text)
        self.assertIn('QFile::ReadOwner|QFile::WriteOwner|QFile::ExeOwner',text)
    def test_node_lifecycle_is_idempotent(self):
        text=(ROOT/'native/core/commons_relay_module.cpp').read_text()
        self.assertIn('if(storageInitialized_)',text);self.assertIn('if(storageStarted_)',text)
    def test_signed_mutation_still_required(self):
        text=(ROOT/'commons_relay/service.py').read_text()
        self.assertIn("self.engine.submit(params['envelope']",text)
        self.assertNotIn('shell=True',text)
if __name__=='__main__':unittest.main()
