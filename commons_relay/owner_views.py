"""Bounded owner-channel views; never truncate an approval's arguments silently.

Large file results and skill schemas do not belong in a status packet. These
views stay below the encrypted owner channel's response budget and explicitly
report pagination and omitted detail. The normal task engine remains authoritative.
"""
from __future__ import annotations
from dataclasses import asdict
from .codec import Rejected, canonical, identifier

MAX_RESPONSE_BYTES = 12000
TASK_FIELDS = ('id', 'skill', 'state', 'phase', 'maximum_spend', 'asset',
               'intent_hash', 'created', 'updated', 'deadline', 'error')


def task_summary(task: dict) -> dict:
    result = {key: task.get(key) for key in TASK_FIELDS}
    if isinstance(result.get('error'), str):
        result['error'] = result['error'][:200]
    return result


def snapshot(engine, offset: int = 0) -> dict:
    if type(offset) is not int or not 0 <= offset <= 100000:
        raise Rejected('INVALID_OWNER_PAGE')
    with engine.tx() as db:
        now = engine.now(db)
        engine._expire(db, now)
        count = db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
        pending = db.execute("SELECT COUNT(*) FROM tasks WHERE state='input-required'").fetchone()[0]
        rows = db.execute('''SELECT * FROM tasks ORDER BY
            CASE WHEN state='input-required' THEN 0
                 WHEN state IN ('submitted','working','unknown') THEN 1 ELSE 2 END,
            created DESC, rowid DESC LIMIT 8 OFFSET ?''', (offset,)).fetchall()
        tasks = [task_summary(engine._view(row)) for row in rows]
        spend = str(engine._usage(db, 'LEZ-testnet', now))
    result = {
        'owner_snapshot': True, 'agent_id': engine.agent,
        'policy': asdict(engine.policy), 'reserved_and_recent_spend': spend,
        'tasks': tasks, 'task_count': count, 'approval_count': pending,
        'offset': offset, 'next_offset': offset + len(tasks),
        'has_more': offset + len(tasks) < count,
        'skill_count': len(engine.registry.describe()),
        'network': 'LEZ-testnet',
    }
    if len(canonical(result)) > MAX_RESPONSE_BYTES:
        raise Rejected('OWNER_SNAPSHOT_LIMIT')
    return result


def skills_page(engine, offset: int = 0) -> dict:
    if type(offset) is not int or not 0 <= offset <= 100000:
        raise Rejected('INVALID_OWNER_PAGE')
    all_skills = engine.registry.describe()
    rows = [{'id': item['id'], 'description': item['description'][:300],
             'argument_names': item['argument_names']} for item in all_skills[offset:offset + 16]]
    result = {'owner_skills': True, 'agent_id': engine.agent, 'skills': rows,
              'skill_count': len(all_skills), 'offset': offset,
              'next_offset': offset + len(rows), 'has_more': offset + len(rows) < len(all_skills)}
    if len(canonical(result)) > MAX_RESPONSE_BYTES:
        raise Rejected('OWNER_SKILL_PAGE_LIMIT')
    return result


def skill_details(engine, name: str) -> dict:
    identifier(name)
    engine.registry.get(name)
    description = next(row for row in engine.registry.describe() if row['id'] == name)
    result = {'owner_skill': True, 'agent_id': engine.agent, 'skill': description}
    if len(canonical(result)) > MAX_RESPONSE_BYTES:
        raise Rejected('OWNER_SKILL_SCHEMA_TOO_LARGE')
    return result


def task_details(engine, task_id: str) -> dict:
    identifier(task_id)
    task = engine.get(task_id, requester=engine.owner)
    item = task_summary(task)
    arguments = task.get('arguments', {})
    item['arguments_complete'] = len(canonical(arguments)) <= 6000
    if item['arguments_complete']:
        item['arguments'] = arguments
    result_json = canonical(task.get('result')).decode('utf-8')
    item['result_complete'] = len(result_json.encode('utf-8')) <= 4000
    # A preview is explicitly a string, not a fabricated partial result object.
    item['result_preview'] = result_json if item['result_complete'] else result_json[:900] + '\n[Preview truncated; inspect the local task record for the complete result.]'
    item['policy_version'] = engine.policy.version
    result = {'owner_task': True, 'agent_id': engine.agent, 'task': item}
    if len(canonical(result)) > MAX_RESPONSE_BYTES:
        raise Rejected('OWNER_TASK_DETAIL_LIMIT')
    return result


def inbox_page(mailbox, after: int = 0) -> dict:
    """Read owner replies without adding transport depth to their signed payload.

    Payload JSON is data, not executable source. A large historical record is
    explicitly marked as omitted rather than blocking access to all newer replies.
    """
    import json
    if type(after) is not int or after < 0:
        raise Rejected('INVALID_INBOX_CURSOR')
    with mailbox.guard:
        rows = mailbox.db.execute("SELECT rowid,body FROM inbox WHERE rowid>? AND kind='owner-result' ORDER BY rowid LIMIT 20", (after,)).fetchall()
    messages = []; used = 64
    for cursor, raw in rows:
        body = json.loads(raw)
        payload = json.dumps(body.get('payload', {}), separators=(',', ':'), ensure_ascii=True)
        item = {'cursor': cursor, 'kind': 'owner-result', 'sender': body['sender']}
        if len(payload.encode()) > 30000:
            item.update({'payload_omitted': True, 'payload_bytes': len(payload.encode())})
        else:
            item['payload_json'] = payload
        size = len(canonical(item)) + 1
        if used + size > 48000:
            break
        messages.append(item); used += size
    result = {'messages': messages, 'owner_inbox': True}
    canonical({'id': 'transport-id', 'success': True, 'result': result})
    return result
