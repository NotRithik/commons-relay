import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from commons_relay.a2a_types import jcs,sign_card,verify_card,task_document,validate_card,PAYMENT_EXTENSION,BINDING_EXTENSION
from commons_relay.signing import Ed25519
from commons_relay.codec import Rejected,b64

class A2ATypesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.crypto=Ed25519(Path(self.tmp.name));self.key=Path(self.tmp.name)/'key.pem';self.public=self.crypto.generate(self.key)
        self.card={'name':'Example','description':'A service','version':'0.1.0','supportedInterfaces':[{'url':'logos://example','protocolBinding':'LOGOS-MESSAGING','protocolVersion':'1.0'}],'capabilities':{'streaming':True},'defaultInputModes':['application/json'],'defaultOutputModes':['application/json'],'skills':[]}
        self.card['capabilities']['extensions']=[{'uri':BINDING_EXTENSION,'params':{'address':'example','signingPublicKey':b64(self.public),'encryptionPublicKey':b64(bytes(32)),'discoveryTopic':'fixture'}}]
    def tearDown(self):self.tmp.cleanup()
    def test_detached_jws_roundtrip(self):
        card=sign_card(self.card,self.key,self.public,self.crypto);self.assertEqual(verify_card(card,self.public,self.crypto),self.card)
    def test_changed_card_rejected(self):
        card=sign_card(self.card,self.key,self.public,self.crypto);card['name']='changed'
        with self.assertRaises(Rejected):verify_card(card,self.public,self.crypto)
    def test_wrong_signer_rejected(self):
        card=sign_card(self.card,self.key,self.public,self.crypto);key=Path(self.tmp.name)/'other.pem';other=self.crypto.generate(key)
        with self.assertRaises(Rejected):verify_card(card,other,self.crypto)
    def test_jcs_utf16_sort_and_unicode_encoding(self):
        value={'\ufffd':'caf\u00e9','\U0001f600':'x','control':'\x7f\n'}
        expected='{"control":"\x7f\\n","\U0001f600":"x","\ufffd":"caf\u00e9"}'.encode()
        self.assertEqual(jcs(value),expected)
    def test_jcs_rejects_unsafe_numbers(self):
        for value in [2**53,1.5,float('nan')]:
            with self.assertRaises(Rejected):jcs({'x':value})
    def test_old_a2a_url_only_card_rejected(self):
        card=dict(self.card);card.pop('supportedInterfaces');card['url']='https://example.test'
        with self.assertRaises(Rejected):validate_card(card)
    def test_unsupported_binding_not_advertised(self):
        card=dict(self.card);card['supportedInterfaces']=[{'url':'http://x','protocolBinding':'FAKE','protocolVersion':'1.0'}]
        with self.assertRaises(Rejected):validate_card(card)
    def test_duplicate_skill_ids_rejected(self):
        card=dict(self.card);skill={'id':'x','name':'x','description':'x','tags':[]};card['skills']=[skill,skill]
        with self.assertRaises(Rejected):validate_card(card)
    def test_task_unknown_maps_to_working_not_invented_a2a_state(self):
        row={'id':'t','context_id':'c','state':'unknown','updated':1000,'sequence':2,'skill':'x'}
        self.assertEqual(task_document(row,1000)['status']['state'],'TASK_STATE_WORKING')
    def test_artifact_uses_v1_data_part(self):
        row={'id':'t','context_id':'c','state':'completed','updated':1000,'sequence':2,'skill':'x','result':'{"answer":42}'}
        doc=task_document(row,1000);self.assertEqual(doc['artifacts'][0]['parts'][0]['data'],{'answer':42});self.assertEqual(doc['status']['state'],'TASK_STATE_COMPLETED')
    def test_timestamp_is_rfc3339(self):
        row={'id':'t','context_id':'c','state':'input-required','updated':1000,'sequence':2,'skill':'x'}
        self.assertEqual(task_document(row,1000)['status']['timestamp'],'1970-01-01T00:16:40Z')
    def test_error_message_role_is_v1_enum(self):
        row={'id':'t','context_id':'c','state':'failed','updated':1000,'sequence':2,'skill':'x','error':'failure'}
        self.assertEqual(task_document(row,1000)['status']['message']['role'],'ROLE_AGENT')
if __name__=='__main__':unittest.main()
