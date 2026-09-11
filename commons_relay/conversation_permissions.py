"""A model may request an action; only a separate owner signature can authorize it.

Requests are data, not grants. The ordinary permission engine executes exactly
one owner-reviewed task. Replayed approval resumes that task instead of creating
another one, including a crash between task submission and scheduling.
"""
import json
import re
from .codec import Rejected, canonical, digest, identifier
from .engine import REQUEST_DOMAIN
from .signing import sign_envelope, verify_envelope

DOMAIN = 'commons/relay/conversation-permission/v1'
FORBIDDEN = frozenset({'meta.configure'})


def compose_decision(command, agent, signer, private, now):
    fields = {'kind','goal_id','permission','decision'}
    if not isinstance(command,dict) or set(command)!=fields:
        raise Rejected('INVALID_PERMISSION_REVIEW')
    request=command['permission']; goal=identifier(command['goal_id'])
    if not isinstance(request,dict) or len(canonical(request))>8000:
        raise Rejected('INVALID_PERMISSION_REVIEW')
    required={'goal_id','skill','arguments','reason','description','maximum_spend','asset','policy_version','expires_at','intent_hash'}
    if not required<=set(request) or set(request)-required-{'execution_expires_at'} or request['goal_id']!=goal:
        raise Rejected('INVALID_PERMISSION_REVIEW')
    check={key:value for key,value in request.items() if key!='intent_hash'}
    if request['intent_hash']!=digest(check): raise Rejected('PERMISSION_REVIEW_CHANGED')
    if command['decision'] not in ('approve','decline'): raise Rejected('INVALID_PERMISSION_DECISION')
    if type(request['expires_at']) is not int or not now<request['expires_at']<=now+3600:
        raise Rejected('PERMISSION_REQUEST_EXPIRED')
    expiry=min(now+600,request['expires_at'])
    execution_expiry=request.get('execution_expires_at',expiry)
    if type(execution_expiry) is not int or not now<execution_expiry<=now+7200:
        raise Rejected('INVALID_EXECUTION_WINDOW')
    task=None
    if command['decision']=='approve':
        body={'domain':REQUEST_DOMAIN,'agent_id':agent,'request_id':'permission-'+goal,
              'skill':request['skill'],'arguments':request['arguments'],'expires_at':execution_expiry}
        task=sign_envelope(body,private,signer)
    body={'domain':DOMAIN,'agent_id':agent,'goal_id':goal,'permission_hash':request['intent_hash'],
          'decision':command['decision'],'task_envelope':task,'expires_at':expiry}
    return {'method':'planner.permission','params':{'envelope':sign_envelope(body,private,signer)}}


class ConversationPermissions:
    def __init__(self,planner):
        self.planner=planner;self.engine=planner.engine
        with planner.lock:
            planner.db.execute('''CREATE TABLE IF NOT EXISTS conversation_permissions (
                goal_id TEXT PRIMARY KEY, request TEXT NOT NULL,
                decision TEXT NOT NULL DEFAULT 'pending', task_id TEXT)''')
            columns={row[1] for row in planner.db.execute('PRAGMA table_info(conversation_permissions)')}
            if 'attempt' not in columns:
                planner.db.execute('ALTER TABLE conversation_permissions ADD COLUMN attempt TEXT')
    def row(self,goal):
        return self.planner.db.execute('SELECT * FROM conversation_permissions WHERE goal_id=?',(goal,)).fetchone()
    def view(self,goal):
        with self.planner.lock:
            row=self.row(goal)
            if row is None:return None
            self.reconcile_existing(goal)
            row=self.row(goal)
            return {'request':json.loads(row['request']),'decision':row['decision'],'task_id':row['task_id']}
    def quote(self,skill,arguments):
        item=self.engine.registry.get(skill);quote=item.validate(arguments)
        if self.engine.quote_provider is not None:quote=self.engine.quote_provider(skill,arguments,quote)
        return item,quote
    def propose(self,grant,params):
        if not isinstance(params,dict) or set(params)!={'skill','arguments','reason'}:
            raise Rejected('INVALID_PERMISSION_REQUEST')
        skill=identifier(params['skill']);reason=params['reason']
        if skill in FORBIDDEN or skill in grant['allowed_skills']:
            raise Rejected('PERMISSION_REQUEST_NOT_NEEDED_OR_FORBIDDEN')
        if not isinstance(reason,str) or not 1<=len(reason.strip())<=800:
            raise Rejected('INVALID_PERMISSION_REASON')
        if not isinstance(params['arguments'],dict) or len(canonical(params['arguments']))>4500:
            raise Rejected('INVALID_PERMISSION_ARGUMENTS')
        item,quote=self.quote(skill,params['arguments'])
        if quote.maximum>self.engine.policy.hard_maximum:raise Rejected('HARD_LIMIT_EXCEEDED')
        now=int(self.engine.clock())
        if grant['expires_at']<=now or self.planner.cancel_event.is_set():raise Rejected('REQUEST_EXPIRED')
        goal=grant['grant_id']
        with self.planner.lock:
            current=self.planner._row(goal)
            if current['state'] in ('cancelled','interrupted','failed','completed'):
                raise Rejected('CONVERSATION_NOT_ACTIVE')
            old=self.row(goal)
            if old:
                request=json.loads(old['request'])
                if request['skill']!=skill or request['arguments']!=params['arguments'] or request['reason']!=reason.strip():
                    raise Rejected('ONE_PERMISSION_REQUEST_PER_TURN')
                return {'permission_required':True,**self.view(goal)}
            request={'goal_id':goal,'skill':skill,'arguments':params['arguments'],'reason':reason.strip(),
                     'description':item.description,'maximum_spend':str(quote.maximum),'asset':quote.asset,
                     'policy_version':self.engine.policy.version,'expires_at':now+1800}
            if quote.maximum>0 and skill in ('wallet.send','agent.task'):
                request['execution_expires_at']=now+min(7200,self.engine.policy.approval_ttl)
            if len(canonical(request)) > 6000: raise Rejected('PERMISSION_REQUEST_TOO_LARGE')
            request['intent_hash']=digest(request)
            self.planner.db.execute('INSERT INTO conversation_permissions(goal_id,request) VALUES (?,?)',(goal,canonical(request).decode()))
            return {'permission_required':True,'request':request,'decision':'pending','task_id':None}
    def _request(self,row):
        request=json.loads(row['request'])
        check={key:value for key,value in request.items() if key!='intent_hash'}
        if request.get('intent_hash')!=digest(check) or request.get('goal_id')!=row['goal_id']:
            raise Rejected('PERMISSION_REVIEW_CHANGED')
        return request
    def _existing(self,goal,row,request):
        # The engine's accepted immutable intent is authoritative after its
        # commit. Current quotes/policy cannot erase that already accepted task.
        with self.engine.tx() as db:
            saved=db.execute('SELECT * FROM tasks WHERE requester=? AND request_id=?',
                             (self.engine.owner,'permission-'+goal)).fetchone()
        if saved is None:
            if row['task_id']:raise Rejected('PERMISSION_TASK_BINDING_MISMATCH')
            return None
        expected={'agent_id':self.engine.agent,'requester':self.engine.owner,
                  'skill':request['skill'],'arguments':request['arguments'],
                  'asset':request['asset'],'maximum_spend':request['maximum_spend'],
                  'policy_version':request['policy_version']}
        if (json.loads(saved['intent'])!=expected or saved['hash']!=digest(expected)
            or saved['skill']!=request['skill'] or json.loads(saved['args'])!=request['arguments']
            or saved['amount']!=request['maximum_spend'] or saved['asset']!=request['asset']
            or type(saved['deadline']) is not int or not 0<saved['deadline']<=request.get('execution_expires_at',request['expires_at'])
            or (row['task_id'] and row['task_id']!=saved['id'])):
            raise Rejected('PERMISSION_TASK_BINDING_MISMATCH')
        if row['attempt']:
            attempt=verify_envelope(json.loads(row['attempt']),self.engine.owner_key,self.engine.crypto)
            task_body=verify_envelope(attempt.get('task_envelope'),self.engine.owner_key,self.engine.crypto)
            # Engine.approve may narrow the executable deadline. Keep checking
            # the ORIGINAL signed request; a narrower deadline does not change
            # the immutable intent and must not break completed-result recovery.
            request_deadline=request.get('execution_expires_at',attempt.get('expires_at'))
            if type(request_deadline) is not int or not 0<saved['deadline']<=request_deadline:
                raise Rejected('PERMISSION_TASK_BINDING_MISMATCH')
            expected_body={'domain':REQUEST_DOMAIN,'agent_id':self.engine.agent,'request_id':'permission-'+goal,
                           'skill':request['skill'],'arguments':request['arguments'],'expires_at':request_deadline}
            if (attempt.get('domain')!=DOMAIN or attempt.get('agent_id')!=self.engine.agent
                or attempt.get('goal_id')!=goal or attempt.get('decision')!='approve'
                or attempt.get('permission_hash')!=request['intent_hash']
                or task_body!=expected_body):
                raise Rejected('PERMISSION_TASK_BINDING_MISMATCH')
        # get() applies the normal engine expiry rules. An expired authorization
        # is never revived merely to repair the conversation's metadata.
        return self.engine.get(saved['id'])
    def _link_accepted(self,goal,task):
        db=self.planner.db
        db.execute('BEGIN IMMEDIATE')
        try:
            row=self.row(goal)
            if row['decision']=='decline':raise Rejected('PERMISSION_TASK_BINDING_MISMATCH')
            conversation=self.planner._row(goal)
            ids=json.loads(conversation['task_ids'])
            repair=row['decision']!='approve' or row['task_id']!=task['id'] or task['id'] not in ids
            if repair:
                if task['id'] not in ids:ids.append(task['id'])
                db.execute("UPDATE conversation_permissions SET decision='approve',task_id=? WHERE goal_id=?",(task['id'],goal))
                if conversation['state']=='cancelled':
                    db.execute('UPDATE conversations SET task_ids=? WHERE id=?',(json.dumps(ids),goal))
                else:
                    db.execute("UPDATE conversations SET task_ids=?,state='waiting',reply=?,error=NULL,updated=? WHERE id=?",
                        (json.dumps(ids),'You approved the requested action. Its current status and result are shown in the linked tool result.',int(self.engine.clock()),goal))
            db.execute('COMMIT')
        except BaseException:
            db.execute('ROLLBACK');raise
    def reconcile_existing(self,goal):
        # Reading a saved result may repair missing links. It never submits,
        # schedules, broadcasts or runs an action.
        with self.planner.lock:
            row=self.row(goal)
            if row is None:return None
            request=self._request(row)
            task=self._existing(goal,row,request)
            if task is not None:self._link_accepted(goal,task)
            return task
    def resume_accepted(self,controller):
        # Called only by explicit agent.start, not by a read-only view. This
        # restores the schedule for an already committed, still-valid owner
        # task after process loss; no new task or authorization is created.
        resumed=[]
        with self.planner.lock:
            rows=list(self.planner.db.execute("SELECT goal_id FROM conversation_permissions WHERE decision IN ('pending','approve') ORDER BY rowid LIMIT 1001"))
            if len(rows)>1000:raise Rejected('PLANNER_HISTORY_LIMIT')
            for row in rows:
                goal=row['goal_id'];task=self.reconcile_existing(goal)
                if (task is not None and task['state']=='submitted' and task['deadline']>int(self.engine.clock())
                    and self.planner._row(goal)['state']!='cancelled'):
                    controller.schedule(task['id']);resumed.append(task['id'])
        return resumed
    def _schedule_accepted(self,goal,task):
        if (task['state']=='submitted' and task['deadline']>int(self.engine.clock())
            and self.planner._row(goal)['state']!='cancelled'):
            controller=self.planner.service.get_controller();controller.start();controller.schedule(task['id'])
    def decide(self,envelope):
        body=verify_envelope(envelope,self.engine.owner_key,self.engine.crypto)
        required={'domain','agent_id','goal_id','permission_hash','decision','task_envelope','expires_at'}
        now=int(self.engine.clock())
        if set(body)!=required or body['domain']!=DOMAIN or body['agent_id']!=self.engine.agent:
            raise Rejected('PERMISSION_OWNER_BINDING_MISMATCH')
        if type(body['expires_at']) is not int or body['expires_at']>now+600:
            raise Rejected('PERMISSION_REQUEST_EXPIRED')
        if body['decision'] not in ('approve','decline'):raise Rejected('INVALID_PERMISSION_DECISION')
        goal=identifier(body['goal_id'])
        with self.planner.lock:
            row=self.row(goal)
            if row is None:raise Rejected('PERMISSION_REQUEST_NOT_FOUND')
            request=self._request(row)
            if body['permission_hash']!=request['intent_hash']:raise Rejected('PERMISSION_REVIEW_CHANGED')
            if body['decision']=='approve':
                task_body=verify_envelope(body['task_envelope'],self.engine.owner_key,self.engine.crypto)
                expected={'domain':REQUEST_DOMAIN,'agent_id':self.engine.agent,'request_id':'permission-'+goal,
                          'skill':request['skill'],'arguments':request['arguments'],'expires_at':request.get('execution_expires_at',body['expires_at'])}
                if task_body!=expected:raise Rejected('PERMISSION_ACTION_MISMATCH')
            elif body['task_envelope'] is not None:raise Rejected('INVALID_PERMISSION_DECISION')
            existing=self._existing(goal,row,request)
            if existing is not None:
                self._link_accepted(goal,existing)
                if body['decision']=='decline':raise Rejected('PERMISSION_ACTION_ALREADY_SUBMITTED')
                self._schedule_accepted(goal,existing)
                return self.planner.view(goal)
            if row['decision']!='pending':
                if row['decision']!=body['decision']:raise Rejected('PERMISSION_ALREADY_DECIDED')
                return self.planner.view(goal)
            if self.planner._row(goal)['state']=='cancelled':raise Rejected('CONVERSATION_NOT_ACTIVE')
            if body['expires_at']<=now or request['expires_at']<=now:raise Rejected('PERMISSION_REQUEST_EXPIRED')
            if self.planner.active == goal and self.planner.thread and self.planner.thread.is_alive():
                raise Rejected('PERMISSION_TURN_STILL_RUNNING')
            if body['decision']=='decline':
                db=self.planner.db;db.execute('BEGIN IMMEDIATE')
                try:
                    db.execute("UPDATE conversation_permissions SET decision='decline' WHERE goal_id=?",(goal,))
                    self.planner._set(goal,state='completed',reply='You declined the requested action. It was not run.')
                    db.execute('COMMIT')
                except BaseException:db.execute('ROLLBACK');raise
                return self.planner.view(goal)
            _,quote=self.quote(request['skill'],request['arguments'])
            if (self.engine.policy.version!=request['policy_version'] or str(quote.maximum)!=request['maximum_spend'] or quote.asset!=request['asset']):
                raise Rejected('PERMISSION_QUOTE_OR_POLICY_CHANGED')
            # Save the signed attempt before the independent engine commit. A
            # process loss at any following line can adopt this exact task.
            self.planner.db.execute('UPDATE conversation_permissions SET attempt=? WHERE goal_id=?',(canonical(envelope).decode(),goal))
            reviewed={'agent_id':self.engine.agent,'requester':self.engine.owner,
                      'skill':request['skill'],'arguments':request['arguments'],
                      'asset':request['asset'],'maximum_spend':request['maximum_spend'],
                      'policy_version':request['policy_version']}
            task=self.engine.submit(body['task_envelope'],self.engine.owner_key,reviewed_intent=reviewed)
            row=self.row(goal);accepted=self._existing(goal,row,request)
            if accepted is None or accepted['id']!=task['id']:raise Rejected('PERMISSION_TASK_BINDING_MISMATCH')
            self._link_accepted(goal,accepted)
            self._schedule_accepted(goal,accepted)
            return self.planner.view(goal)
