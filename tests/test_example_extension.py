"""The shipped example is a real subprocess, not a fixed response in the UI."""
import hashlib
import json
from pathlib import Path
import subprocess
import unittest
ROOT=Path(__file__).resolve().parents[1]
EXECUTABLE=ROOT/'examples/extensions/text-statistics'

class ExampleExtensionTests(unittest.TestCase):
    def run_phase(self,phase,text,effect=None):
        request={'protocol':'commons-relay-extension/v1','phase':phase,'task_id':'example-test','arguments':{'text':text}}
        if effect is not None:request['effect_id']=effect
        run=subprocess.run([str(EXECUTABLE)],input=json.dumps(request),capture_output=True,text=True,timeout=5)
        return run, json.loads(run.stdout) if run.stdout else None
    def test_manifest_matches_the_exact_executable(self):
        manifest=json.loads((EXECUTABLE.parent/'text-statistics.json').read_text())
        self.assertEqual(manifest['executable_sha256'],hashlib.sha256(EXECUTABLE.read_bytes()).hexdigest())
    def test_actual_unicode_input_changes_the_result(self):
        text='Hello Logos\nworld'
        run,prepared=self.run_phase('prepare',text)
        self.assertEqual(run.returncode,0)
        run,result=self.run_phase('execute',text,prepared['effect_id'])
        self.assertEqual(run.returncode,0)
        self.assertEqual(result,{'state':'confirmed','result':{'characters':17,'words':3,'lines':2,'utf8_bytes':17}})
        _,replayed=self.run_phase('lookup',text,prepared['effect_id'])
        self.assertEqual(result,replayed)
    def test_changed_text_cannot_reuse_an_effect(self):
        _,prepared=self.run_phase('prepare','First')
        run,_=self.run_phase('execute','Changed',prepared['effect_id'])
        self.assertEqual(run.returncode,2)
        self.assertEqual(run.stdout,'')

if __name__=='__main__':unittest.main()
