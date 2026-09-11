"""Framework-neutral A2A client using a running Logos Core, not an HTTP server.

Persist each request_id before submitting work. A timeout means pending, not
failed, and the same request_id can be queried or re-enqueued without duplication.
This client never signs a wallet transfer; paid execution uses agent.task and its
owner-authorized spending policy, or explicit payment-extension negotiation.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Iterator
from .a2a_types import BINDING_EXTENSION, PAYMENT_EXTENSION
from .codec import Rejected, canonical, identifier
from .control import CoreClient


@dataclass
class PendingRequest(TimeoutError):
    request_id: str
    def __str__(self):
        return f'Request {self.request_id} is pending. Resume it; do not create a replacement request.'


class RemoteError(RuntimeError):
    def __init__(self, error: dict):
        self.code = error.get('code')
        self.message = error.get('message', 'Remote A2A request failed')
        super().__init__(f'A2A error {self.code}: {self.message}')


class LogosA2AClient:
    """Use this object from Python, a framework tool, or an async to_thread call.

    The Core session is on this machine. Its Messaging module supplies network
    transport and its persistent journal supplies encrypted retries and recovery.
    No private key is read by this adapter.
    """
    def __init__(self, logosctl: str | Path, session: str | Path, *, timeout: int = 30,
                 environment: dict | None = None):
        self.core = CoreClient(Path(logosctl), Path(session), timeout=timeout,
                               environment=environment)

    def discover(self, topic: str = 'commons', *, wait_seconds: float = 5) -> list[dict]:
        if not isinstance(topic, str) or not 1 <= len(topic) <= 128:
            raise ValueError('Topic must contain 1 to 128 characters.')
        if not 0 <= wait_seconds <= 30:
            raise ValueError('Discovery wait must be between 0 and 30 seconds.')
        self.core.request('a2a.client.discover', {'topic': topic, 'offset': 0, 'refresh': True})
        if wait_seconds: time.sleep(wait_seconds)
        cards, seen, offset = [], set(), 0
        while offset <= 1000:
            page = self.core.request('a2a.client.discover',
                                     {'topic': topic, 'offset': offset, 'refresh': False})
            for item in page['cards']:
                if item['address'] not in seen:
                    cards.append(item); seen.add(item['address'])
            if not page['has_more']: return cards
            next_offset = page['next_offset']
            if type(next_offset) is not int or next_offset <= offset:
                raise Rejected('A2A_DIRECTORY_CURSOR_NOT_ADVANCING')
            offset = next_offset
        raise Rejected('A2A_DIRECTORY_LIMIT')

    def import_card(self, signed_card: dict) -> str:
        """Verify and remember a public service card; never pin an owner contact."""
        return self.core.request('a2a.client.card', {'card': signed_card})['address']

    def card(self, peer: str) -> dict:
        return self.core.request('a2a.client.verified_card', {'peer': identifier(peer)})['card']

    def request(self, peer: str, method: str, params: dict, *, request_id: str) -> str:
        """Queue one A2A RPC. Save and reuse the caller-supplied id on uncertainty."""
        identifier(request_id); identifier(peer)
        if not isinstance(params, dict): raise TypeError('params must be a dictionary')
        canonical(params)
        value = self.core.request('a2a.client.request',
            {'peer': peer, 'method': method, 'params': params, 'request_id': request_id})
        if value.get('request_id') != request_id: raise Rejected('A2A_REQUEST_ID_MISMATCH')
        return request_id

    def response(self, request_id: str) -> dict | None:
        return self.core.request('a2a.client.response',
                                 {'request_id': identifier(request_id)})['response']

    def wait(self, request_id: str, *, timeout: float = 60) -> dict:
        if not 0 < timeout <= 7200: raise ValueError('Timeout must be between 0 and 7200 seconds.')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            reply = self.response(request_id)
            if reply is not None:
                if reply.get('jsonrpc') != '2.0' or reply.get('id') != request_id:
                    raise Rejected('INVALID_A2A_RESPONSE')
                if 'error' in reply: raise RemoteError(reply['error'])
                if 'result' not in reply: raise Rejected('INVALID_A2A_RESPONSE')
                return reply['result']
            time.sleep(min(.5, max(0, deadline - time.monotonic())))
        raise PendingRequest(request_id)

    def send_free_task(self, peer: str, skill: str, arguments: dict, *, request_id: str,
                       streaming: bool = False) -> str:
        """A convenience for free skills; paid prices are rejected before sending.

        The provider still enforces its current price. A changed price may produce
        a remote error or a quote, but this client cannot broadcast a payment.
        """
        card = self.card(peer)
        ext = next((e for e in card.get('capabilities', {}).get('extensions', [])
                    if e.get('uri') == PAYMENT_EXTENSION), {})
        if ext.get('params', {}).get('prices', {}).get(skill) != '0':
            raise Rejected('A2A_FREE_CLIENT_REQUIRES_ZERO_PRICE')
        params = {'message': {'messageId': identifier(request_id), 'role': 'ROLE_USER',
            'parts': [{'data': {'skill': skill, 'arguments': arguments, 'refundAddress': None},
                       'mediaType': 'application/json'}], 'extensions': [PAYMENT_EXTENSION]},
            'configuration': {'returnImmediately': True, 'acceptedOutputModes': ['application/json']}}
        return self.request(peer, 'SendStreamingMessage' if streaming else 'SendMessage',
                            params, request_id=request_id)

    def events(self, request_id: str, *, after: int = 0) -> dict:
        page = self.core.request('a2a.client.events',
                                 {'request_id': identifier(request_id), 'after': after})
        if page.get('format') == 'single-stream-response-v1':
            kinds = [key for key in ('task', 'statusUpdate', 'artifactUpdate') if key in page]
            if len(kinds) > 1: raise Rejected('INVALID_A2A_EVENT')
            events = ([{'sequence': page['sequence'], 'response': {kinds[0]: page[kinds[0]]}}]
                      if kinds else [])
            return {'request_id': page['request_id'], 'events': events,
                    'next_after': page['next_after'], 'waiting_for_gap': page['waiting_for_gap']}
        return page

    @staticmethod
    def _stream_finished(response: dict) -> bool:
        update = response.get('statusUpdate', response.get('task', {}))
        return update.get('status', {}).get('state') in {
            'TASK_STATE_COMPLETED', 'TASK_STATE_FAILED', 'TASK_STATE_REJECTED',
            'TASK_STATE_CANCELED', 'TASK_STATE_INPUT_REQUIRED', 'TASK_STATE_AUTH_REQUIRED'}

    def stream(self, request_id: str, *, after: int = 0, timeout: float = 120) -> Iterator[dict]:
        """Yield standard A2A StreamResponses with an outer resumable cursor.

        Keep each yielded `sequence` to resume. A timeout never cancels the task.
        The initial task is a snapshot; later events begin after that snapshot's
        sequence, not necessarily at one. Missing events are not silently skipped.
        """
        if type(after) is not int or after < 0: raise ValueError('Invalid stream cursor')
        initial = self.wait(request_id, timeout=min(timeout, 60))
        task = initial.get('task', initial)
        base = int(task.get('metadata', {}).get(BINDING_EXTENSION, {}).get('sequence', '0'))
        cursor = max(after, base)
        if after == 0:
            yield {'sequence': base, 'response': {'task': task}}
        if self._stream_finished({'task': task}):
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            page = self.events(request_id, after=cursor)
            for event in page['events']:
                cursor = event['sequence']; yield event
                response = event['response']
                if self._stream_finished(response): return
            cursor = page['next_after']
            if not page['events'] and cursor > base and not page.get('waiting_for_gap'):
                # The caller may have persisted the terminal cursor before a
                # disconnect. Inspect that exact event without yielding it twice
                # or creating another request. A2A 1.0 closes on terminal state;
                # it does not require the older `final` flag.
                previous = self.events(request_id, after=cursor - 1)
                if any(event['sequence'] == cursor and self._stream_finished(event['response'])
                       for event in previous['events']):
                    return
            time.sleep(.5)
        raise PendingRequest(request_id)
