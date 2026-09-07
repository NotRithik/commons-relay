import json
import os
import queue
import threading
import time
import unittest
from commons_relay.bridge import Wire
from commons_relay.codec import Rejected,canonical

class BridgeTests(unittest.TestCase):
    def setUp(self):
        r,w=os.pipe();r2,w2=os.pipe()
        self.source=os.fdopen(r,'rb');self.host_send=os.fdopen(w,'wb',buffering=0)
        self.host_read=os.fdopen(r2,'rb');self.sink=os.fdopen(w2,'wb',buffering=0)
        self.wire=Wire(self.source,self.sink);self.wire.start();self.running=[]
    def tearDown(self):
        self.host_send.close();self.wire.thread.join(timeout=2)
        for t in self.running:t.join(timeout=2)
        self.source.close();self.sink.close();self.host_read.close()
    def call_async(self,action='modules.probe',timeout=2):
        answer=queue.Queue()
        def run():
            try:answer.put(('ok',self.wire.call(action,{},timeout)))
            except Exception as error:answer.put(('error',str(error)))
        thread=threading.Thread(target=run);thread.start();self.running.append(thread)
        request=json.loads(self.host_read.readline());return request,answer
    def send(self,value):self.host_send.write(canonical(value)+b'\n')
    def test_roundtrip_correlated(self):
        request,result=self.call_async();self.send({'kind':'bridge_response','id':request['id'],'success':True,'result':{'version':'fixture'}})
        self.assertEqual(result.get(timeout=3),('ok',{'version':'fixture'}))
    def test_unknown_reply_does_not_satisfy_request(self):
        request,result=self.call_async();self.send({'kind':'bridge_response','id':'other','success':True,'result':'wrong'})
        self.send({'kind':'bridge_response','id':request['id'],'success':True,'result':'right'})
        self.assertEqual(result.get(timeout=3),('ok','right'))
    def test_duplicate_reply_does_not_break_reader(self):
        request,result=self.call_async();reply={'kind':'bridge_response','id':request['id'],'success':True,'result':7}
        self.send(reply);self.send(reply);self.assertEqual(result.get(timeout=3),('ok',7))
        self.send({'id':'ui','method':'status','params':{}});self.assertEqual(self.wire.next_command()['id'],'ui')
    def test_static_error_only(self):
        request,result=self.call_async();self.send({'kind':'bridge_response','id':request['id'],'success':False,'error':'secret private details /Users/somebody'})
        self.assertEqual(result.get(timeout=3),('error','CORE_OPERATION_FAILED'))
    def test_valid_error_preserved(self):
        request,result=self.call_async();self.send({'kind':'bridge_response','id':request['id'],'success':False,'error':'MODULE_BUSY'})
        self.assertEqual(result.get(timeout=3),('error','MODULE_BUSY'))
    def test_timeout_cleans_pending(self):
        request,result=self.call_async(timeout=.05);self.assertEqual(result.get(timeout=3),('error','CORE_OPERATION_TIMEOUT'));self.assertEqual(self.wire.pending,{})
    def test_queued_ui_request_during_bridge(self):
        request,result=self.call_async();self.send({'id':'ui','method':'status','params':{}});self.send({'kind':'bridge_response','id':request['id'],'success':True,'result':1})
        self.assertEqual(result.get(timeout=3),('ok',1));self.assertEqual(self.wire.next_command()['method'],'status')
    def test_forbidden_target_never_written(self):
        with self.assertRaises(Rejected):self.wire.call('shell.exec',{'command':'anything'},.1)
        self.assertEqual(self.wire.pending,{})
    def test_corrupt_response_closes_link(self):
        request,result=self.call_async();self.host_send.write(b'not-json\n');self.assertEqual(result.get(timeout=3),('error','CORE_LINK_CLOSED'))
    def test_out_of_order_responses(self):
        a,qa=self.call_async();b,qb=self.call_async()
        self.send({'kind':'bridge_response','id':b['id'],'success':True,'result':2});self.send({'kind':'bridge_response','id':a['id'],'success':True,'result':1})
        self.assertEqual(qa.get(timeout=3),('ok',1));self.assertEqual(qb.get(timeout=3),('ok',2))
if __name__=='__main__':unittest.main()
