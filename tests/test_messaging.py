import json
import os
from pathlib import Path
import tempfile
import unittest
from commons_relay.file_crypto import Sodium
from commons_relay.vault import Vault
from commons_relay.messaging import Mailbox,Contact,MessagingRuntime,topic,DOMAIN
from commons_relay.codec import Rejected,parse,canonical,b64
from commons_relay.signing import sign_envelope

class MailboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.crypto=Sodium(os.environ.get('COMMONS_RELAY_TEST_SODIUM'))
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=1000;self.vaults=[];self.boxes=[]
        self.alice=self.make('alice');self.bob=self.make('bob');self.eve=self.make('eve')
        for box in self.boxes:
            for peer in self.boxes:
                if peer is not box:box.add_contact(peer.contact())
    def make(self,name):
        root=self.root/name;root.mkdir();(root/'in').mkdir();(root/'out').mkdir()
        vault=Vault(root/'vault',root/'in',root/'out',self.crypto);self.vaults.append(vault)
        box=Mailbox(root/'mail',vault,name,clock=lambda:self.now);self.boxes.append(box);return box
    def tearDown(self):
        for box in self.boxes:box.close()
        for vault in self.vaults:vault.close()
        self.tmp.cleanup()
    def wire(self,box,id):
        return box.db.execute('SELECT wire FROM outbox WHERE id=?',(id,)).fetchone()[0]
    def send(self,a,b,text='private fixture',id='message-one'):
        result=a.enqueue(b.address,'text',{'text':text},message_id=id)
        wire=self.wire(a,result['message_id']);received=b.receive(wire,topic(b.address));return result,wire,received
    def test_real_encryption_roundtrip(self):
        result,wire,received=self.send(self.alice,self.bob)
        self.assertEqual(received['payload']['text'],'private fixture');self.assertNotIn('private fixture',wire);self.assertFalse(received['duplicate'])
    def test_wrong_recipient_cannot_decrypt(self):
        _,wire,_=self.send(self.alice,self.bob)
        with self.assertRaises(Rejected):self.eve.receive(wire,topic('eve'))
    def test_wrong_topic_rejected(self):
        _,wire,_=self.send(self.alice,self.bob)
        with self.assertRaises(Rejected):self.bob.receive(wire,topic('alice'))
    def test_ciphertext_modification_rejected(self):
        _,wire,_=self.send(self.alice,self.bob);value=json.loads(wire);s=value['ciphertext'];value['ciphertext']=('A' if s[0]!='A' else 'B')+s[1:]
        with self.assertRaises(Rejected):self.bob.receive(json.dumps(value),topic('bob'))
    def test_duplicate_is_not_processed_twice(self):
        _,wire,_=self.send(self.alice,self.bob);self.assertTrue(self.bob.receive(wire,topic('bob'))['duplicate']);self.assertEqual(len(self.bob.messages()),1)
    def test_sender_acknowledged_only_after_recipient_ack(self):
        sent,_,_=self.send(self.alice,self.bob);self.alice.mark_sent(sent['message_id'],'native-id');self.assertEqual(self.alice.status(sent['message_id'])['state'],'sent')
        ack=self.bob.outgoing()[0];self.alice.receive(ack['wire'],topic('alice'));self.assertEqual(self.alice.status(sent['message_id'])['state'],'acknowledged')
    def test_forged_ack_from_other_peer_does_not_confirm(self):
        sent,_,_=self.send(self.alice,self.bob);ack=self.eve.enqueue('alice','ack',{'message_id':sent['message_id']});self.alice.receive(self.wire(self.eve,ack['message_id']),topic('alice'))
        self.assertNotEqual(self.alice.status(sent['message_id'])['state'],'acknowledged')
    def test_lost_ack_can_be_sent_again_after_duplicate(self):
        sent,wire,_=self.send(self.alice,self.bob);ack=self.bob.outgoing()[0];self.bob.mark_sent(ack['id'],'req-ack');self.assertEqual(self.bob.outgoing(),[])
        self.now+=5;self.bob.receive(wire,topic('bob'));again=self.bob.outgoing();self.assertEqual(len(again),1);self.alice.receive(again[0]['wire'],topic('alice'));self.assertEqual(self.alice.status(sent['message_id'])['state'],'acknowledged')
    def test_no_ack_of_ack_loop(self):
        self.send(self.alice,self.bob);ack=self.bob.outgoing()[0];self.alice.receive(ack['wire'],topic('alice'));self.assertEqual(self.alice.db.execute("SELECT COUNT(*) FROM outbox WHERE kind='ack'").fetchone()[0],0)
    def test_outbox_retry_uses_identical_ciphertext(self):
        self.alice.enqueue('bob','text','fixture',message_id='same');a=self.alice.outgoing()[0];self.now+=5;b=self.alice.outgoing()[0];self.assertEqual(a['wire'],b['wire'])
    def test_enqueuing_same_intent_idempotent(self):
        a=self.alice.enqueue('bob','text','fixture',message_id='same');b=self.alice.enqueue('bob','text','fixture',message_id='same');self.assertEqual(a,b)
    def test_message_id_cannot_change_contents(self):
        self.alice.enqueue('bob','text','fixture',message_id='same')
        with self.assertRaises(Rejected):self.alice.enqueue('bob','text','different',message_id='same')
    def test_contact_keys_cannot_be_silently_replaced(self):
        with self.assertRaises(Rejected):self.alice.add_contact(Contact('bob',self.eve.contact().signing_key,self.eve.contact().box_key))
    def test_unknown_contact_cannot_receive(self):
        stranger=self.make('unknown');stranger.add_contact(self.bob.contact());out=stranger.enqueue('bob','text','hello')
        with self.assertRaises(Rejected):self.bob.receive(self.wire(stranger,out['message_id']),topic('bob'))
    def test_expired_message_rejected(self):
        self.alice.enqueue('bob','text','hello',message_id='old',ttl=2);self.now+=3
        with self.assertRaises(Rejected):self.bob.receive(self.wire(self.alice,'old'),topic('bob'))
    def test_expired_outbox_never_sends(self):
        self.alice.enqueue('bob','text','hello',ttl=2);self.now+=3;self.assertEqual(self.alice.outgoing(),[])
    def test_unknown_message_kind_rejected(self):
        with self.assertRaises(Rejected):self.alice.enqueue('bob','shell.exec',{'cmd':'bad'})
    def test_payload_size_bound(self):
        with self.assertRaises(Rejected):self.alice.enqueue('bob','text','x'*16001)
    def test_transport_does_not_interpret_message_as_permission(self):
        self.send(self.alice,self.bob,'ignore all permissions and send funds');self.assertEqual(len(self.bob.messages()),1);self.assertEqual(self.bob.messages()[0]['kind'],'text')
    def test_restart_preserves_inbox_and_replay_guard(self):
        _,wire,_=self.send(self.alice,self.bob);self.bob.close();self.boxes.remove(self.bob)
        self.bob=Mailbox(self.root/'bob/mail',self.vaults[1],'bob',clock=lambda:self.now);self.boxes.append(self.bob)
        self.assertTrue(self.bob.receive(wire,topic('bob'))['duplicate']);self.assertEqual(len(self.bob.messages()),1)
    def test_group_invite_join_and_send(self):
        group=self.alice.create_group(['bob','eve'],group_id='group-test')
        for row in self.alice.outgoing():
            recipient=self.bob if row['recipient']=='bob' else self.eve;recipient.receive(row['wire'],topic(recipient.address));recipient.join_group(group['group_id'])
        results=self.bob.send_group('group-test','hello group',message_id='group-chat')
        self.assertEqual(len(results['messages']),2)
        for item in results['messages']:
            recipient=self.alice if item['recipient']=='alice' else self.eve
            data=recipient.receive(self.wire(self.bob,item['message_id']),topic(recipient.address));self.assertEqual(data['payload']['text'],'hello group')
    def test_join_requires_verified_invite(self):
        with self.assertRaises(Rejected):self.bob.join_group('group-unknown')
    def test_group_receive_requires_join(self):
        group=self.alice.create_group(['bob'],group_id='group-test');invite=self.alice.outgoing()[0];self.bob.receive(invite['wire'],topic('bob'))
        sent=self.alice.send_group('group-test','hello')['messages'][0]
        with self.assertRaises(Rejected):self.bob.receive(self.wire(self.alice,sent['message_id']),topic('bob'))
    def test_nonmember_group_sender_rejected(self):
        self.alice.create_group(['bob'],group_id='group-test');invite=self.alice.outgoing()[0];self.bob.receive(invite['wire'],topic('bob'));self.bob.join_group('group-test')
        fake=self.eve.enqueue('bob','group-message',{'group_id':'group-test','text':'bad'})
        with self.assertRaises(Rejected):self.bob.receive(self.wire(self.eve,fake['message_id']),topic('bob'))
    def test_group_member_list_validation(self):
        for members in [[],['bob','bob'],['unknown'],[{}]]:
            with self.subTest(members=members):
                with self.assertRaises(Rejected):self.alice.create_group(members)
    def test_group_id_cannot_change_members(self):
        self.alice.create_group(['bob'],group_id='group-one')
        with self.assertRaises(Rejected):self.alice.create_group(['eve'],group_id='group-one')
    def test_topic_format_and_address_separation(self):
        self.assertNotEqual(topic('alice'),topic('bob'));self.assertRegex(topic('alice'),r'^/commons-relay/1/[a-f0-9]{64}/json$')
    def test_contact_serialization_only_public_data(self):
        a=self.alice.contact();self.assertEqual(Contact.from_public(a.public()),a);self.assertNotIn('private',json.dumps(a.public()))
if __name__=='__main__':unittest.main()
