"""Permissionless, bounded discovery on Logos topics, separate from owner trust.

Announcements bind the signed Agent Card, Storage address, topic and expiration.
Queries make late joining useful without a centralized index or a minute's wait.
No executable or arbitrary URL is fetched as a result of an advertisement.
"""
from __future__ import annotations
import hashlib
import secrets
import threading
import time
from .codec import Rejected, canonical, parse, identifier, digest
from .signing import sign_envelope, verify_envelope
from .a2a_types import BINDING_EXTENSION, verify_card
from .service_identity import contact_from_card, validate_public_contact

DOMAIN = 'commons/relay/public-discovery/v1'
TTL = 300
MAX_ADVERTISEMENT = 14000
MAX_CARDS = 256


class PublicDiscovery:
    def __init__(self, protocol):
        self.protocol = protocol
        self.engine = protocol.engine
        self.mailbox = protocol.mailbox
        self.guard = threading.RLock()
        self.topics = set()
        self.pending = False
        self.last_publish = 0.0
        self.published_hash = None
        self.rate_window = -1
        self.rate_count = 0
        self.query_seen = {}
        with self.engine.tx() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS public_agent_cards(
                topic TEXT NOT NULL,address TEXT NOT NULL,card TEXT NOT NULL,
                storage_cid TEXT NOT NULL,issued INTEGER NOT NULL,expires INTEGER NOT NULL,
                received INTEGER NOT NULL,PRIMARY KEY(topic,address))''')

    def _body(self, kind, name):
        now = int(self.engine.clock())
        return {'domain': DOMAIN, 'kind': kind, 'topic': name, 'issued': now,
                'expires': now + TTL, 'sender': self.mailbox.public_contact().public()}

    def _signed(self, body):
        return sign_envelope(body, self.mailbox.vault.signing_private, self.mailbox.vault.signer)

    def subscribe(self, name):
        topic = self.protocol.discovery_topic(name)
        with self.guard:
            if name in self.topics:
                return topic
            if len(self.topics) >= 16:
                raise Rejected('DISCOVERY_TOPIC_LIMIT')
            self.protocol.runtime.discovery_handlers[topic] = lambda raw: self.receive(name, raw)
            self.protocol.service.bridge.call('delivery.subscribe', {'topic': topic})
            self.topics.add(name)
        return topic

    def query(self, name):
        topic = self.subscribe(name)
        body = self._body('query', name)
        body['nonce'] = secrets.token_hex(16)
        self.protocol.service.bridge.call('delivery.publish-card',
            {'topic': topic, 'payload': canonical(self._signed(body)).decode()})

    def announce(self):
        with self.protocol.guard:
            config = self.protocol.config
            if not config.get('public', False):
                return False
            name = config['discovery_topic']
            card = self.protocol.card(public=True)
        topic = self.subscribe(name)
        cid = self.protocol.publish_storage_card(public=True, snapshot=card)
        # A listing edited while Storage was busy must not be announced as fresh.
        with self.protocol.guard:
            if self.protocol.config != config:
                self.pending = True
                return False
        body = self._body('agent-card', name)
        body['card'], body['storageCid'] = card, cid
        raw = canonical(self._signed(body))
        if len(raw) > MAX_ADVERTISEMENT:
            raise Rejected('PUBLIC_AGENT_CARD_TOO_LARGE')
        self.protocol.service.bridge.call('delivery.publish-card', {'topic': topic, 'payload': raw.decode()})
        with self.guard:
            self.last_publish = time.monotonic()
            self.published_hash = digest(card)
            self.pending = self.protocol.config != config
        return True

    def receive(self, name, raw):
        if not isinstance(raw, str) or len(raw.encode()) > MAX_ADVERTISEMENT:
            raise Rejected('DISCOVERY_MESSAGE_TOO_LARGE')
        # Bound expensive signature checks globally; per-identity limits alone
        # do not protect a permissionless network from fresh-key spam.
        now = int(self.engine.clock())
        with self.guard:
            if self.rate_window != now // 60:
                self.rate_window, self.rate_count = now // 60, 0
            self.rate_count += 1
            if self.rate_count > 120:
                raise Rejected('DISCOVERY_RATE_LIMIT')
        envelope = parse(raw.encode())
        if not isinstance(envelope, dict) or not isinstance(envelope.get('body'), dict):
            # Legacy cards are handled separately by the protocol, only for pins.
            raise Rejected('SIGNED_DISCOVERY_REQUIRED')
        from .messaging import Contact
        sender = validate_public_contact(Contact.from_public(envelope['body'].get('sender')))
        body = verify_envelope(envelope, sender.signing_key, self.mailbox.vault.signer)
        kind = body.get('kind')
        fields = {'domain', 'kind', 'topic', 'issued', 'expires', 'sender'}
        fields |= {'card', 'storageCid'} if kind == 'agent-card' else {'nonce'}
        if set(body) != fields or body['domain'] != DOMAIN or body['topic'] != name or kind not in {'agent-card', 'query'}:
            raise Rejected('DISCOVERY_BINDING_MISMATCH')
        if type(body['issued']) is not int or type(body['expires']) is not int or not body['issued'] <= now + 30 or not now < body['expires'] <= body['issued'] + TTL:
            raise Rejected('DISCOVERY_EXPIRED')
        if sender.address == self.mailbox.public_contact().address:
            return
        if kind == 'query':
            identifier(body['nonce'])
            if name != self.protocol.config['discovery_topic']:
                return
            with self.guard:
                previous = self.query_seen.get(sender.address, 0)
                if now - previous >= 10:
                    if len(self.query_seen) >= 256:
                        self.query_seen = {k: v for k, v in self.query_seen.items() if v > now - 60}
                    if len(self.query_seen) < 256:
                        self.query_seen[sender.address] = now
                        self.pending = True
            return
        contact = contact_from_card(body['card'])
        if contact.address != sender.address:
            raise Rejected('DISCOVERY_CARD_SIGNER_MISMATCH')
        value = verify_card(body['card'], contact.signing_key, self.mailbox.vault.signer)
        binding = next(e for e in value['capabilities']['extensions'] if e['uri'] == BINDING_EXTENSION)
        if binding['params'].get('discoveryTopic') != name:
            raise Rejected('DISCOVERY_CARD_TOPIC_MISMATCH')
        cid = body['storageCid']
        if not isinstance(cid, str) or not 20 <= len(cid) <= 180 or not cid.isalnum():
            raise Rejected('INVALID_CARD_STORAGE_ADDRESS')
        with self.engine.tx() as db:
            old = db.execute('SELECT issued FROM public_agent_cards WHERE topic=? AND address=?', (name, contact.address)).fetchone()
            if old and old['issued'] >= body['issued']:
                return  # Replaying a signed ad never refreshes its lease.
            db.execute('DELETE FROM public_agent_cards WHERE expires<=?', (now,))
            if not old and db.execute('SELECT COUNT(*) FROM public_agent_cards').fetchone()[0] >= MAX_CARDS:
                raise Rejected('DISCOVERY_DIRECTORY_FULL')
            db.execute('INSERT INTO public_agent_cards VALUES (?,?,?,?,?,?,?) ON CONFLICT(topic,address) DO UPDATE SET card=excluded.card,storage_cid=excluded.storage_cid,issued=excluded.issued,expires=excluded.expires,received=excluded.received',
                       (name, contact.address, canonical(body['card']).decode(), cid, body['issued'], body['expires'], now))
        self.mailbox.remember_service_contact(contact)
        self.protocol.remember_card(contact.address, body['card'])

    def cards(self, name):
        import json
        with self.engine.tx() as db:
            rows = db.execute('SELECT * FROM public_agent_cards WHERE topic=? AND expires>? ORDER BY address LIMIT ?',
                              (name, int(self.engine.clock()), MAX_CARDS)).fetchall()
        return [{'address': r['address'], 'card': json.loads(r['card']), 'received': r['received'],
                 'expires': r['expires'], 'storageCid': r['storage_cid'], 'topic': r['topic'],
                 'trust': 'public-service', 'identityVerified': True} for r in rows]

    def tick(self):
        with self.guard:
            due = time.monotonic() - self.last_publish
            publish = self.protocol.config.get('public', False) and (due > 60 or self.pending and due > 3)
        if publish:
            self.announce()
