"""Owner-authorized inference destinations, with recipient-sealed credential updates.

No network requests are made here. A model/delegate cannot change these settings.
Credentials are bound to one exact base URL; changing the destination never
silently forwards an existing key. Only ciphertext enters the messaging journal.
"""
from __future__ import annotations
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import secrets
import stat
from urllib.parse import urlsplit, urlunsplit
from .codec import Rejected, canonical, parse, b64, unb64, identifier
from .file_crypto import Sodium
from .signing import verify_envelope, sign_envelope

DOMAIN = 'commons/relay/inference-settings/v1'
KEY_DOMAIN = DOMAIN + '/credential'
DEFAULT_ENDPOINT = 'https://api.openai.com/v1'
MODEL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$')
FIELDS = {'api', 'endpoint', 'model', 'credential_mode', 'max_output_tokens',
          'input_micro_per_million', 'output_micro_per_million'}

def endpoint(value: str) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= 500
        or any(ord(c) <= 32 or ord(c) >= 127 for c in value)
        or any(c in value for c in ('\\', '%', '?', '#'))):
        raise Rejected('INVALID_INFERENCE_ENDPOINT')
    try:
        parsed = urlsplit(value)
        port = parsed.port
        host = parsed.hostname
    except ValueError:
        raise Rejected('INVALID_INFERENCE_ENDPOINT') from None
    if (not host or parsed.username is not None or parsed.password is not None
        or parsed.scheme not in ('https', 'http') or (port is not None and not 1 <= port <= 65535)
        or parsed.netloc.endswith(':')
        or not re.fullmatch(r'(?:[A-Za-z0-9.-]+|[0-9A-Fa-f:]+)', host)
        or host.startswith('.') or host.endswith('.') or '..' in host
        or any(part in ('.', '..') for part in parsed.path.split('/'))
        or not re.fullmatch(r'[A-Za-z0-9._/-]*', parsed.path)):
        raise Rejected('INVALID_INFERENCE_ENDPOINT')
    if parsed.scheme == 'http' and host.lower() not in ('localhost', '127.0.0.1', '::1'):
        raise Rejected('INFERENCE_HTTPS_REQUIRED')
    if ':' in host:
        try: host = str(ipaddress.IPv6Address(host))
        except ValueError: raise Rejected('INVALID_INFERENCE_ENDPOINT') from None
    path = parsed.path.rstrip('/')
    if path.endswith(('/responses', '/chat/completions')):
        raise Rejected('INFERENCE_BASE_URL_REQUIRED')
    authority = ('[' + host.lower() + ']' if ':' in host else host.lower())
    if port is not None and port != (443 if parsed.scheme == 'https' else 80): authority += ':' + str(port)
    return urlunsplit((parsed.scheme, authority, path, '', ''))

def settings(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise Rejected('INVALID_INFERENCE_SETTINGS')
    result = dict(value)
    result['endpoint'] = endpoint(result['endpoint'])
    if result['api'] not in ('responses', 'chat-completions'):
        raise Rejected('INVALID_INFERENCE_API')
    if not isinstance(result['model'], str) or not MODEL.fullmatch(result['model']):
        raise Rejected('INVALID_INFERENCE_MODEL')
    if result['credential_mode'] not in ('keep', 'replace', 'none'):
        raise Rejected('INVALID_INFERENCE_CREDENTIAL_MODE')
    if type(result['max_output_tokens']) is not int or not 128 <= result['max_output_tokens'] <= 8192:
        raise Rejected('INVALID_INFERENCE_OUTPUT_LIMIT')
    for key in ('input_micro_per_million', 'output_micro_per_million'):
        if type(result[key]) is not int or not 0 <= result[key] <= 1000000000:
            raise Rejected('INVALID_INFERENCE_ESTIMATE')
    return result

def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()

def read_private(path: Path, maximum=32000):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            st = os.fstat(stream.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077 or st.st_size > maximum:
                raise Rejected('INFERENCE_FILE_NOT_PRIVATE')
            return parse(stream.read(maximum + 1))
    except OSError:
        raise Rejected('INFERENCE_FILE_UNAVAILABLE') from None

def private_write(path: Path, raw: bytes, *, exclusive=False):
    if path.is_symlink(): raise Rejected('INFERENCE_FILE_SYMLINK')
    temporary = path.with_name(path.name + '.new-' + secrets.token_hex(8))
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        if exclusive: os.link(temporary, path)
        else: os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if temporary.exists(): temporary.unlink()

def credential_bytes(path: str) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if info.st_mode & 0o077: raise Rejected('PLANNER_SECRET_PERMISSIONS')
            if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= 10000:
                raise Rejected('INFERENCE_CREDENTIAL_FILE_INVALID')
            return stream.read(10001)
    except OSError:
        raise Rejected('INFERENCE_CREDENTIAL_FILE_UNAVAILABLE') from None

def effective(base_path: Path, raw: dict, *, owner_key=None, crypto=None, agent_id='') -> dict:
    value = dict(raw)
    value.update(endpoint=DEFAULT_ENDPOINT, api='responses', max_output_tokens=1536,
                 credential_mode='keep')
    sidecar = base_path.with_name('inference.json')
    expected_digest = None
    if sidecar.exists() or sidecar.is_symlink():
        saved = read_private(sidecar)
        if (not isinstance(saved, dict) or set(saved) != {'schema', 'settings', 'api_key_file', 'request_id', 'request_digest', 'owner_envelope'}
            or type(saved['schema']) is not int or saved['schema'] != 2):
            raise Rejected('INVALID_SAVED_INFERENCE_SETTINGS')
        if owner_key is None or crypto is None or not agent_id:
            raise Rejected('INFERENCE_OWNER_VALIDATION_REQUIRED')
        body = verify_envelope(saved['owner_envelope'], owner_key, crypto)
        required = {'domain','agent_id','request_id','expires_at','expected_hash','settings','sealed_key','credential_digest'}
        if (set(body) != required or body['domain'] != DOMAIN or body['agent_id'] != agent_id
            or body['request_id'] != saved['request_id'] or digest(body) != saved['request_digest']
            or body['settings'] != saved['settings']):
            raise Rejected('SAVED_INFERENCE_SIGNATURE_MISMATCH')
        chosen = settings(saved['settings']); expected_digest = body['credential_digest']
        key_file = saved['api_key_file']
        if chosen['credential_mode'] == 'none':
            if key_file != '' or expected_digest != '': raise Rejected('INFERENCE_KEY_BINDING_MISMATCH')
        else:
            if not isinstance(key_file, str) or not re.fullmatch('[a-f0-9]{64}', str(expected_digest)):
                raise Rejected('INFERENCE_KEY_BINDING_MISMATCH')
            if key_file != raw['api_key_file']:
                key_path = Path(key_file)
                expected_parent = (base_path.parent / 'planner' / 'inference-keys').absolute()
                if key_path.parent != expected_parent or not re.fullmatch(r'key-[a-f0-9]{64}\.txt', key_path.name):
                    raise Rejected('INFERENCE_KEY_PATH_REJECTED')
            elif chosen['endpoint'] != DEFAULT_ENDPOINT:
                raise Rejected('INFERENCE_KEY_BINDING_MISMATCH')
        value.update(chosen, api_key_file=key_file)
    if not isinstance(value.get('api_key_file'), str): raise Rejected('INVALID_PLANNER_PATH')
    value['credential_sha256'] = hashlib.sha256(credential_bytes(value['api_key_file'])).hexdigest() if value['api_key_file'] else ''
    if expected_digest is not None and value['credential_sha256'] != expected_digest:
        raise Rejected('INFERENCE_CREDENTIAL_CHANGED')
    return value

def seal_update(command: dict, agent: str, signer, private, now: int) -> dict:
    required = {'kind', 'settings', 'expected_hash', 'box_key', 'api_key', 'credential_digest'}
    if not isinstance(command, dict) or set(command) != required:
        raise Rejected('INVALID_INFERENCE_SETTINGS_REQUEST')
    chosen = settings(command['settings'])
    if not re.fullmatch('[a-f0-9]{64}', str(command['expected_hash'])):
        raise Rejected('INFERENCE_REVIEW_REQUIRED')
    key = command['api_key']
    if not isinstance(key, str) or len(key) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise Rejected('INVALID_INFERENCE_CREDENTIAL')
    if (chosen['credential_mode'] == 'replace') != bool(key):
        raise Rejected('INFERENCE_CREDENTIAL_ACTION_MISMATCH')
    request_id = 'inference-' + secrets.token_hex(16)
    body = {'domain': DOMAIN, 'agent_id': agent, 'request_id': request_id,
            'expires_at': now + 120, 'expected_hash': command['expected_hash'],
            'settings': chosen, 'sealed_key': '', 'credential_digest': ''}
    if chosen['credential_mode'] == 'keep':
        if not re.fullmatch('[a-f0-9]{64}', str(command['credential_digest'])):
            raise Rejected('INFERENCE_CREDENTIAL_REVIEW_REQUIRED')
        body['credential_digest'] = command['credential_digest']
    elif key:
        body['credential_digest'] = hashlib.sha256(key.encode()).hexdigest()
    if key:
        public = unb64(command['box_key'], 32)
        if len(public) != 32: raise Rejected('INFERENCE_KEY_RECIPIENT_REQUIRED')
        binding = {'domain': KEY_DOMAIN, 'agent_id': agent, 'request_id': request_id,
                   'settings_digest': digest(chosen), 'key': key}
        crypto = Sodium(os.environ.get('COMMONS_RELAY_SODIUM_LIBRARY'))
        body['sealed_key'] = b64(crypto.seal(public, canonical(binding)))
    return {'method': 'planner.configure', 'params': {'envelope': sign_envelope(body, private, signer)}}

class InferenceStore:
    def __init__(self, planner):
        self.planner = planner
        self.service = planner.service
        self.path = self.service.root / 'inference.json'

    def box_pair(self):
        path = self.planner.root / 'inference-box.json'
        crypto = Sodium(os.environ.get('COMMONS_RELAY_SODIUM_LIBRARY'))
        if not path.exists():
            public, secret = crypto.box_keypair()
            try: private_write(path, canonical({'public': b64(public), 'secret': b64(secret)}), exclusive=True)
            except FileExistsError: pass
        data = read_private(path, 4096)
        if not isinstance(data, dict) or set(data) != {'public', 'secret'}:
            raise Rejected('INVALID_INFERENCE_BOX')
        public, secret = unb64(data['public'], 32), unb64(data['secret'], 32)
        if len(public) != 32 or len(secret) != 32: raise Rejected('INVALID_INFERENCE_BOX')
        return crypto, public, secret

    def configure(self, envelope):
        engine = self.service.engine
        body = verify_envelope(envelope, engine.owner_key, engine.crypto)
        required = {'domain', 'agent_id', 'request_id', 'expires_at', 'expected_hash', 'settings', 'sealed_key', 'credential_digest'}
        if set(body) != required or body['domain'] != DOMAIN or body['agent_id'] != engine.agent:
            raise Rejected('INFERENCE_OWNER_BINDING_MISMATCH')
        identifier(body['request_id'])
        if type(body['expires_at']) is not int or not int(engine.clock()) < body['expires_at'] <= int(engine.clock()) + 600:
            raise Rejected('INFERENCE_SETTINGS_EXPIRED')
        chosen = settings(body['settings']); request_digest = digest(body)
        with self.planner.lock:
            if self.planner.active or (self.planner.thread and self.planner.thread.is_alive()):
                raise Rejected('INFERENCE_CHANGE_WHILE_BUSY')
            if self.path.exists():
                prior = read_private(self.path)
                if prior.get('request_id') == body['request_id']:
                    if prior.get('request_digest') != request_digest: raise Rejected('INFERENCE_REQUEST_ID_REUSED')
                    return {'inference_updated': True, 'replayed': True, 'planner': self.planner.status()}
            current = self.planner.configuration()
            if current is None: raise Rejected('PLANNER_NOT_CONFIGURED')
            if body['expected_hash'] != current.configuration_hash:
                raise Rejected('INFERENCE_SETTINGS_CHANGED_REVIEW_AGAIN')
            mode = chosen['credential_mode']
            if mode == 'keep':
                if body['sealed_key']: raise Rejected('UNEXPECTED_INFERENCE_CREDENTIAL')
                if chosen['endpoint'] != current.endpoint:
                    raise Rejected('INFERENCE_KEY_DESTINATION_CHANGED')
                key_file = current.api_key_file
                if body['credential_digest'] != current.credential_sha256: raise Rejected('INFERENCE_CREDENTIAL_CHANGED')
                if not key_file: raise Rejected('INFERENCE_KEY_NOT_CONFIGURED')
            elif mode == 'none':
                if body['sealed_key'] or body['credential_digest']: raise Rejected('UNEXPECTED_INFERENCE_CREDENTIAL')
                key_file = ''
            else:
                crypto, public, secret = self.box_pair()
                value = parse(crypto.open_sealed(public, secret, unb64(body['sealed_key'], 10000)))
                if (not isinstance(value, dict) or set(value) != {'domain','agent_id','request_id','settings_digest','key'}
                    or value['domain'] != KEY_DOMAIN or value['agent_id'] != engine.agent
                    or value['request_id'] != body['request_id'] or value['settings_digest'] != digest(chosen)):
                    raise Rejected('INFERENCE_CREDENTIAL_BINDING_MISMATCH')
                key = value['key']
                if not isinstance(key, str) or not 1 <= len(key) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
                    raise Rejected('INVALID_INFERENCE_CREDENTIAL')
                if hashlib.sha256(key.encode()).hexdigest() != body['credential_digest']:
                    raise Rejected('INFERENCE_CREDENTIAL_BINDING_MISMATCH')
                directory = self.planner.root / 'inference-keys'
                if directory.is_symlink(): raise Rejected('INFERENCE_FILE_SYMLINK')
                directory.mkdir(mode=0o700, exist_ok=True)
                path = directory / ('key-' + hashlib.sha256(body['request_id'].encode()).hexdigest() + '.txt')
                if path.exists():
                    if path.is_symlink() or path.stat().st_mode & 0o077 or path.read_bytes() != key.encode():
                        raise Rejected('INFERENCE_KEY_WRITE_CONFLICT')
                else: private_write(path, key.encode(), exclusive=True)
                key_file = str(path.absolute())
            saved = {'schema': 2, 'settings': chosen, 'api_key_file': key_file,
                     'request_id': body['request_id'], 'request_digest': request_digest, 'owner_envelope': envelope}
            private_write(self.path, canonical(saved))
            return {'inference_updated': True, 'replayed': False, 'planner': self.planner.status()}
