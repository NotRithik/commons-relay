from pathlib import Path
import json
import sqlite3
import threading
import unittest
from commons_relay.codec import canonical, Rejected
from commons_relay.messaging import Mailbox

class InboxPageBounds(unittest.TestCase):
    def setUp(self):
        self.mailbox=object.__new__(Mailbox)
        self.mailbox.guard=threading.RLock()
        self.mailbox.db=sqlite3.connect(':memory:',isolation_level=None)
        self.mailbox.db.execute('CREATE TABLE inbox(id TEXT PRIMARY KEY, kind TEXT, body TEXT)')
        self.mailbox.db.execute('CREATE TABLE inbox_archive(seq INTEGER PRIMARY KEY,id TEXT,kind TEXT,body TEXT)')
        self.mailbox.db.execute('CREATE VIEW inbox_history AS SELECT rowid,id,kind,body FROM inbox UNION ALL SELECT seq AS rowid,id,kind,body FROM inbox_archive')
        for n in range(100):
            body={'id':str(n),'kind':'owner-result','sender':'fixture-agent','payload':{'text':'x'*4096}}
            self.mailbox.db.execute('INSERT INTO inbox VALUES (?,?,?)',(str(n),'owner-result',json.dumps(body)))
        self.mailbox.db.commit()
    def tearDown(self): self.mailbox.db.close()
    def test_pages_fit_wire_without_truncating_any_message(self):
        cursor=0;received=[];pages=0
        while True:
            page=self.mailbox.messages(cursor)
            if not page:break
            pages+=1
            self.assertLess(len(canonical({'id':'fixture','success':True,'result':{'messages':page}})),65536)
            for row in page:
                self.assertEqual(row['payload']['text'],'x'*4096)
                received.append(row['id'])
            cursor=page[-1]['cursor']
        self.assertGreater(pages,1)
        self.assertEqual(received,[str(n) for n in range(100)])
    def test_oversized_single_message_is_an_error_not_a_silent_skip(self):
        self.mailbox.db.execute('DELETE FROM inbox')
        self.mailbox.db.execute('INSERT INTO inbox VALUES (?,?,?)',('big','owner-result',json.dumps({'payload':['x'*24000]*3})))
        with self.assertRaises(Rejected): self.mailbox.messages()
    def test_invalid_limits_do_not_query_unbounded_rows(self):
        for limit in [0,-1,101,True,1.5]:
            with self.assertRaises(Rejected):self.mailbox.messages(0,limit)
    def test_cursor_continues_after_the_exact_last_returned_row(self):
        first=self.mailbox.messages(0,3)
        second=self.mailbox.messages(first[-1]['cursor'],3)
        self.assertEqual([x['id'] for x in second],['3','4','5'])

class OwnerInboxTransportTests(InboxPageBounds):
    def test_signed_payload_depth_is_not_increased_by_the_wire_envelope(self):
        from commons_relay.owner_views import inbox_page
        self.mailbox.db.execute('DELETE FROM inbox')
        value={'text':'safe data'}
        for _ in range(13):value={'nested':value}
        self.mailbox.db.execute('INSERT INTO inbox VALUES (?,?,?)',('nested','owner-result',json.dumps({'sender':'fixture-agent','payload':value})))
        result=inbox_page(self.mailbox,0)
        encoded=canonical({'id':'x','success':True,'result':result})
        self.assertLess(len(encoded),65536)
        self.assertEqual(json.loads(result['messages'][0]['payload_json']),value)
    def test_old_oversized_payload_is_explicit_and_does_not_hide_newer_replies(self):
        from commons_relay.owner_views import inbox_page
        self.mailbox.db.execute('DELETE FROM inbox')
        for name,payload in [('big',{'text':'z'*40000}),('new',{'success':True})]:
            self.mailbox.db.execute('INSERT INTO inbox VALUES (?,?,?)',(name,'owner-result',json.dumps({'sender':'fixture-agent','payload':payload})))
        page=inbox_page(self.mailbox,0)['messages']
        self.assertTrue(page[0]['payload_omitted'])
        self.assertTrue(json.loads(page[1]['payload_json'])['success'])
