import os
from pathlib import Path
import tempfile
import unittest
from commons_relay.file_crypto import Sodium
from commons_relay.vault import Vault
from commons_relay.storage_adapter import StorageAdapter
from commons_relay.engine import Prepared
from commons_relay.codec import Rejected
from test_vault import MemoryStore

class StorageAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'in').mkdir();(self.root/'out').mkdir();(self.root/'in/doc').write_text('fixture document')
        self.vault=Vault(self.root/'vault',self.root/'in',self.root/'out',Sodium(os.environ.get('COMMONS_RELAY_TEST_SODIUM')))
        self.store=MemoryStore();self.adapter=StorageAdapter(self.vault,self.store)
    def tearDown(self):self.vault.close();self.temp.cleanup()
    def task(self,name='t1',skill='storage.upload',args=None):
        return {'id':name,'skill':skill,'maximum_spend':'0','arguments':args if args is not None else {'path':'doc','label':'private label'}}
    def test_upload_confirmed_only_after_store(self):
        effect=self.adapter.prepare(self.task());self.assertEqual(self.adapter.lookup(effect).state,'pending');self.adapter.broadcast(effect);r=self.adapter.lookup(effect);self.assertEqual(r.state,'confirmed');self.assertIn('address',r.result)
    def test_restart_reconciles_without_resend(self):
        effect=self.adapter.prepare(self.task());self.adapter.broadcast(effect);replacement=StorageAdapter(self.vault,self.store)
        self.assertEqual(replacement.lookup(effect).state,'confirmed');self.assertEqual(self.store.uploads,1)
    def test_no_second_effect_after_result(self):
        effect=self.adapter.prepare(self.task());self.adapter.broadcast(effect);self.adapter.broadcast(effect);self.assertEqual(self.store.uploads,1)
    def test_directory_escape_fails_during_preparation(self):
        with self.assertRaises(Rejected):self.adapter.prepare(self.task(args={'path':'../private','label':'x'}))
    def test_unconnected_skill_cannot_dispatch(self):
        with self.assertRaises(Rejected):self.adapter.prepare(self.task(skill='wallet.send'))
    def test_nonzero_payment_not_assumed(self):
        task=self.task();task['maximum_spend']='1'
        with self.assertRaises(Rejected):self.adapter.prepare(task)
    def test_list_no_upload(self):
        effect=self.adapter.prepare(self.task(skill='storage.list',args={}));self.adapter.broadcast(effect);self.assertEqual(self.adapter.lookup(effect).result,{'files':[]});self.assertEqual(self.store.uploads,0)
    def test_download_real_decrypt_via_store_interface(self):
        effect=self.adapter.prepare(self.task());self.adapter.broadcast(effect);cid=self.adapter.lookup(effect).result['address']
        download=self.adapter.prepare(self.task('t2','storage.download',{'address':cid,'path':'retrieved'}));self.adapter.broadcast(download)
        self.assertEqual(self.adapter.lookup(download).state,'confirmed');self.assertEqual((self.root/'out/retrieved').read_text(),'fixture document')
    def test_download_label_is_bound_to_exact_address_during_prepare(self):
        effect=self.adapter.prepare(self.task());self.adapter.broadcast(effect);cid=self.adapter.lookup(effect).result['address']
        download=self.adapter.prepare(self.task('t2','storage.download',{'address':'private label','path':'by-label'}))
        with self.vault.guard:
            row=self.vault.db.execute('SELECT args FROM effects WHERE task_id=?',('t2',)).fetchone()
        self.assertIn(cid,row['args']);self.assertNotIn('private label',row['args'])
        self.adapter.broadcast(download)
        self.assertEqual((self.root/'out/by-label').read_text(),'fixture document')
    def test_upload_commit_reconciled_when_effect_receipt_lost(self):
        effect=self.adapter.prepare(self.task());self.vault.upload(effect.opaque_handle,self.store)
        self.assertEqual(self.adapter.lookup(effect).state,'confirmed');self.assertEqual(self.store.uploads,1)
if __name__=='__main__':unittest.main()
