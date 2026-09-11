#!/usr/bin/env python3
"""A real LangChain runnable pipeline over Logos A2A, with a durable request ID.

This example deliberately calls only the free meta.skills service. It does not
load a model, a wallet key, or an external tracing service. Run it again with the
same journal to resume the same remote task, not create replacement work.
"""
from __future__ import annotations
import argparse
import fcntl
from importlib.metadata import version
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time

# Tracing would disclose intermediate agent inputs/results. This example opts out
# before importing the framework; applications must obtain consent to enable it.
os.environ['LANGCHAIN_TRACING_V2'] = 'false'
os.environ['LANGSMITH_TRACING'] = 'false'
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from commons_relay.a2a_client import LogosA2AClient, PendingRequest
from commons_relay.a2a_types import PAYMENT_EXTENSION
from commons_relay.codec import Rejected, identifier
from langchain_core.runnables import RunnableLambda

TERMINAL = {'TASK_STATE_COMPLETED', 'TASK_STATE_FAILED', 'TASK_STATE_REJECTED', 'TASK_STATE_CANCELED'}


class Journal:
    def __init__(self, path: Path, intent: dict):
        self.path = path.expanduser().absolute()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink(): raise ValueError('Journal must not be a symlink.')
        lock_path = self.path.with_suffix(self.path.suffix + '.lock')
        self.lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if self.path.exists():
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 1000000:
                raise ValueError('Journal must be an owner-only regular file below 1 MB.')
            self.value = json.loads(self.path.read_text())
            if self.value.get('intent') != intent: raise ValueError('Use the original journal arguments when resuming.')
        else:
            self.value = {'schema': 1, 'intent': intent, 'created_at': int(time.time()), 'poll': 0}
            self.save()

    def save(self, **updates):
        self.value.update(updates)
        fd, raw = tempfile.mkstemp(prefix='.a2a-journal-', dir=self.path.parent)
        temporary = Path(raw)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(self.value, stream, ensure_ascii=False, allow_nan=False, indent=2)
                stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            if temporary.exists(): temporary.unlink()

    def close(self):
        os.close(self.lock_fd)


def make_pipeline(client: LogosA2AClient, journal: Journal, timeout: float):
    """Returns composable framework Runnables, not a second agent runtime."""
    def discover(intent):
        if journal.value.get('peer'):
            return journal.value['peer']
        candidates = client.discover(intent['topic'], wait_seconds=5)
        matches = []
        for entry in candidates:
            card = entry['card']
            if card['name'] != intent['provider_name']: continue
            if entry.get('trust') != 'public-service': continue
            extension = next((x for x in card['capabilities'].get('extensions', [])
                              if x.get('uri') == PAYMENT_EXTENSION), {})
            if extension.get('params', {}).get('prices', {}).get('meta.skills') == '0':
                matches.append(entry)
        if len(matches) != 1:
            raise ValueError('Expected one live, public provider with a free meta.skills service; found ' + str(len(matches)))
        peer = matches[0]['address']
        journal.save(peer=peer, discovery={'topic': intent['topic'], 'name': intent['provider_name'],
                                         'trust': matches[0]['trust'], 'observed_at': int(time.time())})
        print('Discovered public service:', intent['provider_name'], flush=True)
        return peer

    def call(peer):
        if journal.value.get('result'): return journal.value['result']
        intent = journal.value['intent']; rid = intent['request_id']
        if not journal.value.get('remote_task_id'):
            try: response = client.response(rid)
            except Rejected as error:
                if str(error) != 'A2A_CLIENT_REQUEST_NOT_FOUND': raise
                # request_id was durably recorded before the first network call.
                client.send_free_task(peer, 'meta.skills', {}, request_id=rid)
                response = None
            accepted = client.wait(rid, timeout=timeout)
            task = accepted.get('task')
            if not isinstance(task, dict) or not isinstance(task.get('id'), str):
                raise ValueError('Provider did not return an A2A task.')
            journal.save(remote_task_id=task['id'], context_id=task['contextId'])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            counter = journal.value['poll'] + 1
            journal.save(poll=counter)
            query_id = identifier(rid + '-get-' + str(counter))
            client.request(peer, 'GetTask', {'id': journal.value['remote_task_id']}, request_id=query_id)
            task = client.wait(query_id, timeout=min(30, max(1, deadline - time.monotonic())))
            if task.get('id') != journal.value['remote_task_id'] or task.get('contextId') != journal.value['context_id']:
                raise ValueError('Returned task identity changed.')
            state = task.get('status', {}).get('state')
            if state in TERMINAL:
                journal.save(result=task, finished_at=int(time.time()))
                return task
            time.sleep(.5)
        raise PendingRequest(rid)

    def summarize(task):
        state = task.get('status', {}).get('state')
        if state != 'TASK_STATE_COMPLETED':
            raise ValueError('The remote task ended without a successful result: ' + str(state))
        values = [part['data'] for artifact in task.get('artifacts', []) for part in artifact.get('parts', [])
                  if isinstance(part.get('data'), dict)]
        skills = [skill['id'] for value in values for skill in value.get('skills', [])
                  if isinstance(skill, dict) and isinstance(skill.get('id'), str)]
        if not skills: raise ValueError('Completed task has no capability list.')
        report = {'framework': 'langchain-core', 'framework_version': version('langchain-core'),
                  'transport': 'LOGOS-MESSAGING', 'discovery': journal.value['discovery'],
                  'provider': journal.value['peer'], 'request_id': journal.value['intent']['request_id'],
                  'remote_task_id': task['id'], 'state': state, 'tool_count': len(skills), 'tools': skills,
                  'paid_amount': '0', 'model_calls': 0, 'tracing_enabled': False,
                  'steps': ['discover', 'call_and_wait', 'summarize'], 'observed_at': int(time.time())}
        journal.save(report=report)
        return report

    return (RunnableLambda(discover, name='discover_logos_provider')
            | RunnableLambda(call, name='invoke_logos_free_service')
            | RunnableLambda(summarize, name='summarize_real_service_result'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--logosctl', type=Path, required=True)
    p.add_argument('--session', type=Path, required=True)
    p.add_argument('--provider-name', default='Blockchain Agent')
    p.add_argument('--topic', default='commons')
    p.add_argument('--request-id', required=True)
    p.add_argument('--journal', type=Path, required=True)
    p.add_argument('--timeout', type=int, default=60)
    args = p.parse_args()
    if not 5 <= args.timeout <= 180: p.error('timeout must be 5..180 seconds')
    identifier(args.request_id)
    if len(args.request_id) > 55: p.error('request-id must be at most 55 characters')
    intent = {'request_id': args.request_id, 'topic': args.topic, 'provider_name': args.provider_name,
              'session': str(args.session.resolve()), 'skill': 'meta.skills', 'arguments': {}, 'price': '0'}
    journal = Journal(args.journal, intent)
    try:
        client = LogosA2AClient(args.logosctl, args.session, timeout=20)
        with __import__('langsmith').tracing_context(enabled=False):
            result = make_pipeline(client, journal, args.timeout).invoke(intent, config={'callbacks': []})
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally: journal.close()


if __name__ == '__main__':
    try: main()
    except (Rejected, PendingRequest, ValueError, OSError) as error:
        print('Pipeline stopped: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
