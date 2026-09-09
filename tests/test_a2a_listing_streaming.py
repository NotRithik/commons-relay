"""No network/model calls: official-shaped operations on real local journals."""
from pathlib import Path
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch
from a2a_fixtures import ProtocolFixture
from commons_relay.a2a_listing import timestamp_lower_bound
from commons_relay.a2a_types import BINDING_EXTENSION
from commons_relay.codec import Rejected
from test_a2a_protocol import ReadFixture

class ListingStreamingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        self.provider=ProtocolFixture(root,'provider',price='0');self.client=ProtocolFixture(root,'client');self.other=ProtocolFixture(root,'other')
        for a in [self.provider,self.client,self.other]:
            for b in [self.provider,self.client,self.other]:
                if a is not b:a.link(b)
        self.p=self.provider.protocol;self.counter=0
    def tearDown(self):
        for item in [self.provider,self.client,self.other]:item.close()
        self.temp.cleanup()
    def rpc(self,method,params=None,peer='client',rid=None):
        self.counter+=1
        return self.p.handle_request(peer,{'jsonrpc':'2.0','id':rid or 'rpc-'+str(self.counter),'method':method,'params':params or {}})
    def create(self,name,peer='client',context=None):
        params=self.provider.params(message_id=name)
        if context:params['message']['contextId']=context
        return self.p.send_message(peer,params)['task']
    def ack_initial(self,rid,peer='client'):
        ident='rpc-result-'+hashlib.sha256((peer+'\0'+rid).encode()).hexdigest()
        with self.provider.mailbox.tx() as db:db.execute("UPDATE outbox SET state='acknowledged' WHERE id=?",(ident,))
    def event_count(self):
        return self.provider.mailbox.db.execute("SELECT count(*) FROM outbox WHERE kind='a2a-event'").fetchone()[0]
    def finish(self,task):
        row=self.p._row(task['id']);self.provider.engine.execute(row['local_task'],ReadFixture());self.p.tick()
    def test_lists_only_callers_tasks_and_scoped_total(self):
        own=self.create('one');self.create('other','other')
        result=self.rpc('ListTasks')['result']
        self.assertEqual([v['id'] for v in result['tasks']],[own['id']]);self.assertEqual(result['totalSize'],1)
        self.assertEqual(result['nextPageToken'],'');self.assertEqual(result['pageSize'],50)
    def test_unknown_peer_is_denied_before_task_query(self):
        from commons_relay.a2a_listing import list_tasks
        with patch.object(self.p.engine,'tx',side_effect=AssertionError('No task query for unknown contact')):
            with self.assertRaisesRegex(Rejected,'UNKNOWN_MESSAGE_CONTACT'):list_tasks(self.p,'unknown',{})
    def test_empty_list_omits_no_required_fields(self):
        self.assertEqual(self.rpc('ListTasks')['result'],{'tasks':[],'nextPageToken':'','pageSize':50,'totalSize':0})
    def test_descending_cursor_pages_are_disjoint(self):
        tasks=[]
        for i in range(5):
            self.provider.now+=1;tasks.append(self.create('m'+str(i)))
        first=self.rpc('ListTasks',{'pageSize':2})['result']
        second=self.rpc('ListTasks',{'pageSize':2,'pageToken':first['nextPageToken']})['result']
        third=self.rpc('ListTasks',{'pageSize':2,'pageToken':second['nextPageToken']})['result']
        self.assertEqual([x['id'] for page in [first,second,third] for x in page['tasks']],[x['id'] for x in reversed(tasks)])
        self.assertEqual(third['nextPageToken'],'');self.assertEqual(first['totalSize'],5)
    def test_equal_timestamps_use_stable_id_tiebreaker(self):
        ids=sorted([self.create('m'+str(i))['id'] for i in range(4)],reverse=True)
        first=self.rpc('ListTasks',{'pageSize':2})['result']
        last=self.rpc('ListTasks',{'pageSize':2,'pageToken':first['nextPageToken']})['result']
        self.assertEqual([x['id'] for x in first['tasks']+last['tasks']],ids)
    def test_page_token_cannot_be_tampered_or_reused_by_another_peer(self):
        for i in range(3):self.create('m'+str(i))
        token=self.rpc('ListTasks',{'pageSize':1})['result']['nextPageToken']
        for changed,peer in [(token[:-3]+'abc','client'),(token,'other')]:
            self.assertEqual(self.rpc('ListTasks',{'pageSize':1,'pageToken':changed},peer=peer)['error']['code'],-32602)
    def test_page_token_is_bound_to_filters_and_survives_protocol_restart(self):
        self.create('one',context='ctx');self.create('two',context='ctx')
        token=self.rpc('ListTasks',{'contextId':'ctx','pageSize':1})['result']['nextPageToken']
        self.assertEqual(self.rpc('ListTasks',{'pageSize':1,'pageToken':token})['error']['code'],-32602)
        self.p.close();self.provider.protocol=self.p=type(self.p)(self.provider.service)
        next_page=self.rpc('ListTasks',{'contextId':'ctx','pageSize':1,'pageToken':token})['result']
        self.assertEqual(len(next_page['tasks']),1)
    def test_context_status_and_timestamp_filters_combine(self):
        self.provider.now=10000;older=self.create('older',context='ctx');self.finish(older)
        self.provider.now=10002;newer=self.create('newer',context='ctx');self.create('other',context='different')
        result=self.rpc('ListTasks',{'contextId':'ctx','status':'TASK_STATE_WORKING','statusTimestampAfter':'1970-01-01T02:46:40.000000001Z'})['result']
        self.assertEqual([x['id'] for x in result['tasks']],[newer['id']])
    def test_artifacts_omitted_by_default_and_included_on_request(self):
        task=self.create('one');self.finish(task)
        self.assertNotIn('artifacts',self.rpc('ListTasks')['result']['tasks'][0])
        result=self.rpc('ListTasks',{'includeArtifacts':True,'historyLength':0})['result']['tasks'][0]
        self.assertEqual(result['artifacts'][0]['parts'][0]['data'],{'value':42});self.assertNotIn('history',result)
    def test_task_without_artifacts_has_empty_array_only_when_requested(self):
        self.create('one');self.assertEqual(self.rpc('ListTasks',{'includeArtifacts':True})['result']['tasks'][0]['artifacts'],[])
    def test_invalid_listing_params_are_errors_not_ignored(self):
        for params in [{'pageSize':0},{'pageSize':101},{'pageSize':True},{'historyLength':-1},{'historyLength':True},{'includeArtifacts':'true'},{'status':[]},{'contextId':[]},{'pageToken':None},{'tenant':'other'},{'statusTimestampAfter':'yesterday'},{'unknown':'x'}]:
            with self.subTest(params=params):self.assertEqual(self.rpc('ListTasks',params)['error']['code'],-32602)
    def test_high_precision_timestamp_rounding_is_not_float_based(self):
        self.assertEqual(timestamp_lower_bound('1970-01-01T00:00:00.000000001Z'),1)
        self.assertEqual(timestamp_lower_bound('1970-01-01T05:30:00+05:30'),0)
        self.assertEqual(timestamp_lower_bound('1970-01-01T00:00:00.000000000Z'),0)
    def test_streaming_send_returns_initial_task_immediately(self):
        params=self.provider.params(message_id='stream');params['configuration']['returnImmediately']=False
        response=self.rpc('SendStreamingMessage',params,rid='stream-rpc')
        self.assertEqual(response['result']['task']['status']['state'],'TASK_STATE_WORKING')
        self.assertEqual(len(self.provider.scheduled),1)
        row=self.provider.engine.db.execute('SELECT rpc_id,last_sequence FROM a2a_subscriptions').fetchone()
        self.assertEqual(row['rpc_id'],'stream-rpc');self.assertEqual(row['last_sequence'],2)
    def test_streaming_retry_creates_no_second_task_or_schedule(self):
        params=self.provider.params(message_id='stream');a=self.rpc('SendStreamingMessage',params,rid='stream-rpc');b=self.rpc('SendStreamingMessage',params,rid='stream-rpc')
        self.assertEqual(a,b);self.assertEqual(len(self.provider.scheduled),1)
    def test_stream_waits_for_initial_task_ack_then_sends_terminal_updates(self):
        response=self.rpc('SendStreamingMessage',self.provider.params(message_id='stream'),rid='stream-rpc')
        self.finish(response['result']['task']);self.assertEqual(self.event_count(),0)
        self.ack_initial('stream-rpc');self.p.tick();self.assertEqual(self.event_count(),2)
        self.assertEqual(self.provider.engine.db.execute('SELECT count(*) FROM a2a_subscriptions').fetchone()[0],0)
        self.p.tick();self.assertEqual(self.event_count(),2)
    def test_stream_client_requires_exact_task_and_context(self):
        protocol=self.client.protocol
        protocol.request('provider','SubscribeToTask',{'id':'task-one'},id='watch')
        initial={'id':'task-one','contextId':'context-one','status':{'state':'TASK_STATE_WORKING'},'metadata':{BINDING_EXTENSION:{'sequence':'1'}}}
        protocol.remember_task('provider',initial)
        for task,context in [('other-task','context-one'),('task-one','other-context')]:
            payload={'requestId':'watch','sequence':2,'response':{'statusUpdate':{'taskId':task,'contextId':context,'status':{'state':'TASK_STATE_COMPLETED'}}}}
            with self.assertRaises(Rejected):protocol.handle_message({'kind':'a2a-event','sender':'provider','payload':payload})
        self.assertEqual(protocol.remote_task('provider','task-one')['status']['state'],'TASK_STATE_WORKING')
    def test_unsupported_push_routes_return_explicit_capability_error(self):
        for method in ['CreateTaskPushNotificationConfig','GetTaskPushNotificationConfig','ListTaskPushNotificationConfigs','DeleteTaskPushNotificationConfig']:
            self.assertEqual(self.rpc(method,{'taskId':'unknown'})['error']['code'],-32003)
    def test_get_and_send_reject_invalid_history_and_output_modes(self):
        task=self.create('one')
        self.assertEqual(self.rpc('GetTask',{'id':task['id'],'historyLength':-1})['error']['code'],-32602)
        params=self.provider.params(message_id='bad');params['configuration']['acceptedOutputModes']=['image/png']
        self.assertEqual(self.rpc('SendMessage',params)['error']['code'],-32005)

    def pump_encrypted(self, rounds=5):
        from commons_relay.messaging import topic
        peers={'provider':self.provider,'client':self.client,'other':self.other}
        if not hasattr(self,'cursors'):self.cursors={name:0 for name in peers}
        for _ in range(rounds):
            for sender in peers.values():
                for outgoing in sender.mailbox.outgoing(limit=20):
                    receiver=peers[outgoing['recipient']]
                    receiver.mailbox.receive(outgoing['wire'],topic(receiver.mailbox.address))
                    sender.mailbox.mark_sent(outgoing['id'],'fixture-transport')
            for name,receiver in peers.items():
                for message in receiver.mailbox.messages(self.cursors[name],limit=100):
                    receiver.protocol.handle_message(message)
                    self.cursors[name]=message['cursor']
                receiver.protocol.tick()
    def test_signed_encrypted_stream_roundtrip_delivers_initial_task_then_final_artifact(self):
        client=self.client.protocol;rid=client.request('provider','SendStreamingMessage',self.provider.params(message_id='encrypted-stream'))
        self.pump_encrypted()
        initial=client.response(rid)['result']['task'];self.assertEqual(initial['status']['state'],'TASK_STATE_WORKING')
        self.assertEqual(self.event_count(),0)
        self.finish(initial);self.pump_encrypted()
        final=client.remote_task('provider',initial['id'])
        self.assertEqual(final['status']['state'],'TASK_STATE_COMPLETED')
        self.assertEqual(final['artifacts'][0]['parts'][0]['data'],{'value':42})
        messages=self.client.mailbox.messages()
        response_cursor=next(m['cursor'] for m in messages if m['kind']=='a2a-response' and m['payload']['id']==rid)
        event_cursors=[m['cursor'] for m in messages if m['kind']=='a2a-event' and m['payload']['requestId']==rid]
        self.assertEqual(len(event_cursors),2);self.assertTrue(all(c>response_cursor for c in event_cursors))
        self.assertEqual(self.provider.engine.db.execute('SELECT count(*) FROM a2a_subscriptions').fetchone()[0],0)
        self.assertEqual(len(self.provider.scheduled),1)
    def test_list_response_stays_bounded_when_artifacts_are_large(self):
        for i in range(4):
            task=self.create('large-'+str(i));self.p._set(task['id'],state='completed',result=json.dumps({'text':'x'*6500}))
        response=self.rpc('ListTasks',{'pageSize':100,'includeArtifacts':True})['result']
        self.assertEqual(len(response['tasks']),1);self.assertTrue(response['nextPageToken'])
        self.assertEqual(response['totalSize'],4)
        from commons_relay.codec import canonical
        self.assertLess(len(canonical(response)),13000)
    def test_new_peer_tasks_do_not_appear_in_existing_page_scope(self):
        self.create('one');self.create('two')
        first=self.rpc('ListTasks',{'pageSize':1})['result']
        self.provider.now+=10;hidden=self.create('hidden','other')
        second=self.rpc('ListTasks',{'pageSize':1,'pageToken':first['nextPageToken']})['result']
        self.assertEqual(second['totalSize'],2)
        self.assertNotIn(hidden['id'],[x['id'] for x in second['tasks']])
