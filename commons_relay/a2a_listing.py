"""Peer-scoped A2A ListTasks with authenticated cursor pagination.

Only stored task documents are read. Listing never runs, pays, or schedules work.
"""
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
import re
import secrets
from .codec import Rejected, canonical, identifier, digest
from .a2a_types import STATES, task_document

MAX_RESULT = 13000
FIELDS = {'tenant','contextId','status','pageSize','pageToken','historyLength','statusTimestampAfter','includeArtifacts'}
STAMP = re.compile(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$')

def timestamp_lower_bound(value):
    if not isinstance(value,str): raise Rejected('INVALID_A2A_TIMESTAMP')
    match=STAMP.fullmatch(value)
    if match is None: raise Rejected('INVALID_A2A_TIMESTAMP')
    try:
        parsed=datetime.fromisoformat(match[1]+('+00:00' if match[3]=='Z' else match[3]))
        utc=parsed.astimezone(timezone.utc)
        seconds=(utc-datetime(1970,1,1,tzinfo=timezone.utc)).days*86400+utc.hour*3600+utc.minute*60+utc.second
    except (ValueError,OverflowError):raise Rejected('INVALID_A2A_TIMESTAMP') from None
    return seconds+int(bool(match[2] and int(match[2])))

def _key(engine):
    with engine.tx() as db:
        row=db.execute("SELECT value FROM metadata WHERE key='a2a:cursor-key:v1'").fetchone()
        if row is None:
            raw=secrets.token_hex(32)
            db.execute("INSERT INTO metadata VALUES ('a2a:cursor-key:v1',?)",(raw,))
        else:raw=row[0]
    if not isinstance(raw,str) or not re.fullmatch('[a-f0-9]{64}',raw):raise Rejected('A2A_CURSOR_KEY_INVALID')
    return bytes.fromhex(raw)

def _token(engine,scope,row):
    raw=canonical({'v':1,'scope':scope,'updated':row['updated'],'id':row['id']})
    tag=hmac.digest(_key(engine),raw,'sha256')
    return base64.urlsafe_b64encode(raw+tag).decode().rstrip('=')

def _cursor(engine,scope,value):
    if not isinstance(value,str) or len(value)>1000 or not re.fullmatch('[A-Za-z0-9_-]+',value):raise Rejected('INVALID_A2A_PAGE_TOKEN')
    try:
        raw=base64.urlsafe_b64decode(value+'='*((-len(value))%4))
        if base64.urlsafe_b64encode(raw).decode().rstrip('=')!=value or len(raw)<33:raise ValueError()
        content,tag=raw[:-32],raw[-32:]
        if not hmac.compare_digest(hmac.digest(_key(engine),content,'sha256'),tag):raise ValueError()
        record=json.loads(content)
        if (set(record)!={'v','scope','updated','id'} or record['v']!=1 or record['scope']!=scope
            or type(record['updated'])is not int or not 0<=record['updated']<=253402300799):raise ValueError()
        identifier(record['id'])
        return record
    except (ValueError,TypeError,KeyError):raise Rejected('INVALID_A2A_PAGE_TOKEN') from None

def list_tasks(protocol,peer,params):
    # Resolve the authenticated contact before reading any task rows or totals.
    protocol.mailbox.get_contact(peer)
    if not isinstance(params,dict) or set(params)-FIELDS:raise Rejected('INVALID_A2A_LIST_PARAMS')
    if params.get('tenant','')!='':raise Rejected('UNSUPPORTED_A2A_TENANT')
    limit=params.get('pageSize',50)
    if type(limit)is not int or not 1<=limit<=100:raise Rejected('INVALID_A2A_PAGE_SIZE')
    history=params.get('historyLength')
    if history is not None and (type(history)is not int or not 0<=history<=2147483647):raise Rejected('INVALID_A2A_HISTORY_LENGTH')
    include=params.get('includeArtifacts',False)
    if type(include)is not bool:raise Rejected('INVALID_A2A_ARTIFACT_FLAG')
    context=params.get('contextId','')
    if context:identifier(context)
    elif not isinstance(context,str):raise Rejected('INVALID_A2A_CONTEXT')
    status=params.get('status','TASK_STATE_UNSPECIFIED')
    inverse={value:key for key,value in STATES.items()}
    if not isinstance(status,str) or (status not in inverse and status!='TASK_STATE_UNSPECIFIED'):raise Rejected('INVALID_A2A_STATUS_FILTER')
    after=timestamp_lower_bound(params['statusTimestampAfter']) if 'statusTimestampAfter' in params else None
    scope=digest({'peer':peer,'context':context,'status':status,'after':after,'includeArtifacts':include,'historyLength':history})
    token=params.get('pageToken','')
    cursor=_cursor(protocol.engine,scope,token) if token else None
    if not isinstance(token,str):raise Rejected('INVALID_A2A_PAGE_TOKEN')
    where=['peer=?'];values=[peer]
    if context:where.append('context_id=?');values.append(context)
    if status!='TASK_STATE_UNSPECIFIED':
        states=[key for key,value in STATES.items() if value==status]
        where.append('state IN ('+','.join('?' for _ in states)+')');values.extend(states)
    if after is not None:where.append('updated>=?');values.append(after)
    base=' AND '.join(where)
    with protocol.engine.tx() as db:
        total=db.execute('SELECT count(*) FROM a2a_tasks WHERE '+base,values).fetchone()[0]
        if cursor:
            where.append('(updated<? OR (updated=? AND id<?))');values.extend([cursor['updated'],cursor['updated'],cursor['id']])
        rows=[dict(row) for row in db.execute('SELECT * FROM a2a_tasks WHERE '+' AND '.join(where)+' ORDER BY updated DESC,id DESC LIMIT ?',(*values,limit+1))]
    tasks=[];last=None
    for row in rows[:limit]:
        task=task_document(row,int(protocol.engine.clock()))
        # This server retains artifacts and status, not message history. The
        # protocol permits fewer history messages than the requested maximum.
        task.pop('history',None)
        if include:task.setdefault('artifacts',[])
        else:task.pop('artifacts',None)
        trial={'tasks':tasks+[task],'nextPageToken':'x'*600,'pageSize':limit,'totalSize':total}
        if len(canonical(trial))>MAX_RESULT:
            if not tasks:raise Rejected('A2A_TASK_DOCUMENT_TOO_LARGE')
            break
        tasks.append(task);last=row
    next_token=_token(protocol.engine,scope,last) if last and len(rows)>len(tasks) else ''
    return {'tasks':tasks,'nextPageToken':next_token,'pageSize':limit,'totalSize':total}
