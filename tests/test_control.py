from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from commons_relay.codec import Rejected
from commons_relay.control import CoreClient

class CoreControlTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.exe=self.root/'logosctl';self.exe.write_text('#!/bin/sh\nexit 0\n');self.exe.chmod(0o700)
        self.client=CoreClient(self.exe,self.root,environment={'PATH':'/usr/bin:/bin','OPENAI_API_KEY':'not-a-real-key','UNRELATED_TOKEN':'not-a-token'})
    def tearDown(self):self.temp.cleanup()
    def completed(self,result,status='ok',rc=0,module='commons_relay_module'):
        return subprocess.CompletedProcess([],rc,json.dumps({'status':status,'module':module,'result':result}).encode(),b'')
    def test_valid_invoke_result(self):
        with patch('commons_relay.control.subprocess.run',return_value=self.completed('accepted')):self.assertEqual(self.client.invoke('runtimeState'),'accepted')
    def test_zero_exit_error_not_success(self):
        with patch('commons_relay.control.subprocess.run',return_value=self.completed(None,'error')):
            with self.assertRaises(Rejected):self.client.invoke('runtimeState')
    def test_wrong_module_response_rejected(self):
        with patch('commons_relay.control.subprocess.run',return_value=self.completed('x',module='other')):
            with self.assertRaises(Rejected):self.client.invoke('runtimeState')
    def test_invalid_cli_json_rejected(self):
        with patch('commons_relay.control.subprocess.run',return_value=subprocess.CompletedProcess([],0,b'not-json',b'')):
            with self.assertRaises(Rejected):self.client.invoke('runtimeState')
    def test_arbitrary_module_method_forbidden(self):
        with self.assertRaises(Rejected):self.client.invoke('shell.exec','bad')
    def test_environment_excludes_provider_credentials(self):
        self.assertNotIn('OPENAI_API_KEY',self.client.env);self.assertNotIn('UNRELATED_TOKEN',self.client.env)
    def test_replies_must_match_request_id(self):
        rid='a'*36
        with patch.object(self.client,'invoke',side_effect=[rid,json.dumps({'id':'b'*36,'success':True,'result':1})]):
            with self.assertRaises(Rejected):self.client.request('status',{})
    def test_pending_then_success(self):
        rid='a'*36
        with patch.object(self.client,'invoke',side_effect=[rid,json.dumps({'id':rid,'pending':True}),json.dumps({'id':rid,'success':True,'result':{'n':3}})]),patch('commons_relay.control.time.sleep'):
            self.assertEqual(self.client.request('status',{}),{'n':3})
    def test_expired_slot_is_not_a_success(self):
        rid='a'*36
        with patch.object(self.client,'invoke',side_effect=[rid,json.dumps({'id':rid,'expired':True})]):
            with self.assertRaises(Rejected):self.client.request('status',{})
    def test_error_payload_does_not_echo_private_text(self):
        rid='a'*36
        with patch.object(self.client,'invoke',side_effect=[rid,json.dumps({'id':rid,'success':False,'error':'secret /Users/anything'})]):
            with self.assertRaisesRegex(Rejected,'^CORE_REQUEST_FAILED$'):self.client.request('status',{})
    def test_static_error_preserved(self):
        rid='a'*36
        with patch.object(self.client,'invoke',side_effect=[rid,json.dumps({'id':rid,'success':False,'error':'INVALID_SIGNATURE'})]):
            with self.assertRaisesRegex(Rejected,'^INVALID_SIGNATURE$'):self.client.request('status',{})
    def test_command_is_not_shell_interpolated(self):
        with patch('commons_relay.control.subprocess.run',return_value=self.completed('ok')) as runner:
            self.client.invoke('request','str:literal; echo no')
            args=runner.call_args.args[0];self.assertEqual(args[-1],'str:literal; echo no');self.assertNotIn('shell',runner.call_args.kwargs)
if __name__=='__main__':unittest.main()
