"""Message effects are prepared separately from publication and receipt lookup."""
import json
import time
from .codec import Rejected,canonical
from .engine import Prepared,Receipt
from .vault import Peer

class MessagingAdapter:
    SKILLS=frozenset(['messaging.send','messaging.inbox','messaging.join','messaging.create_group','storage.share'])
    def __init__(self,runtime):
        self.runtime=runtime;self.mailbox=runtime.mailbox
        with self.mailbox.tx() as db:db.execute('CREATE TABLE IF NOT EXISTS skill_effects(task_id TEXT PRIMARY KEY,skill TEXT NOT NULL,args TEXT NOT NULL,deadline INTEGER NOT NULL,result TEXT)')
    def prepare(self,task):
        if task['skill'] not in self.SKILLS:raise Rejected('MESSAGE_SKILL_UNAVAILABLE')
        if task['maximum_spend']!='0':raise Rejected('UNEXPECTED_MESSAGING_COST')
        self.runtime.initialize();args=task['arguments'];skill=task['skill']
        if skill=='storage.share':
            selected=self.mailbox.vault._file(args['address']);self.mailbox.get_contact(args['recipient'])
            # Bind a friendly unique label to its exact content address before
            # the share is persisted, so later catalogue changes cannot retarget it.
            args={**args,'address':selected['address']}
        if skill=='messaging.inbox' and args:raise Rejected('INVALID_MESSAGE_INBOX_ARGUMENTS')
        if skill=='messaging.send' and not args['recipient'].startswith('group-'):self.mailbox.get_contact(args['recipient'])
        with self.mailbox.tx() as db:
            encoded=canonical(args).decode();old=db.execute('SELECT * FROM skill_effects WHERE task_id=?',(task['id'],)).fetchone()
            if old and (old['skill']!=skill or old['args']!=encoded):raise Rejected('MESSAGE_EFFECT_REUSED')
            db.execute('INSERT OR IGNORE INTO skill_effects VALUES (?,?,?,?,NULL)',(task['id'],skill,encoded,task['deadline']))
        return Prepared('message:'+task['id'],0,task['id'])
    def broadcast(self,effect):
        with self.mailbox.guard:row=self.mailbox.db.execute('SELECT * FROM skill_effects WHERE task_id=?',(effect.opaque_handle,)).fetchone()
        if not row:raise Rejected('MESSAGE_EFFECT_NOT_PREPARED')
        if row['result']:return
        args=json.loads(row['args']);skill=row['skill'];ttl=max(1,min(3600,row['deadline']-int(self.mailbox.clock())))
        if row['deadline']<=int(self.mailbox.clock()):raise Rejected('MESSAGE_AUTHORIZATION_EXPIRED')
        task_id=effect.opaque_handle
        if skill=='messaging.send':
            if args['recipient'].startswith('group-'):result=self.mailbox.send_group(args['recipient'],args['message'],message_id=task_id)
            else:result=self.mailbox.enqueue(args['recipient'],'text',{'text':args['message']},message_id=task_id,ttl=ttl)
        elif skill=='messaging.inbox':result={'messages':self.mailbox.recent_user_messages()}
        elif skill=='messaging.create_group':result=self.mailbox.create_group(args['members'],group_id='group-'+task_id)
        elif skill=='messaging.join':result=self.mailbox.join_group(args['group_id'])
        elif skill=='storage.share':
            peer=self.mailbox.get_contact(args['recipient']);p=Peer(peer.address,peer.signing_key,peer.box_key)
            share=self.mailbox.vault.make_share(args['address'],p,lifetime=ttl)
            result=self.mailbox.enqueue(peer.address,'file-share',share,message_id=task_id,ttl=ttl)
        else:raise Rejected('MESSAGE_SKILL_UNAVAILABLE')
        with self.mailbox.tx() as db:db.execute('UPDATE skill_effects SET result=? WHERE task_id=?',(canonical(result).decode(),task_id))
        self.runtime.pump()
    def lookup(self,effect):
        with self.mailbox.guard:row=self.mailbox.db.execute('SELECT * FROM skill_effects WHERE task_id=?',(effect.opaque_handle,)).fetchone()
        if not row:return Receipt(effect.reference,'pending')
        if row['skill'] in ['messaging.inbox','messaging.join','messaging.create_group'] and row['result']:return Receipt(effect.reference,'confirmed',0,json.loads(row['result']))
        if row['result']:
            result=json.loads(row['result']);ids=[r['message_id'] for r in result['messages']] if 'messages' in result else [result['message_id']]
        else:
            # Recover a send that was durably enqueued before the caller lost
            # the return value. Never generate a second message ID.
            try:self.mailbox.status(effect.opaque_handle)
            except Rejected:return Receipt(effect.reference,'pending')
            ids=[effect.opaque_handle];result={'message_id':effect.opaque_handle}
        statuses=[self.mailbox.status(id) for id in ids]
        if row['skill']=='messaging.send' and isinstance(result,dict) and result.get('group_id'):
            membership=self.mailbox.group_membership(result['group_id']);delivered=[];waiting=[];pending=[]
            for status in statuses:
                if status['state']=='acknowledged':delivered.append(status['recipient'])
                elif membership.get(status['recipient'])=='joined':pending.append(status['recipient'])
                else:
                    waiting.append(status['recipient']);self.mailbox.suppress_message(status['id'])
            if not pending and delivered:
                return Receipt(effect.reference,'confirmed',0,{**result,'state':'acknowledged','receipt':'delivered to joined group members','delivered_to':delivered,'waiting_for_join':waiting})
            return Receipt(effect.reference,'pending')
        states=[status['state'] for status in statuses]
        if all(state=='acknowledged' for state in states):return Receipt(effect.reference,'confirmed',0,{**result,'state':'acknowledged','receipt':'recipient inbox committed'})
        return Receipt(effect.reference,'pending')
