import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('relay_packaging',ROOT/'scripts/package-lgx.py')
PACK=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(PACK)

class PackagingTests(unittest.TestCase):
    def test_only_current_code_and_required_library_are_packaged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);name='commons_relay_module'
            (root/(name+'_plugin.dylib')).write_bytes(b'EXPLICIT TEST FIXTURE')
            (root/'metadata.json').write_text(json.dumps({'name':name,'main':name+'_plugin','type':'core','version':'0.1.0'}))
            (root/'commons_relay_worker.py').write_text('# fixture')
            (root/'commons_relay').mkdir();(root/'commons_relay/__init__.py').write_text('')
            (root/'commons_relay/service.py').write_text('# fixture')
            (root/'owner-signing.pem').write_text('not a real key')
            (root/'commons_relay/state.sqlite').write_text('not a database')
            names,_=PACK.files_for_variant(root,'darwin-arm64',name)
            self.assertIn('commons_relay/service.py',names)
            self.assertNotIn('owner-signing.pem',names)
            self.assertNotIn('commons_relay/state.sqlite',names)
            (root/'commons_relay/service.py').unlink()
            (root/'commons_relay/service.py').symlink_to(root/'owner-signing.pem')
            with self.assertRaises(ValueError):PACK.files_for_variant(root,'darwin-arm64',name)
    def test_unknown_module_and_platform_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for variant,name in [('darwin-arm64','unknown'),('windows-x64','commons_relay_wallet')]:
                with self.assertRaises(ValueError):PACK.files_for_variant(Path(tmp),variant,name)

if __name__=='__main__':unittest.main()

class PackageAssemblyTests(unittest.TestCase):
    def test_file_copy_loop_does_not_replace_the_module_identity(self):
        import tarfile
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);native=root/'native';native.mkdir();name='commons_relay_wallet'
            library=name+'_plugin.dylib'
            (native/library).write_bytes(b'EXPLICIT TEST FIXTURE')
            (native/'metadata.json').write_text(json.dumps({'name':name,'main':name+'_plugin','type':'core','version':'0.1.0'}))
            checked=[]
            class FakeLibrary:
                def lgx_load(self,path):
                    with tarfile.open(path.decode(),'r:gz') as archive:
                        manifest=json.load(archive.extractfile('manifest.json'))
                    checked.append(manifest['name'])
                    self.asserted_icon=manifest['icon']
                    return 1
                def lgx_add_variant(self,handle,variant,payload,main):
                    checked.append(main.decode())
                    if not (Path(payload.decode())/main.decode()).is_file():raise AssertionError('Main library is missing')
                    return True
                def lgx_save(self,handle,path):Path(path.decode()).write_bytes(b'not a real LGX: assembly test');return True
                def lgx_free_package(self,handle):pass
            fake=SimpleNamespace(lib=FakeLibrary(),check=lambda result:self.assertTrue(result),verify=lambda path:{'valid':True,'errors':[],'warnings':[]})
            PACK.package(fake,native,'darwin-arm64',root/'test-only.lgx',name)
            self.assertEqual(checked,[name,library])
            self.assertEqual(fake.lib.asserted_icon,'')


class ChatUiPackagingTests(unittest.TestCase):
    def fixture(self,root,variant):
        name='commons_relay_owner_ui';ext='.dylib' if variant=='darwin-arm64' else '.so'
        files=[name+'_plugin'+ext,name+'_replica_factory'+ext,'qml/Main.qml','qml/ChatTranscript.qml','icons/commons_relay.svg','commons_relay_owner_ui.py','commons_relay/__init__.py','commons_relay/conversation_permissions.py','commons_relay/inference_settings.py']
        for filename in files:
            path=root/filename;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('explicit packaging fixture')
        (root/'metadata.json').write_text(json.dumps({'name':name,'main':name+'_plugin','type':'ui_qml','version':'0.1.0'}))
        return name
    def test_both_platform_packages_include_the_chat_component(self):
        for variant in ['darwin-arm64','linux-x86_64']:
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);name=self.fixture(root,variant)
                files,_=PACK.files_for_variant(root,variant,name)
                self.assertIn('qml/ChatTranscript.qml',files)
                self.assertIn('commons_relay/conversation_permissions.py',files)
                self.assertIn('commons_relay/inference_settings.py',files)
    def test_missing_chat_component_fails_instead_of_shipping_broken_ui(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);name=self.fixture(root,'darwin-arm64');(root/'qml/ChatTranscript.qml').unlink()
            with self.assertRaisesRegex(ValueError,'ChatTranscript'):PACK.files_for_variant(root,'darwin-arm64',name)
    def test_chat_component_cannot_be_a_link_outside_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);name=self.fixture(root,'darwin-arm64');(root/'qml/ChatTranscript.qml').unlink()
            (root/'qml/ChatTranscript.qml').symlink_to(root/'commons_relay_owner_ui.py')
            with self.assertRaises(ValueError):PACK.files_for_variant(root,'darwin-arm64',name)
