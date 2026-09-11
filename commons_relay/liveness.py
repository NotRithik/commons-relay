"""Cheap, challenge-bound observations. No model, wallet, adapter or task execution.

A live supervisor is not proof of progress by its effect/prover subprocess. We
report phase age and heartbeat age separately and never fabricate percentages.
"""
from __future__ import annotations
import json
import re
import time
from .codec import Rejected, canonical
from .result_plain import result_plain


def is_status_question(prompt):
    # Deliberately narrow: do not intercept substantive searches about "status".
    text = str(prompt).strip().lower().strip(' ?!.')
    return text in {'status', 'status update', 'what is the status', "what's the status",
                    'any update', 'ping', 'are you there', 'are you online'}


def task_observation(row, now, active=None):
    item = {key: row[key] for key in ('id', 'skill', 'state', 'phase')}
    if row['progress_stage']:
        item['progress']={'stage':row['progress_stage'],'detail':row['progress_detail'],'updated':row['progress_updated']}
        item['progress_age_seconds']=max(0,now-row['progress_updated'])
    item['age_seconds'] = max(0, now - row['created'])
    item['phase_age_seconds'] = max(0, now - row['updated'])
    item['maximum_spend'] = row['amount']
    item['heartbeat_age_seconds'] = max(0, now - row['heartbeat']) if row['heartbeat'] else None
    item['authorization_expired'] = row['deadline'] <= now
    phase, state = row['phase'], row['state']
    if row['progress_detail'] and state in ('working','unknown'):
        item['detail']=row['progress_detail']
    elif state == 'submitted':
        item['detail'] = 'Waiting for another task to finish' if active else 'Waiting to start'
        if active: item['blocked_by'] = active
    elif state == 'input-required': item['detail'] = 'Waiting for your approval. Nothing is running yet'
    elif state == 'unknown':
        item['detail'] = ('Relay could not reach the stored file yet. It will safely try the same download again; do not start another one.'
                          if row['skill']=='storage.download' else
                          'One or more group members have not confirmed this message yet. Relay is checking the same message; do not send it again.'
                          if row['skill']=='messaging.send' else
                          'The network result is unclear. Relay is checking the original action. Do not send it again')
    elif phase == 'preparing' and row['skill'] in ('agent.task', 'wallet.send') and row['amount'] != '0':
        waited = item.get('progress_age_seconds') if item.get('progress_age_seconds') is not None else item['phase_age_seconds']
        minutes = max(1, (waited + 59) // 60) if waited else 1
        item['detail'] = (f'Building a private payment proof on this device ({minutes} min so far). '
                          'This is local math and often takes 15 to 45 minutes. Nothing has been sent yet')
    elif phase == 'broadcasting': item['detail'] = 'Sending to the network. Waiting for confirmation'
    else: item['detail'] = 'Still working. No newer update has arrived yet'
    if state == 'working' and (item['heartbeat_age_seconds'] is None or item['heartbeat_age_seconds'] > 20):
        item['supervision'] = 'stale'
    elif state == 'working': item['supervision'] = 'alive; completion progress is unknown'
    return item


def recent_task_update(row, now):
    item={key:row[key] for key in ('id','skill','state','phase','updated','deadline','error')}
    item['maximum_spend']=row['amount']
    if row['progress_stage']:
        item['progress']={'stage':row['progress_stage'],'detail':row['progress_detail'],'updated':row['progress_updated']}
        item['progress_age_seconds']=max(0,now-row['progress_updated'])
    try:
        payload=json.loads(row['result']) if row['result'] else None
    except Exception:
        payload=None
    summary=result_plain(row['skill'], payload, state=row['state'], error=row['error'])
    if summary:
        item['result_summary']=summary[:400]
    return item


def observe(service):
    now = int(service.engine.clock())
    controller = service.controller
    active = getattr(controller, 'active', None)
    with service.engine.tx() as db:
        rows = db.execute("SELECT t.id,t.skill,t.state,t.phase,t.amount,t.created,t.updated,t.heartbeat,t.deadline,t.error,p.stage AS progress_stage,p.detail AS progress_detail,p.updated AS progress_updated FROM tasks t LEFT JOIN task_progress p ON p.task_id=t.id WHERE t.state IN ('submitted','working','unknown','input-required') ORDER BY t.created DESC LIMIT 9").fetchall()
        recent = db.execute("SELECT t.id,t.skill,t.state,t.phase,t.amount,t.updated,t.deadline,t.error,t.result,p.stage AS progress_stage,p.detail AS progress_detail,p.updated AS progress_updated FROM tasks t LEFT JOIN task_progress p ON p.task_id=t.id ORDER BY t.updated DESC,t.rowid DESC LIMIT 4").fetchall()
        counts = {row[0]: row[1] for row in db.execute("SELECT state,count(*) FROM tasks WHERE state IN ('submitted','working','unknown','input-required') GROUP BY state")}
    thread = getattr(controller, 'thread', None)
    tick = getattr(controller, 'last_tick', None)
    result = {'agent_id': service.engine.agent, 'observed_at': now,
              'controller_alive': bool(thread and thread.is_alive()),
              'controller_tick_age_seconds': int(max(0, time.monotonic() - tick)) if tick is not None else None,
              'active_effect': active, 'counts': counts,
              'tasks': [task_observation(row, now, active) for row in rows[:8]],
              'tasks_has_more': len(rows) > 8,
              'recent_task_updates':[recent_task_update(row,now) for row in recent],
              'wallet_queried': False, 'model_called': False, 'tasks_created': 0}
    if len(canonical(result)) > 7000: raise Rejected('HEALTH_RESPONSE_LIMIT')
    return result


def pong(service, nonce):
    if not isinstance(nonce, str) or not re.fullmatch('[0-9a-f]{32}', nonce):
        raise Rejected('INVALID_HEARTBEAT_CHALLENGE')
    return {'owner_ping': True, 'nonce': nonce, **observe(service)}


def status_reply(service):
    health = observe(service)
    lines = ['Here’s what your agent is doing right now:']
    if not health['tasks']:
        lines.append('Nothing is waiting or running.')
    for task in health['tasks'][:4]:
        try: label = service.engine.registry.get(task['skill']).description
        except Exception: label = task['skill']
        lines.append(f"{label}: {task['detail']}. Running for {task['age_seconds']}s; last changed {task['phase_age_seconds']}s ago.")
    if health['tasks_has_more'] or len(health['tasks']) > 4:
        lines.append('Open Activity to see the rest.')
    lines.append('The agent is responding right now. A task can still be waiting on another agent or the network.')
    return '\n'.join(lines)
