from pathlib import Path
import hashlib
import os
import secrets
import shutil
import tempfile
import unittest
from commons_relay.file_crypto import Sodium
from commons_relay.vault import Vault,Peer
from commons_relay.codec import Rejected

class MemoryStore:
    """Explicit test-only store; not evidence of a Logos Storage deployment."""
    def __init__(self):self.files={};self.uploads=0;self.corrupt=False
    def upload(self,path,operation):
        data=path.read_bytes();address='z'+hashlib.sha256(data).hexdigest();self.files[address]=data;self.uploads+=1;return address
    def download(self,address,path,maximum):
        data=self.files[address]
        if self.corrupt:data=data[:-1]+bytes([data[-1]^1])
        if len(data)>maximum:raise ValueError('fixture size cap')
        path.write_bytes(data)

class VaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.crypto=Sodium(os.environ.get('COMMONS_RELAY_TEST_SODIUM'))
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.vaults=[];self.store=MemoryStore()
        self.alice=self.make('alice');self.bob=self.make('bob')
        (self.root/'alice-in'/'report.txt').write_text('private fixture content')
    def make(self,name):
        inputs=self.root/(name+'-in');outputs=self.root/(name+'-out');inputs.mkdir();outputs.mkdir()
        v=Vault(self.root/name,inputs,outputs,self.crypto,maximum_file=1024*1024,clock=lambda:1000);self.vaults.append(v);return v
    def tearDown(self):
        for v in self.vaults:v.close()
        self.temp.cleanup()
    def test_usage_is_an_aggregate_not_secret_or_network_capacity(self):
        self.assertEqual(self.alice.usage()['file_count'],0)
        self.upload()
        usage=self.alice.usage()
        self.assertEqual(usage['file_count'],1)
        self.assertEqual(usage['plaintext_bytes'],str(len('private fixture content'.encode())))
        self.assertGreater(int(usage['ciphertext_reference_bytes']),int(usage['plaintext_bytes']))
        self.assertEqual(set(usage),{'file_count','plaintext_bytes','ciphertext_reference_bytes'})

    def upload(self):
        self.alice.prepare_upload('task1','report.txt','Q3 report');return self.alice.upload('task1',self.store)
    def test_upload_download_roundtrip(self):
        result=self.upload();out=self.alice.download(result['address'],'returned.txt',self.store)
        self.assertEqual((self.root/'alice-out/returned.txt').read_text(),'private fixture content');self.assertTrue(out['authenticated'])
    def test_content_and_label_not_on_store(self):
        result=self.upload();data=self.store.files[result['address']]
        self.assertNotIn(b'private fixture content',data);self.assertNotIn(b'Q3 report',data)
    def test_repeating_completed_upload_does_not_resend(self):
        a=self.upload();b=self.alice.upload('task1',self.store);self.assertEqual(a,b);self.assertEqual(self.store.uploads,1)
    def test_prepare_replay_does_not_encrypt_new_blob(self):
        a=self.alice.prepare_upload('task1','report.txt','Q3 report');b=self.alice.prepare_upload('task1','report.txt','Q3 report')
        self.assertEqual(a,b);self.assertEqual(len(list(self.alice.blobs.iterdir())),1)
    def test_operation_id_cannot_change_label(self):
        self.upload()
        with self.assertRaises(Rejected):self.alice.prepare_upload('task1','report.txt','different')
    def test_list_never_returns_keys(self):
        self.upload();row=self.alice.list()[0];self.assertEqual(set(row),{'address','label','bytes','sender'})
    def test_share_to_other_identity(self):
        result=self.upload();envelope=self.alice.make_share(result['address'],self.bob.identity())
        self.bob.receive_share(envelope,self.alice.identity());self.bob.download(result['address'],'shared.txt',self.store)
        self.assertEqual((self.root/'bob-out/shared.txt').read_text(),'private fixture content')
    def test_share_replay_idempotent(self):
        result=self.upload();envelope=self.alice.make_share(result['address'],self.bob.identity());self.bob.receive_share(envelope,self.alice.identity());self.bob.receive_share(envelope,self.alice.identity())
        self.assertEqual(len(self.bob.list()),1)
    def test_wrong_recipient_rejected(self):
        result=self.upload();other=self.make('charlie');envelope=self.alice.make_share(result['address'],self.bob.identity())
        with self.assertRaises(Rejected):other.receive_share(envelope,self.alice.identity())
    def test_forged_sender_rejected(self):
        result=self.upload();envelope=self.alice.make_share(result['address'],self.bob.identity())
        with self.assertRaises(Rejected):self.bob.receive_share(envelope,self.bob.identity())
    def test_modified_envelope_rejected(self):
        result=self.upload();envelope=self.alice.make_share(result['address'],self.bob.identity());envelope['body']['expires_at']+=1
        with self.assertRaises(Rejected):self.bob.receive_share(envelope,self.alice.identity())
    def test_share_expires(self):
        result=self.upload();envelope=self.alice.make_share(result['address'],self.bob.identity());self.bob.clock=lambda:1400
        with self.assertRaises(Rejected):self.bob.receive_share(envelope,self.alice.identity())
    def test_corrupt_download_never_publishes_output(self):
        result=self.upload();self.store.corrupt=True
        with self.assertRaises(Rejected):self.alice.download(result['address'],'bad.txt',self.store)
        self.assertFalse((self.root/'alice-out/bad.txt').exists())
    def test_cannot_download_without_key(self):
        result=self.upload()
        with self.assertRaises(Rejected):self.bob.download(result['address'],'no.txt',self.store)
    def test_no_source_escape(self):
        with self.assertRaises(Rejected):self.alice.prepare_upload('task1','../bob/keys/identity.pem','no')
    def test_no_source_symlink(self):
        (self.root/'alice-in/link').symlink_to(self.root/'alice/keys/identity.pem')
        with self.assertRaises(Rejected):self.alice.prepare_upload('task1','link','no')
    def test_no_destination_escape(self):
        result=self.upload()
        with self.assertRaises(Rejected):self.alice.download(result['address'],'../bad',self.store)
    def test_changed_prepared_cipher_rejected(self):
        self.alice.prepare_upload('task1','report.txt','label');path=next(self.alice.blobs.iterdir());path.write_bytes(b'corrupted')
        with self.assertRaises(Rejected):self.alice.upload('task1',self.store)
    def test_catalogue_restart_keeps_file_keys(self):
        result=self.upload();self.alice.close();self.vaults.remove(self.alice)
        self.alice=Vault(self.root/'alice',self.root/'alice-in',self.root/'alice-out',self.crypto,maximum_file=1024*1024,clock=lambda:1000);self.vaults.append(self.alice)
        self.alice.download(result['address'],'after-restart',self.store);self.assertEqual((self.root/'alice-out/after-restart').read_text(),'private fixture content')
    def test_no_destination_overwrite(self):
        result=self.upload();(self.root/'alice-out/existing').write_text('keep')
        with self.assertRaises(Rejected):self.alice.download(result['address'],'existing',self.store)
        self.assertEqual((self.root/'alice-out/existing').read_text(),'keep')
if __name__=='__main__':unittest.main()
