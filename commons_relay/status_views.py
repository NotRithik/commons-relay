"""Bounded observed agent status. Unknown balances are never reported as zero.

Status observes state; it does not transfer tokens, upload files or start a model.
Vault usage measures locally known encrypted-file references, not network capacity.
"""
from __future__ import annotations
import re
from .codec import Rejected, amount, canonical
from .owner_views import snapshot, skills_page


def error_code(error: Rejected) -> str:
    value = str(error)
    return value if re.fullmatch(r'[A-Z][A-Z0-9_]{0,99}', value) else 'STATUS_UNAVAILABLE'


def local_status(service) -> dict:
    """Cheap connection status without wallet/network/model calls."""
    result = snapshot(service.engine, 0)
    result.pop('owner_snapshot', None)
    page = skills_page(service.engine, 0)
    result['skills'] = page['skills']
    result['skills_has_more'] = page['has_more']
    result['skills_next_offset'] = page['next_offset']
    result['transport'] = 'Logos Core local IPC'
    result['state_store'] = 'SQLite WAL'
    result['wallet_funded'] = None
    result['wallet_status'] = 'not-queried'
    result['inference_enabled'] = bool(service.planner and service.planner.status()['enabled'])
    result['planner_active'] = service.planner.active if service.planner else None
    return result


def observed_status(service) -> dict:
    result = snapshot(service.engine, 0)
    result.pop('owner_snapshot', None)
    result['status_observation'] = True
    with service.engine.tx() as db:
        result['active_task_count'] = db.execute(
            "SELECT COUNT(*) FROM tasks WHERE state IN ('submitted','working','unknown','input-required')"
        ).fetchone()[0]
    result['wallet'] = {'status': 'unavailable', 'balance': None, 'asset': 'LEZ-testnet'}
    try:
        observed = service.get_wallet().invoke('balance', timeout=20)
        if not isinstance(observed, dict) or not isinstance(observed.get('balance'), str):
            raise Rejected('INVALID_WALLET_STATUS')
        amount(observed['balance'])
        block = observed.get('block')
        if type(block) is not int or block < 0:
            raise Rejected('INVALID_WALLET_STATUS')
        result['wallet'].update(status='observed', balance=observed['balance'], block=block)
    except Rejected as error:
        result['wallet']['error'] = error_code(error)
    result['storage_usage'] = {'status': 'unavailable',
                              'measurement': 'agent-known encrypted file references'}
    try:
        service.storage_adapter()
        usage = service.vault.usage()
        result['storage_usage'].update(usage, status='observed')
    except Rejected as error:
        result['storage_usage']['error'] = error_code(error)
    if len(canonical(result)) > 14000:
        raise Rejected('STATUS_RESPONSE_LIMIT')
    return result
