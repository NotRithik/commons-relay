"""Local Core IPC adapter for external A2A clients, not a public HTTP endpoint.

Only A2A operations cross this boundary. It never initiates a wallet transfer,
changes owner settings or grants a remote peer access to private local tools.
Paid requests negotiate the declared payment extension; autonomous settlement
continues to use the owner-authorized agent.task engine path.
"""
from __future__ import annotations
import json
from .codec import Rejected, canonical, identifier
from .a2a_types import BINDING_EXTENSION

METHODS = frozenset({'SendMessage', 'SendStreamingMessage', 'GetTask', 'ListTasks',
                     'CancelTask', 'SubscribeToTask', 'GetExtendedAgentCard',
                     'CreateTaskPushNotificationConfig', 'GetTaskPushNotificationConfig',
                     'ListTaskPushNotificationConfigs', 'DeleteTaskPushNotificationConfig'})


def handle(service, method, params):
    protocol = service.get_agent_protocol()
    service.get_controller().start()
    if method == 'a2a.client.card' and set(params) == {'card'}:
        card=params['card']
        if not isinstance(card,dict):raise Rejected('INVALID_A2A_CARD_FIELDS')
        interfaces=card.get('supportedInterfaces',[])
        if not isinstance(interfaces,list):raise Rejected('INVALID_A2A_CARD_FIELDS')
        routes=[i.get('url') for i in interfaces if isinstance(i,dict) and i.get('protocolBinding')=='LOGOS-MESSAGING']
        if len(routes)!=1 or not isinstance(routes[0],str) or not routes[0].startswith('logos://'):raise Rejected('INVALID_A2A_INTERFACE')
        address=identifier(routes[0][8:])
        protocol.remember_card(address,card)
        return {'address':address,'verified':True}
    if method == 'a2a.client.verified_card' and set(params)=={'peer'}:
        # Return the original signed document, not a protobuf reserialization
        # that may omit explicitly false/default fields in its signature input.
        address=identifier(params['peer']);protocol.verified_card(address)
        with service.engine.tx() as db:row=db.execute('SELECT card FROM a2a_cards WHERE address=?',(address,)).fetchone()
        return {'card':json.loads(row['card'])}
    if method == 'a2a.client.discover' and set(params) == {'topic', 'offset', 'refresh'}:
        protocol.discovery_topic(params['topic'])
        if type(params['offset']) is not int or not 0 <= params['offset'] <= 1000 or type(params['refresh']) is not bool:
            raise Rejected('INVALID_DIRECTORY_REQUEST')
        if params['refresh']: protocol.discovery.query(params['topic'])
        entries = protocol.cards(params['topic'])
        page = []; index = params['offset']
        for item in entries[index:]:
            if len(canonical(page + [item])) > 12000:
                if not page: raise Rejected('AGENT_CARD_TOO_LARGE_FOR_CLIENT_PAGE')
                break
            page.append(item); index += 1
            if len(page) == 8: break
        return {'cards': page, 'next_offset': index, 'has_more': index < len(entries)}
    if method == 'a2a.client.request' and set(params) == {'peer', 'method', 'params', 'request_id'}:
        identifier(params['peer']); identifier(params['request_id'])
        if not isinstance(params['method'], str) or params['method'] not in METHODS or not isinstance(params['params'], dict):
            raise Rejected('INVALID_A2A_CLIENT_REQUEST')
        if len(canonical(params['params'])) > 12000: raise Rejected('A2A_CLIENT_REQUEST_TOO_LARGE')
        request_id = protocol.request(params['peer'], params['method'], params['params'], id=params['request_id'])
        return {'request_id': request_id, 'queued': True}
    if method in ('a2a.client.response', 'a2a.client.events'):
        fields = {'request_id'} if method == 'a2a.client.response' else {'request_id', 'after'}
        if set(params) != fields: raise Rejected('INVALID_A2A_CLIENT_REQUEST')
        request_id = identifier(params['request_id'])
        with service.engine.tx() as db:
            request = db.execute('SELECT response FROM a2a_client_requests WHERE id=?', (request_id,)).fetchone()
        if not request: raise Rejected('A2A_CLIENT_REQUEST_NOT_FOUND')
        if method == 'a2a.client.response':
            return {'request_id': request_id, 'response': json.loads(request['response']) if request['response'] else None}
        after = params['after']
        if type(after) is not int or not 0 <= after <= 2**53 - 1: raise Rejected('INVALID_A2A_EVENT_CURSOR')
        with service.engine.tx() as db:
            rows = db.execute('SELECT sequence,response FROM a2a_client_events WHERE request_id=? AND sequence>? ORDER BY sequence LIMIT 16', (request_id, after)).fetchall()
        # A subscription starts at the initial task snapshot, not at sequence 0.
        initial=json.loads(request['response']) if request['response'] else {}
        first=initial.get('result',{}).get('task',{})
        base=int(first.get('metadata',{}).get(BINDING_EXTENSION,{}).get('sequence','0'))
        if after and after<base:raise Rejected('A2A_EVENT_CURSOR_BEFORE_SUBSCRIPTION')
        cursor = max(after, base)
        answer = {'format': 'single-stream-response-v1', 'request_id': request_id,
                  'next_after': cursor, 'waiting_for_gap': False}
        # Return one intact standard event at the IPC result's top level.
        # A page/list/response wrapper adds artificial nesting to valid events.
        # No encoding or relaxation of canonical message bounds is involved.
        for row in rows:
            if row['sequence'] <= cursor: continue
            if row['sequence'] != cursor + 1:
                answer['waiting_for_gap'] = True
                break
            response = json.loads(row['response'])
            if not isinstance(response, dict) or len(response) != 1 or not set(response) <= {'task', 'statusUpdate', 'artifactUpdate'}:
                raise Rejected('INVALID_STORED_A2A_EVENT')
            answer.update(response)
            answer['sequence'] = row['sequence']
            answer['next_after'] = row['sequence']
            break
        if len(canonical({'id': '0' * 36, 'success': True, 'result': answer})) > 60000:
            raise Rejected('A2A_EVENT_TOO_LARGE_FOR_CLIENT')
        return answer
    raise Rejected('A2A_CLIENT_METHOD_DENIED')
