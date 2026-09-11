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
        self.assertIn('int listenPort=0',text);self.assertIn('"listen-port",listenPort',text)
        self.assertNotIn('"listen-addrs"',text)
        self.assertIn('QFile::ReadOwner|QFile::WriteOwner|QFile::ExeOwner',text)
    def test_node_lifecycle_is_idempotent(self):
        text=(ROOT/'native/core/commons_relay_module.cpp').read_text()
        self.assertIn('if(storageInitialized_)',text);self.assertIn('if(storageStarted_)',text)
    def test_event_wildcard_matches_logos_empty_name_contract(self):
        text=(ROOT/'native/core/commons_relay_module.cpp').read_text()
        self.assertIn('client->onEvent(object,QString(),',text)
        self.assertNotIn('client->onEvent(object,QString("*"),',text)
    def test_nonexistent_download_target_checks_its_existing_parent(self):
        text=(ROOT/'native/core/commons_relay_module.cpp').read_text()
        branch=text.split('action=="storage.download"',1)[1].split('action=="storage.manifests"',1)[0]
        self.assertIn('QFileInfo(file.absolutePath()).canonicalFilePath()!=allowed',branch)
        self.assertNotIn('file.canonicalPath()!=allowed',branch)
    def test_delivery_local_mode_is_loopback_and_lan_requires_explicit_private_address(self):
        text=(ROOT/'native/core/commons_relay_module.cpp').read_text().split('void CommonsRelayModule::handleDeliveryBridge')[1]
        self.assertIn('"listenAddress",mode=="lan" ? "0.0.0.0" : "127.0.0.1"',text)
        self.assertIn('LAN_ADDRESS_REQUIRES_EXPLICIT_LAN_MODE',text)
        self.assertIn('PRIVATE_LAN_ADDRESS_REQUIRED',text)
        self.assertIn('PRIVATE_LAN_PEER_REQUIRED',text)
        self.assertIn('cfg.insert("nat",mode=="lan" ? "extip:"+config.value("advertiseAddress").toString() : "extip:127.0.0.1")',text)
        self.assertIn('mode=="logos.dev"',text)
        self.assertIn('cfg.insert("preset","logos.dev")',text)
        self.assertIn('cfg.insert("discv5UdpPort",port)',text)
        self.assertIn('DELIVERY_PRESET_OVERRIDES_DENIED',text)
        self.assertIn('LOCAL_TEST_PEER_REQUIRED',text)
    def test_signed_mutation_still_required(self):
        text=(ROOT/'commons_relay/service.py').read_text()
        self.assertIn("self.engine.submit(params['envelope']",text)
        self.assertNotIn('shell=True',text)
if __name__=='__main__':unittest.main()

class WalletBridgeDeterminismContracts(unittest.TestCase):
    def test_wallet_bridge_removes_float_proof_seconds(self):
        text=(ROOT/'native/wallet/relay_wallet_module.cpp').read_text()
        self.assertIn('typedResult_.take("proof_seconds")',text)
        self.assertIn('typedResult_.insert("proof_millis",QString::number(qRound64(',text)
        self.assertNotIn('typedResult_.insert("proof_seconds"',text)
    def test_proof_thread_override_is_strictly_bounded(self):
        text=(ROOT/'native/wallet/relay_wallet_module.cpp').read_text()
        self.assertIn('COMMONS_RELAY_PROOF_THREADS',text)
        self.assertIn('^[1-8]$',text)
        self.assertIn('proofThreads="2"',text)
        self.assertIn('env.insert("RAYON_NUM_THREADS",proofThreads)',text)

class LocalStoragePeerSourceContracts(unittest.TestCase):
    def test_local_storage_connect_is_loopback_only(self):
        text=(ROOT/'native/core/commons_relay_module.cpp').read_text()
        branch=text.split('action=="storage.connect-local"',1)[1].split('action=="storage.publish-card"',1)[0]
        self.assertIn('^/ip4/127[.]0[.]0[.]1/tcp/[0-9]{4,5}$',branch)
        self.assertIn('method="connect"',branch)
        self.assertIn('waitEvent="storageConnect"',branch)
        self.assertNotIn('0.0.0.0',branch)
        self.assertNotIn('http',branch)
    def test_local_storage_connect_is_not_a_planner_skill(self):
        from commons_relay.skills import default_registry
        self.assertNotIn('storage.connect_local',{s['id'] for s in default_registry().describe()})

class StorageBootstrapSourceContracts(unittest.TestCase):
    def test_storage_profile_is_allowlisted_and_pins_loopback_api(self):
        text=(ROOT/'native/core/commons_relay_module.cpp').read_text()
        branch=text.split('action=="storage.init"',1)[1].split('action=="storage.start"',1)[0]
        self.assertIn('const QSet<QString> allowed={"mode","listenPort","bootstrapNodes"}',branch)
        self.assertIn('storageMode!="local" && storageMode!="official-testnet"',branch)
        self.assertIn('^spr:[A-Za-z0-9_-]{40,2000}$',branch)
        self.assertIn('{"api-bindaddr","127.0.0.1"}',branch)
        self.assertIn('{"listen-ip","127.0.0.1"}',branch)
        self.assertIn('cfg.insert("bootstrap-node",bootstraps)',branch)
    def test_local_mode_cannot_smuggle_public_bootstrap_records(self):
        text=(ROOT/'native/core/commons_relay_module.cpp').read_text()
        branch=text.split('action=="storage.init"',1)[1].split('action=="storage.start"',1)[0]
        self.assertIn('storageMode=="local" && !bootstraps.isEmpty()',branch)
        self.assertIn('LOCAL_STORAGE_BOOTSTRAP_DENIED',branch)

class RuntimeOwnershipContracts(unittest.TestCase):
    def test_native_identity_lease_outlives_a_stopped_python_worker(self):
        source=(ROOT/'native/core/commons_relay_module.cpp').read_text()
        header=(ROOT/'native/core/commons_relay_module.h').read_text()
        self.assertIn('std::unique_ptr<QLockFile> profileLease_',header)
        self.assertIn('lease->tryLock(0)',source)
        self.assertIn('PROFILE_IN_USE',source)
        stop=source.split('void CommonsRelayModule::stop()',1)[1].split('namespace {',1)[0]
        self.assertNotIn('profileLease_.reset',stop)
        self.assertNotIn('unlock',stop)
    def test_ui_ipc_acceptance_is_nonblocking_and_correlates_early_replies(self):
        source=(ROOT/'native/ui/src/commons_relay_ui_backend.cpp').read_text()
        self.assertIn('invokeRemoteMethodAsync',source)
        self.assertIn('earlyReplies_.take(id)',source)
        self.assertNotIn('invokeRemoteMethod(',source)
    def test_owner_inbox_uses_a_flat_payload_frame(self):
        source=(ROOT/'native/ui/src/commons_relay_ui_backend.cpp').read_text()
        self.assertIn('command("owner.inbox"',source)
        self.assertIn('message.value("payload_json")',source)
