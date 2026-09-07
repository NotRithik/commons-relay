from pathlib import Path
from dataclasses import asdict
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from commons_relay.codec import canonical,b64,Rejected
from commons_relay.signing import Ed25519,sign_envelope
from commons_relay.engine import Policy,REQUEST_DOMAIN
from commons_relay.service import Service,serve
ROOT=Path(__file__).resolve().parents[1]

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.owner=self.root/'owner';self.crypto=Ed25519(self.owner)
        self.key=self.owner/'signing.pem';self.public=self.crypto.generate(self.key)
        self.agent=self.root/'agent';self.agent.mkdir(mode=0o700)
        self.settings=self.agent/'settings.json';self.settings.write_bytes(canonical({'schema_version':1,'agent_id':'service-test','owner_public_key':b64(self.public),'policy':asdict(Policy()),'network':'testnet'}));self.settings.chmod(0o600)
    def tearDown(self):self.temp.cleanup()
    def exchange(self,requests):
        data=io.BytesIO(b''.join(canonical(r)+b'\n' for r in requests));out=io.BytesIO();serve(self.agent,data,out)
        return [json.loads(line) for line in out.getvalue().splitlines()]
    def test_real_process_status(self):
        env={k:v for k,v in os.environ.items() if k in ['PATH','TMPDIR','HOME']}
        p=subprocess.run([sys.executable,'-I',str(ROOT/'commons_relay_worker.py'),'--profile',str(self.agent)],input=b'{"id":"q1","method":"status","params":{}}\n',stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,timeout=10)
        self.assertEqual(p.returncode,0);reply=json.loads(p.stdout)
        self.assertTrue(reply['success']);self.assertEqual(reply['result']['agent_id'],'service-test');self.assertFalse(reply['result']['inference_enabled'])
        self.assertFalse((self.agent/'owner-signing.pem').exists())
    def test_skills_roundtrip(self):
        reply=self.exchange([{'id':'s','method':'skills','params':{}}])[0]
        self.assertTrue(reply['success']);self.assertGreater(len(reply['result']['skills']),10)
    def test_unknown_command_rejected_without_code_execution(self):
        reply=self.exchange([{'id':'bad','method':'exec','params':{'command':'anything'}}])[0]
        self.assertFalse(reply['success']);self.assertEqual(reply['error'],'UNSUPPORTED_SERVICE_METHOD')
    def test_malformed_command_does_not_break_next(self):
        replies=self.exchange([{'id':'bad','method':'exec','params':{}},{'id':'ok','method':'status','params':{}}])
        self.assertFalse(replies[0]['success']);self.assertTrue(replies[1]['success'])
    def test_signed_request_persists_after_worker_exit(self):
        import time
        envelope=sign_envelope({'domain':REQUEST_DOMAIN,'agent_id':'service-test','request_id':'r1','skill':'wallet.send','arguments':{'recipient':'fixture-recipient','amount':'150'},'expires_at':int(time.time())+100},self.key,self.crypto)
        reply=self.exchange([{'id':'a','method':'submit','params':{'envelope':envelope,'public_key':b64(self.public)}}])[0]
        self.assertEqual(reply['result']['state'],'input-required')
        latest=self.exchange([{'id':'b','method':'status','params':{}}])[0]
        self.assertEqual(latest['result']['tasks'][0]['id'],reply['result']['id'])
    def test_unsigned_request_rejected(self):
        reply=self.exchange([{'id':'a','method':'submit','params':{'envelope':{},'public_key':b64(self.public)}}])[0]
        self.assertFalse(reply['success'])
    def test_settings_require_private_permissions(self):
        self.settings.chmod(0o644)
        with self.assertRaises(Rejected):Service(self.agent)
    def test_mainnet_profile_rejected(self):
        value=json.loads(self.settings.read_text());value['network']='mainnet';self.settings.write_bytes(canonical(value))
        with self.assertRaises(Rejected):Service(self.agent)
    def test_no_owner_secret_in_status(self):
        output=self.exchange([{'id':'a','method':'status','params':{}}])[0]
        self.assertNotIn('PRIVATE KEY',json.dumps(output));self.assertNotIn(str(self.owner),json.dumps(output))
    def test_oversized_frame_closes_cleanly(self):
        out=io.BytesIO();serve(self.agent,io.BytesIO(b'x'*65537+b'\n'),out)
        self.assertEqual(json.loads(out.getvalue())['error'],'INVALID_MESSAGE_FRAME')
    def test_partial_frame_is_rejected(self):
        out=io.BytesIO();serve(self.agent,io.BytesIO(b'{}'),out)
        self.assertEqual(json.loads(out.getvalue())['error'],'INVALID_MESSAGE_FRAME')
    def test_no_unsigned_auto_execute_method(self):
        reply=self.exchange([{'id':'a','method':'execute','params':{'task_id':'x'}}])[0]
        self.assertEqual(reply['error'],'UNSUPPORTED_SERVICE_METHOD')
if __name__=='__main__':unittest.main()
