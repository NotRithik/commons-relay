"""Owner-managed service publishing and a bounded directory for the native UI."""
from __future__ import annotations
import json
import os
from pathlib import Path
import secrets
from .codec import Rejected, canonical, digest, identifier, amount
from .signing import verify_envelope
from .a2a_types import PAYMENT_EXTENSION

SETTINGS_DOMAIN = 'commons/relay/provider-settings/v1'


def _public_skills(service):
    result = []
    for entry in service.engine.registry.describe():
        name = entry['id']
        if name in {'meta.skills', 'program.query'} or (
                service.extensions.has(name) and service.extensions.specs[name].public):
            result.append({'id': name, 'description': entry['description']})
    return result


def status(service):
    protocol = service.get_agent_protocol()
    settings = {**protocol.config, 'public': protocol.config.get('public', False)}
    with service.engine.tx() as db:
        row = db.execute("SELECT value FROM metadata WHERE key='public-agent-card-storage'").fetchone()
    published = json.loads(row['value']) if row else {}
    current_hash = digest(protocol.card(public=True))
    is_current = published.get('hash') == current_hash and protocol.discovery.published_hash == current_hash
    return {'provider_status': True, 'agent_id': service.engine.agent,
            'settings': settings, 'settings_hash': digest(settings),
            'public_address': protocol.mailbox.public_contact().address,
            'available_services': _public_skills(service),
            'storage_cid': published.get('cid') if is_current else None,
            'publication_error': protocol.discovery_error,
            'publication_state': 'private' if not settings['public'] else
                'error' if protocol.discovery_error else 'published' if is_current else 'publishing'}


def configure(service, envelope):
    body = verify_envelope(envelope, service.engine.owner_key, service.engine.crypto)
    fields = {'domain', 'agent_id', 'request_id', 'settings', 'expected_hash', 'expires_at'}
    if set(body) != fields or body['domain'] != SETTINGS_DOMAIN or body['agent_id'] != service.engine.agent:
        raise Rejected('INVALID_PROVIDER_SETTINGS_BINDING')
    identifier(body['request_id'])
    now = int(service.engine.clock())
    if type(body['expires_at']) is not int or not now < body['expires_at'] <= now + 300:
        raise Rejected('PROVIDER_SETTINGS_EXPIRED')
    protocol = service.get_agent_protocol()
    settings = protocol.validate_config(body['settings'])
    settings = {**settings, 'public': settings.get('public', False)}
    if settings.get('allow_public_payments',False):protocol.public_receiver()
    with protocol.guard:
        current = {**protocol.config, 'public': protocol.config.get('public', False)}
        if body['expected_hash'] != digest(current) and digest(settings) != digest(current):
            raise Rejected('PROVIDER_SETTINGS_CHANGED_RELOAD')
        path = service.root / 'services.json'
        temporary = service.root / ('services-' + secrets.token_hex(8) + '.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(canonical(settings)); stream.flush(); os.fsync(stream.fileno())
            if path.is_symlink():
                raise Rejected('SERVICE_CONFIGURATION_PERMISSIONS')
            os.replace(temporary, path)
            directory = os.open(service.root, os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            if temporary.exists(): temporary.unlink()
        protocol.config = settings
        protocol.exports = set(settings['exports'])
        protocol.discovery.pending = True
        protocol.discovery_error = None
        if settings['public']:
            protocol.discovery.subscribe(settings['discovery_topic'])
    service.get_controller().start()
    return status(service)


def directory(service, topic, offset=0, refresh=False):
    if type(offset) is not int or not 0 <= offset <= 1000:
        raise Rejected('INVALID_DIRECTORY_PAGE')
    protocol = service.get_agent_protocol()
    protocol.discovery_topic(topic)
    service.get_controller().start()
    if refresh:
        protocol.discovery.query(topic)
    cards = protocol.cards(topic)
    entries = []
    for item in cards:
        card = item['card']
        extension = next((e for e in card.get('capabilities', {}).get('extensions', [])
                          if e.get('uri') == PAYMENT_EXTENSION), {})
        params = extension.get('params', {})
        prices, schemas = params.get('prices', {}), params.get('inputSchemas', {})
        skills = []
        for skill in card.get('skills', []):
            name = skill['id']
            price = prices.get(name)
            if price is None or not isinstance(schemas.get(name), dict): continue
            amount(price)
            skills.append({'id': name, 'name': skill.get('name', name),
                           'description': skill.get('description', ''), 'price': price,
                           'input_schema': schemas[name], 'payment_modes': params.get('paymentModes',['private'])})
        entries.append({'address': item['address'], 'name': card['name'],
                        'description': card['description'], 'trust': item.get('trust', 'owner-contact'),
                        'received': item['received'], 'expires': item.get('expires', item['received'] + 300),
                        'storage_cid': item.get('storageCid'), 'skills': skills})
    page = []
    for entry in entries[offset:offset + 8]:
        if len(canonical(page + [entry])) > 11000:
            if not page:raise Rejected('SERVICE_CARD_TOO_LARGE_FOR_DIRECTORY_PAGE')
            break
        page.append(entry)
    return {'service_directory': True, 'agent_id': service.engine.agent, 'topic': topic,
            'services': page, 'offset': offset, 'next_offset': offset + len(page),
            'has_more': offset + len(page) < len(entries), 'total': len(entries),
            'refreshing': refresh, 'observed_at': int(service.engine.clock())}
