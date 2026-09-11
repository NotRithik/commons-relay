"""Self-certifying public service identities; deliberately not owner contacts.

The address commits to both transport keys. It authenticates a pseudonymous
service, not a legal name, an endorsement, or ownership of a claimed LEZ NPK.
Existing owner-pinned LEZ addresses and their pending tasks remain unchanged.
"""
from __future__ import annotations
import hashlib
import re
from .codec import Rejected, unb64

PUBLIC_PREFIX = 'relay-'
PUBLIC_KINDS = frozenset({'ack', 'agent-card', 'a2a-request', 'a2a-response', 'a2a-event'})
DOMAIN = b'commons-relay/public-service-identity/v1\0'


def public_address(signing_key: bytes, box_key: bytes) -> str:
    if len(signing_key) != 32 or len(box_key) != 32:
        raise Rejected('INVALID_PUBLIC_SERVICE_KEYS')
    return PUBLIC_PREFIX + hashlib.sha256(DOMAIN + signing_key + box_key).hexdigest()


def is_public_address(address: str) -> bool:
    return isinstance(address, str) and re.fullmatch(r'relay-[0-9a-f]{64}', address) is not None


def validate_public_contact(contact):
    if contact.address != public_address(contact.signing_key, contact.box_key):
        raise Rejected('PUBLIC_SERVICE_IDENTITY_MISMATCH')
    return contact


def contact_from_card(card: dict):
    # Import lazily to avoid coupling basic messaging to A2A dispatch.
    from .a2a_types import BINDING_EXTENSION
    from .messaging import Contact
    if not isinstance(card, dict):
        raise Rejected('INVALID_PUBLIC_SERVICE_CARD')
    capabilities = card.get('capabilities')
    if not isinstance(capabilities, dict):
        raise Rejected('INVALID_PUBLIC_SERVICE_CARD')
    extensions = capabilities.get('extensions')
    if not isinstance(extensions, list):
        raise Rejected('PUBLIC_SERVICE_BINDING_REQUIRED')
    bindings = [e for e in extensions if isinstance(e, dict) and e.get('uri') == BINDING_EXTENSION]
    if len(bindings) != 1 or not isinstance(bindings[0].get('params'), dict):
        raise Rejected('PUBLIC_SERVICE_BINDING_REQUIRED')
    params = bindings[0]['params']
    name = card.get('name')
    if not isinstance(name, str) or not 1 <= len(name) <= 200:
        raise Rejected('INVALID_PUBLIC_SERVICE_NAME')
    contact = Contact(params.get('address'), unb64(params.get('signingPublicKey'), 32),
                      unb64(params.get('encryptionPublicKey'), 32), name)
    validate_public_contact(contact)
    interfaces = card.get('supportedInterfaces')
    if not isinstance(interfaces, list) or not any(
            isinstance(i, dict) and i.get('protocolBinding') == 'LOGOS-MESSAGING'
            and i.get('url') == 'logos://' + contact.address for i in interfaces):
        raise Rejected('PUBLIC_SERVICE_ROUTE_MISMATCH')
    return contact
