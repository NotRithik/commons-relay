from pathlib import Path
import tempfile
import unittest
from commons_relay.codec import *
from commons_relay.signing import Ed25519,sign_envelope,verify_envelope

class CodecTests(unittest.TestCase):
    def test_amount_extremes(self):self.assertEqual(amount(str(2**128-1)),2**128-1);self.assertEqual(amount('0'),0)
    def test_reject_ambiguous_amounts(self):
        for x in [True,False,1,1.0,'01','-1','+2','1.0','1e3',' 1','',str(2**128)]:
            with self.subTest(x=x):
                with self.assertRaises(Rejected):amount(x)
    def test_positive_transfer_required(self):
        with self.assertRaises(Rejected):amount('0',positive=True)
    def test_canonical_key_order(self):self.assertEqual(canonical({'b':1,'a':2}),b'{"a":2,"b":1}')
    def test_float_rejected(self):
        with self.assertRaises(Rejected):canonical({'x':1.0})
    def test_duplicate_keys_rejected(self):
        with self.assertRaises(Rejected):parse(b'{"x":1,"x":2}')
    def test_nonfinite_rejected(self):
        for raw in [b'{"x":NaN}',b'{"x":Infinity}']:
            with self.assertRaises(Rejected):parse(raw)
    def test_deep_object_rejected(self):
        value=None
        for _ in range(20):value=[value]
        with self.assertRaises(Rejected):canonical(value)
    def test_large_message_rejected(self):
        with self.assertRaises(Rejected):parse(b'x'*65537)
    def test_canonical_base64(self):
        for data in [b'',b'a',b'ab',bytes(range(255))]:self.assertEqual(unb64(b64(data)),data)
    def test_padding_and_bad_alphabet_rejected(self):
        for raw in ['Zg==','Zg=','Z!','Zh','A']:
            with self.subTest(raw=raw):
                with self.assertRaises(Rejected):unb64(raw)
    def test_untrusted_identifiers(self):
        for x in [None,1,'hello\nworld','<script>']:
            with self.assertRaises(Rejected):identifier(x)

class SigningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.crypto=Ed25519(self.root);self.key=self.root/'owner.pem';self.public=self.crypto.generate(self.key)
    def tearDown(self):self.temp.cleanup()
    def test_real_ed25519_roundtrip(self):
        env=sign_envelope({'domain':'test','amount':'123'},self.key,self.crypto)
        self.assertEqual(verify_envelope(env,self.public,self.crypto),env['body'])
    def test_modified_amount_rejected(self):
        env=sign_envelope({'amount':'1'},self.key,self.crypto);env['body']['amount']='2'
        with self.assertRaises(Rejected):verify_envelope(env,self.public,self.crypto)
    def test_wrong_public_key_rejected(self):
        other=self.crypto.generate(self.root/'other.pem');env=sign_envelope({'x':1},self.key,self.crypto)
        with self.assertRaises(Rejected):verify_envelope(env,other,self.crypto)
    def test_overwrite_refused(self):
        original=self.key.read_bytes()
        with self.assertRaises(Rejected):self.crypto.generate(self.key)
        self.assertEqual(self.key.read_bytes(),original)
    def test_private_key_permissions_required(self):
        self.key.chmod(0o644)
        with self.assertRaises(Rejected):self.crypto.sign(self.key,b'no')
    def test_signature_wrong_length(self):self.assertFalse(self.crypto.verify(self.public,b'message',b'bad'))
    def test_symlink_private_key_refused(self):
        link=self.root/'link.pem';link.symlink_to(self.key)
        with self.assertRaises(Rejected):self.crypto.sign(link,b'message')
    def test_extra_envelope_fields_rejected(self):
        env=sign_envelope({'x':1},self.key,self.crypto);env['role']='owner'
        with self.assertRaises(Rejected):verify_envelope(env,self.public,self.crypto)
    def test_parent_path_boundary(self):
        with self.assertRaises(Rejected):self.crypto.generate(self.root.parent/'outside-not-created.pem')

if __name__=='__main__':unittest.main()
