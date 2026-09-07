import io
import os
from pathlib import Path
import secrets
import tempfile
import unittest
from commons_relay.file_crypto import Sodium,CHUNK
from commons_relay.filesystem import FileRoot
from commons_relay.codec import Rejected

class FileCryptoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        library=os.environ.get('COMMONS_RELAY_TEST_SODIUM')
        cls.crypto=Sodium(library)
    def encrypt(self,data=b'synthetic test content',key=None):
        key=key or secrets.token_bytes(32);out=io.BytesIO()
        result=self.crypto.encrypt(io.BytesIO(data),out,{'label':'private label'},key)
        return key,out.getvalue(),result
    def test_roundtrip_multi_chunk(self):
        data=secrets.token_bytes(CHUNK*3+79);key,cipher,result=self.encrypt(data);plain=io.BytesIO();meta=self.crypto.decrypt(io.BytesIO(cipher),plain,key)
        self.assertEqual(plain.getvalue(),data);self.assertEqual(meta['metadata']['label'],'private label');self.assertEqual(result['plaintext_bytes'],len(data));self.assertNotIn(b'private label',cipher)
    def test_empty_file_roundtrip(self):
        key,cipher,_=self.encrypt(b'');plain=io.BytesIO();self.assertEqual(self.crypto.decrypt(io.BytesIO(cipher),plain,key)['plaintext_bytes'],0)
    def test_randomized_encryption(self):
        key=secrets.token_bytes(32);self.assertNotEqual(self.encrypt(key=key)[1],self.encrypt(key=key)[1])
    def test_tampering_rejected(self):
        key,cipher,_=self.encrypt();bad=bytearray(cipher);bad[-8]^=1
        with self.assertRaises(Rejected):self.crypto.decrypt(io.BytesIO(bad),io.BytesIO(),key)
    def test_wrong_key_rejected(self):
        _,cipher,_=self.encrypt()
        with self.assertRaises(Rejected):self.crypto.decrypt(io.BytesIO(cipher),io.BytesIO(),secrets.token_bytes(32))
    def test_truncation_at_every_tail_offset_rejected(self):
        key,cipher,_=self.encrypt()
        for n in [1,4,17,21,27]:
            with self.subTest(n=n):
                with self.assertRaises(Rejected):self.crypto.decrypt(io.BytesIO(cipher[:-n]),io.BytesIO(),key)
    def test_trailing_data_rejected(self):
        key,cipher,_=self.encrypt()
        with self.assertRaises(Rejected):self.crypto.decrypt(io.BytesIO(cipher+b'x'),io.BytesIO(),key)
    def test_encryption_size_limit(self):
        with self.assertRaises(Rejected):self.crypto.encrypt(io.BytesIO(b'x'*30),io.BytesIO(),{},secrets.token_bytes(32),maximum=29)
    def test_decryption_size_limit(self):
        key,cipher,_=self.encrypt(b'x'*30)
        with self.assertRaises(Rejected):self.crypto.decrypt(io.BytesIO(cipher),io.BytesIO(),key,maximum=29)
    def test_invalid_chunk_length_cannot_allocate_unbounded(self):
        key,cipher,_=self.encrypt();bad=cipher[:32]+b'\xff'*4+cipher[36:]
        with self.assertRaises(Rejected):self.crypto.decrypt(io.BytesIO(bad),io.BytesIO(),key)
    def test_key_sharing_uses_real_sealed_box(self):
        public,secret=self.crypto.box_keypair();message=b'private file key';sealed=self.crypto.seal(public,message)
        self.assertEqual(self.crypto.open_sealed(public,secret,sealed),message);self.assertNotIn(message,sealed)
    def test_wrong_recipient_cannot_open_share(self):
        public,secret=self.crypto.box_keypair();other,othersecret=self.crypto.box_keypair();sealed=self.crypto.seal(public,b'key')
        with self.assertRaises(Rejected):self.crypto.open_sealed(other,othersecret,sealed)
    def test_corrupted_share_rejected(self):
        public,secret=self.crypto.box_keypair();sealed=bytearray(self.crypto.seal(public,b'key'));sealed[-1]^=1
        with self.assertRaises(Rejected):self.crypto.open_sealed(public,secret,bytes(sealed))

class FileRootTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.files=FileRoot(self.root)
    def tearDown(self):self.temp.cleanup()
    def test_create_and_read(self):
        with self.files.create('doc') as f:f.write(b'hello')
        with self.files.read('doc',100) as f:self.assertEqual(f.read(),b'hello')
        self.assertEqual((self.root/'doc').stat().st_mode&0o777,0o600)
    def test_no_overwrite(self):
        (self.root/'doc').write_text('original')
        with self.assertRaises(Rejected):
            with self.files.create('doc') as f:f.write(b'bad')
        self.assertEqual((self.root/'doc').read_text(),'original')
    def test_failed_decrypt_does_not_publish_partial_file(self):
        with self.assertRaises(ValueError):
            with self.files.create('result') as f:f.write(b'partial');raise ValueError('simulated auth fail')
        self.assertFalse((self.root/'result').exists());self.assertEqual(list(self.root.iterdir()),[])
    def test_no_parent_or_absolute_paths(self):
        for name in ['../x','a/../b','/tmp/x','a//b','a/./b','']:
            with self.subTest(name=name):
                with self.assertRaises(Rejected):self.files.parts(name)
    def test_no_symlink_file(self):
        (self.root/'doc').write_text('secret');(self.root/'link').symlink_to(self.root/'doc')
        with self.assertRaises(Rejected):
            with self.files.read('link',100) as f:f.read()
    def test_no_symlink_ancestor(self):
        (self.root/'dir').mkdir();(self.root/'dir/file').write_text('secret');(self.root/'link').symlink_to(self.root/'dir')
        with self.assertRaises(Rejected):
            with self.files.read('link/file',100) as f:f.read()
    def test_late_destination_creation_is_not_overwritten(self):
        with self.assertRaises(Rejected):
            with self.files.create('doc') as f:
                f.write(b'new');(self.root/'doc').write_text('created concurrently')
        self.assertEqual((self.root/'doc').read_text(),'created concurrently')
if __name__=='__main__':unittest.main()
