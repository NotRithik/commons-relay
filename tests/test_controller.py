import json
from types import SimpleNamespace
import unittest
import test_engine as fixtures
from commons_relay.controller import Controller,OWNER_DOMAIN
from commons_relay.signing import sign_envelope
from commons_relay.codec import Rejected

class MailboxFixture:
    def __init__(self):self.out={};self.incoming=[];self.clock=lambda:10000
    def get_contact(self,address):
        if address!='owner-chat':raise Rejected('UNKNOWN_CONTACT')
        return address
    def enqueue(self,recipient,kind,payload,message_id,ttl):
        if message_id in self.out and self.out[message_id]['payload']!=payload:raise Rejected('DUPLICATE_CHANGED')
        self.out[message_id]={'recipient':recipient,'kind':kind,'payload':payload,'state':'queued'}
    def status(self,id):return self.out[id]
    def messages(self,cursor,limit):return [x for x in self.incoming if x['cursor']>cursor][:limit]

class ControllerTests(unittest.TestCase):
    setUp=fixtures.EngineTests.setUp
    request=fixtures.EngineTests.request
    def init(self):
        self.box=MailboxFixture();runtime=SimpleNamespace(mailbox=self.box,start=lambda:None)
        def handle(r):
            if r['method']=='status':return {'agent':'fixture-agent'}
            if r['method']=='submit':return self.engine.submit(r['params']['envelope'],self.owner)
            if r['method']=='approve':return self.engine.approve(r['params']['envelope'])
            raise Rejected('METHOD_NOT_IN_TEST_FIXTURE')
        self.service=SimpleNamespace(engine=self.engine,get_messaging=lambda:runtime,handle=handle,adapter_for=lambda _:fixtures.FixtureAdapter())
        self.controller=Controller(self.service,'owner-chat');return self.controller
    def tearDown(self):
        if hasattr(self,'controller'):self.controller.stop()
        fixtures.EngineTests.tearDown(self)
    def command(self,command=None,key=None,request_id='owner-1',sender='owner-chat',expiry=None):
        body={'domain':OWNER_DOMAIN,'agent_id':'fixture-agent','request_id':request_id,'command':command or {'method':'status','params':{}},'expires_at':expiry or self.clock.value+100}
        return {'kind':'owner-command','sender':sender,'payload':sign_envelope(body,key or self.key,self.crypto)}
    def test_owner_command_dual_channel_and_signature_validation(self):
        c=self.init();result=c.handle_owner(self.command());self.assertTrue(result['success']);self.assertIn('owner-owner-1',self.box.out)
    def test_wrong_chat_sender_rejected_even_with_valid_owner_signature(self):
        c=self.init()
        with self.assertRaises(Rejected):c.handle_owner(self.command(sender='untrusted'))
    def test_wrong_signing_key_rejected_even_on_owner_channel(self):
        c=self.init()
        with self.assertRaises(Rejected):c.handle_owner(self.command(key=self.other_key))
    def test_expired_command_rejected(self):
        c=self.init()
        with self.assertRaises(Rejected):c.handle_owner(self.command(expiry=self.clock.value-1))
    def test_unlisted_owner_method_cannot_execute_shell(self):
        c=self.init()
        with self.assertRaises(Rejected):c.handle_owner(self.command({'method':'shell','params':{}}))
    def test_signed_submission_schedules_without_bypassing_engine(self):
        c=self.init();request=self.request('50');result=c.handle_owner(self.command({'method':'submit','params':{'envelope':request}}));id=result['result']['id']
        self.assertEqual(result['result']['state'],'submitted');self.assertIsNotNone(self.engine.db.execute('SELECT 1 FROM scheduled_tasks WHERE task_id=?',(id,)).fetchone())
    def test_above_limit_not_scheduled_before_approval(self):
        c=self.init();request=self.request('101');result=c.handle_owner(self.command({'method':'submit','params':{'envelope':request}}))
        self.assertEqual(result['result']['state'],'input-required');self.assertEqual(self.engine.db.execute('SELECT COUNT(*) FROM scheduled_tasks').fetchone()[0],0)
    def test_replay_returns_same_task(self):
        c=self.init();message=self.command({'method':'submit','params':{'envelope':self.request()}});a=c.handle_owner(message);b=c.handle_owner(message)
        self.assertEqual(a,b);self.assertEqual(self.engine.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0],1)
    def test_same_command_id_different_intent_rejected(self):
        c=self.init();c.handle_owner(self.command())
        with self.assertRaises(Rejected):c.handle_owner(self.command({'method':'skills','params':{}}))
    def test_owner_notice_delivered_only_after_encrypted_ack(self):
        c=self.init();self.engine.submit(self.request('101'));c.notices();self.assertEqual(len(self.box.out),1)
        notice=next(iter(self.box.out));self.assertEqual(self.engine.db.execute('SELECT delivered FROM notifications').fetchone()[0],0)
        self.box.out[notice]['state']='acknowledged';self.clock.value+=4
        # Preserve acknowledged state when same id is retried in this test fixture.
        original=self.box.enqueue
        self.box.enqueue=lambda *args,**kwargs:None
        c.notices();self.assertEqual(self.engine.db.execute('SELECT delivered FROM notifications').fetchone()[0],1)
    def test_durable_inbox_cursor_skips_processed_owner_message(self):
        c=self.init();message={**self.command(),'cursor':7};self.box.incoming=[message];c.process_inbox();c.process_inbox();self.assertEqual(self.engine.db.execute('SELECT COUNT(*) FROM owner_commands').fetchone()[0],1)
    def test_failed_signature_is_recorded_as_rejected_not_retried_forever(self):
        c=self.init();self.box.incoming=[{**self.command(key=self.other_key),'cursor':8}];c.process_inbox();self.assertIsNotNone(c.last_error);self.assertEqual(self.engine.db.execute('SELECT value FROM control_cursor').fetchone()[0],8)
if __name__=='__main__':unittest.main()
