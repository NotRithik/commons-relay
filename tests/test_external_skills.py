import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from commons_relay.external_skills import ExternalSkills,ExternalAdapter
from commons_relay.skills import default_registry
from commons_relay.engine import Engine
from commons_relay.signing import Ed25519
from commons_relay.codec import Rejected
ROOT=Path(__file__).resolve().parents[1]

SCRIPT='''#!/usr/bin/env python3
import json,sys
r=json.loads(sys.stdin.buffer.read())
if r['phase']=='prepare':print(json.dumps({'effect_id':'fx-'+r['task_id']}))
elif r['phase']=='execute':print(json.dumps({'state':'confirmed','result':{'count':len(r['arguments']['text'])}}))
elif r['phase']=='lookup':print(json.dumps({'state':'confirmed','result':{'count':len(r['arguments']['text'])}}))
else:raise SystemExit(2)
'''
class ExternalSkillTests(unittest.TestCase):
    def setUp(self):
        tmp_root=ROOT/'tests/.tmp';tmp_root.mkdir(exist_ok=True);self.tmp=tempfile.TemporaryDirectory(dir=tmp_root);self.base=Path(self.tmp.name);self.profile=self.base/'profile';self.profile.mkdir(mode=0o700);self.root=self.base/'extensions';self.root.mkdir(mode=0o700)
        self.exe=self.root/'text-count';self.exe.write_text(SCRIPT);self.exe.chmod(0o700);digest=hashlib.sha256(self.exe.read_bytes()).hexdigest()
        schema={'type':'object','properties':{'text':{'type':'string','maxLength':1000}},'required':['text'],'additionalProperties':False}
        manifest={'schema':1,'id':'example.text_count','description':'Count characters in text','executable':'text-count','executable_sha256':digest,'timeout_seconds':3,'input_schema':schema,'public':False}
        (self.root/'text-count.json').write_text(json.dumps(manifest));(self.profile/'extensions.json').write_text(json.dumps({'manifests':['text-count.json']}));(self.profile/'extensions.json').chmod(0o600)
        self.previous=os.environ.get('COMMONS_RELAY_EXTENSION_ROOT');os.environ['COMMONS_RELAY_EXTENSION_ROOT']=str(self.root)
        self.crypto=Ed25519(self.base/'crypto');self.key=self.crypto.scratch/'owner.pem';self.public=self.crypto.generate(self.key)
        self.extensions=ExternalSkills(self.profile);registry=default_registry().extended(self.extensions.skills());self.engine=Engine(self.base/'ledger','agent',self.public,self.crypto,registry=registry)
    def tearDown(self):
        self.engine.close()
        if self.previous is None:os.environ.pop('COMMONS_RELAY_EXTENSION_ROOT',None)
        else:os.environ['COMMONS_RELAY_EXTENSION_ROOT']=self.previous
        self.tmp.cleanup()
    def task(self):
        skill=self.engine.registry.get('example.text_count');quote=skill.validate({'text':'abcd'})
        return {'id':'task1','skill':skill.name,'arguments':{'text':'abcd'},'maximum_spend':str(quote.maximum)}
    def test_manifest_extends_registry_without_core_source_edit(self):self.assertEqual(self.engine.registry.get('example.text_count').description,'Count characters in text')
    def test_extension_is_zero_spend(self):self.assertEqual(self.engine.registry.get('example.text_count').validate({'text':'x'}).maximum,0)
    def test_prepare_execute_lookup_roundtrip(self):
        adapter=ExternalAdapter(self.profile,self.extensions,self.engine);effect=adapter.prepare(self.task());adapter.broadcast(effect);receipt=adapter.lookup(effect);self.assertEqual(receipt.state,'confirmed');self.assertEqual(receipt.result,{'count':4})
    def test_environment_does_not_inherit_api_key(self):
        # Contract check: adapter builds an explicit allowlist rather than copying os.environ.
        import inspect,commons_relay.external_skills as m
        source=inspect.getsource(m.ExternalAdapter._invoke);self.assertIn("env={'PATH':'/opt/homebrew/bin:/usr/bin:/bin'",source);self.assertNotIn('os.environ.copy',source)
    def test_hash_change_rejected(self):
        self.engine.close();self.exe.write_text(SCRIPT+'# changed\n')
        with self.assertRaises(Rejected):ExternalSkills(self.profile)
        # recreate a harmless engine so tearDown remains valid
        self.engine=Engine(self.base/'ledger2','agent',self.public,self.crypto)
    def test_reserved_core_namespace_rejected(self):
        self.engine.close();m=json.loads((self.root/'text-count.json').read_text());m['id']='wallet.steal';(self.root/'text-count.json').write_text(json.dumps(m));m['executable_sha256']=hashlib.sha256(self.exe.read_bytes()).hexdigest();(self.root/'text-count.json').write_text(json.dumps(m))
        with self.assertRaises(Rejected):ExternalSkills(self.profile)
        self.engine=Engine(self.base/'ledger2','agent',self.public,self.crypto)
    def test_symlink_executable_rejected(self):
        self.engine.close();real=self.root/'real';self.exe.rename(real);self.exe.symlink_to(real)
        with self.assertRaises(Rejected):ExternalSkills(self.profile)
        self.engine=Engine(self.base/'ledger2','agent',self.public,self.crypto)
if __name__=='__main__':unittest.main()
