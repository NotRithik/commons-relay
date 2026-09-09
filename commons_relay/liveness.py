"""Cheap, challenge-bound observations. No model, wallet, adapter or task execution.

A live supervisor is not proof of progress by its effect/prover subprocess. We
report phase age and heartbeat age separately and never fabricate percentages.
"""
from __future__ import annotations
import re
import time
from .codec import Rejected, canonical


def is_status_question(prompt):
    # Deliberately narrow: do not intercept substantive searches about "status".
    text = str(prompt).strip().lower().strip(' ?!.')
    return text in {'status', 'status update', 'what is the status', "what's the status",
                    'any update', 'ping', 'are you there', 'are you online'}


def task_observation(row, now, active=None):
    item = {key: row[key] for key in ('id', 'skill', 'state', 'phase')}
    item['age_seconds'] = max(0, now - row['created'])
    item['phase_age_seconds'] = max(0, now - row['updated'])
    item['maximum_spend'] = row['amount']
    item['heartbeat_age_seconds'] = max(0, now - row['heartbeat']) if row['heartbeat'] else None
    item['authorization_expired'] = row['deadline'] <= now
    phase, state = row['phase'], row['state']
    if state == 'submitted':
        item['detail'] = 'Queued behind another task' if active else 'Queued for execution'
        if active: item['blocked_by'] = active
    elif state == 'input-required': item['detail'] = 'Waiting for owner approval; not executing'
    elif state == 'unknown': item['detail'] = 'Outcome uncertain; reconcile the original operation, do not retry'
    elif phase == 'preparing' and row['skill'] in ('agent.task', 'wallet.send') and row['amount'] != '0':
        item['detail'] = 'Preparing private payment (may include proving); payment is not confirmed'
    elif phase == 'broadcasting': item['detail'] = 'Submission in progress; confirmation not established'
    else: item['detail'] = 'Running; no newer phase has been recorded'
    if state == 'working' and (item['heartbeat_age_seconds'] is None or item['heartbeat_age_seconds'] > 20):
        item['supervision'] = 'stale'
    elif state == 'working': item['supervision'] = 'alive; completion progress is unknown'
    return item


def observe(service):
    now = int(service.engine.clock())
    controller = service.controller
    active = getattr(controller, 'active', None)
    with service.engine.tx() as db:
        rows = db.execute("SELECT id,skill,state,phase,amount,created,updated,heartbeat,deadline FROM tasks WHERE state IN ('submitted','working','unknown','input-required') ORDER BY created DESC LIMIT 9").fetchall()
        counts = {row[0]: row[1] for row in db.execute("SELECT state,count(*) FROM tasks WHERE state IN ('submitted','working','unknown','input-required') GROUP BY state")}
    thread = getattr(controller, 'thread', None)
    tick = getattr(controller, 'last_tick', None)
    result = {'agent_id': service.engine.agent, 'observed_at': now,
              'controller_alive': bool(thread and thread.is_alive()),
              'controller_tick_age_seconds': round(max(0, time.monotonic() - tick), 1) if tick is not None else None,
              'active_effect': active, 'counts': counts,
              'tasks': [task_observation(row, now, active) for row in rows[:8]],
              'tasks_has_more': len(rows) > 8,
              'wallet_queried': False, 'model_called': False, 'tasks_created': 0}
    if len(canonical(result)) > 7000: raise Rejected('HEALTH_RESPONSE_LIMIT')
    return result


def pong(service, nonce):
    if not isinstance(nonce, str) or not re.fullmatch('[0-9a-f]{32}', nonce):
        raise Rejected('INVALID_HEARTBEAT_CHALLENGE')
    return {'owner_ping': True, 'nonce': nonce, **observe(service)}


def status_reply(service):
    health = observe(service)
    lines = ['Live agent status (no model call or new task):']
    if not health['tasks']:
        lines.append('No pending tasks are recorded.')
    for task in health['tasks'][:4]:
        lines.append(f"{task['skill']}: {task['detail']}. Elapsed {task['age_seconds']}s; current phase {task['phase_age_seconds']}s. Task {task['id']}.")
    if health['tasks_has_more'] or len(health['tasks']) > 4: lines.append('More tasks are available in Activity.')
    lines.append('A heartbeat means the agent responds; it does not mean a payment or task completed.')
    return '\n'.join(lines)
