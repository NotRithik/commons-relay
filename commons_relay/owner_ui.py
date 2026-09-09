"""One-shot local signer for the trusted Basecamp owner interface.

This helper does not connect to an agent or a network. It loads only a named
owner profile below an operator-configured root, validates a typed UI request,
and returns an owner-signed command. Private keys never enter QML or the agent
runtime. The receiver independently verifies signatures, policy and intent.
"""
from __future__ import annotations
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
from .codec import Rejected, b64, canonical, identifier, parse
from .controller import OWNER_DOMAIN
from .engine import APPROVAL_DOMAIN, REQUEST_DOMAIN, GRANT_DOMAIN
from .signing import Ed25519, sign_envelope
from .skills import default_registry

PROFILE_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
DIGEST = re.compile(r'^[0-9a-f]{64}$')
MAX_FRAME = 60000


def exact(value: dict, fields: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        raise Rejected('INVALID_OWNER_UI_REQUEST')


def read_private_json(path: Path) -> dict:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077 or metadata.st_size > 8192:
                raise Rejected('OWNER_PROFILE_PERMISSIONS')
            value = parse(source.read(8193))
    except OSError:
        raise Rejected('OWNER_PROFILE_UNAVAILABLE') from None
    if not isinstance(value, dict):
        raise Rejected('OWNER_PROFILE_INVALID')
    return value


class OwnerUi:
    def __init__(self, root: Path, clock=time.time):
        if not root.is_absolute() or root.is_symlink() or not root.is_dir():
            raise Rejected('OWNER_ROOT_NOT_CONFIGURED')
        if stat.S_IMODE(root.stat().st_mode) & 0o077:
            raise Rejected('OWNER_ROOT_PERMISSIONS')
        self.root = root.resolve(strict=True)
        self.clock = clock

    def load(self, name: str):
        if not isinstance(name, str) or not PROFILE_NAME.fullmatch(name):
            raise Rejected('INVALID_OWNER_PROFILE_NAME')
        directory = self.root / name
        if directory.is_symlink() or not directory.is_dir() or directory.resolve().parent != self.root:
            raise Rejected('OWNER_PROFILE_OUTSIDE_ROOT')
        if stat.S_IMODE(directory.stat().st_mode) & 0o077:
            raise Rejected('OWNER_PROFILE_PERMISSIONS')
        info = read_private_json(directory / 'agent.json')
        agent = identifier(info.get('agent_id'))
        signer = Ed25519(directory)
        private = directory / 'owner-signing.pem'
        public = signer.public(private)
        if info.get('owner_public_key') != b64(public):
            raise Rejected('OWNER_KEY_BINDING_CHANGED')
        return directory, agent, signer, private, public

    def catalog(self) -> dict:
        profiles = []
        unavailable = 0
        # A bounded catalog of operator-created profiles, not a home-directory scan.
        candidates = sorted(self.root.iterdir(), key=lambda path: path.name)
        if len(candidates) > 64:
            raise Rejected('OWNER_CATALOG_LIMIT')
        for directory in candidates:
            if not directory.is_dir() or directory.is_symlink() or not PROFILE_NAME.fullmatch(directory.name):
                continue
            try:
                _, agent, _, _, _ = self.load(directory.name)
            except Rejected:
                unavailable += 1
                continue
            info = read_private_json(directory / 'agent.json')
            label = info.get('display_name')
            if (not isinstance(label, str) or not 1 <= len(label.strip()) <= 80
                or any(ord(char) < 32 or ord(char) == 127 for char in label)):
                label = directory.name.replace('-', ' ').replace('_', ' ').title()
            profiles.append({'name': directory.name, 'label': label.strip(), 'agent_id': agent})
        return {'profiles': profiles, 'unavailable_profiles': unavailable,
                'signing': 'local-owner-only', 'network_requests': 0}

    def inner_command(self, command: dict, agent: str, signer, private, public) -> dict:
        if not isinstance(command, dict) or not isinstance(command.get('kind'), str):
            raise Rejected('INVALID_OWNER_UI_REQUEST')
        kind = command['kind']
        now = int(self.clock())
        if kind == 'heartbeat':
            exact(command, {'kind', 'nonce'})
            if not isinstance(command['nonce'], str) or not re.fullmatch('[0-9a-f]{32}', command['nonce']):
                raise Rejected('INVALID_HEARTBEAT_CHALLENGE')
            return {'method': 'owner.ping', 'params': {'nonce': command['nonce']}}
        if kind == 'planner_configure':
            from .inference_settings import seal_update
            return seal_update(command, agent, signer, private, now)
        if kind == 'planner_permission':
            from .conversation_permissions import compose_decision
            return compose_decision(command, agent, signer, private, now)
        elif kind == 'planner_status':
            exact(command, {'kind'})
            return {'method': 'planner.status', 'params': {}}
        elif kind == 'planner_history':
            exact(command, {'kind','offset'})
            if type(command['offset']) is not int or not 0 <= command['offset'] <= 100000:
                raise Rejected('INVALID_OWNER_PAGE')
            return {'method': 'planner.history', 'params': {'offset': command['offset']}}
        elif kind in ('planner_goal', 'planner_permission_review'):
            exact(command, {'kind','goal_id'})
            return {'method': 'planner.permission_view' if kind == 'planner_permission_review' else 'planner.goal', 'params': {'goal_id': identifier(command['goal_id'])}}
        elif kind == 'planner_start':
            from .planner import READ_SKILLS
            required = {'kind','goal','delegate_key_id','mode','allowed_skills',
                        'maximum_spend','max_steps','expires_in','policy_version','inference_hash'}
            if set(command) != required:
                raise Rejected('INVALID_OWNER_UI_REQUEST')
            goal=command['goal']; mode=command['mode']; skills=command['allowed_skills']
            if not isinstance(goal,str) or not 1 <= len(goal.strip()) <= 4000 or len(canonical(goal)) > 6000:
                raise Rejected('INVALID_PLANNER_PROMPT')
            if mode not in ('read','actions') or not isinstance(skills,list) or not 1 <= len(skills) <= 32 or len(set(skills)) != len(skills):
                raise Rejected('INVALID_GRANT_SKILLS')
            for name in skills:
                identifier(name)
                if name == 'meta.configure':raise Rejected('PLANNER_CANNOT_CONFIGURE_OWNER_POLICY')
            if mode == 'read' and (not set(skills).issubset(READ_SKILLS) or command['maximum_spend'] != '0'):
                raise Rejected('READ_ONLY_GRANT_MUST_NOT_SPEND')
            from .codec import amount
            amount(command['maximum_spend'])
            delegate=command['delegate_key_id']
            if not isinstance(delegate,str) or not re.fullmatch(r'ed25519:[0-9a-f]{64}',delegate):
                raise Rejected('INVALID_DELEGATE_KEY')
            steps=command['max_steps']; ttl=command['expires_in']; version=command['policy_version']
            if type(steps) is not int or not 1 <= steps <= 8 or type(ttl) is not int or not 1 <= ttl <= 86400 or type(version) is not int or version < 1:
                raise Rejected('INVALID_PLANNER_LIMITS')
            body={'domain':GRANT_DOMAIN,'agent_id':agent,'grant_id':identifier('chat-'+secrets.token_hex(12)),
                  'delegate_key_id':delegate,'goal':goal.strip(),'allowed_skills':skills,
                  'maximum_spend':command['maximum_spend'],'max_steps':steps,'expires_at':now+ttl,
                  'policy_version':version}
            if 'inference_hash' in command:
                if not DIGEST.fullmatch(str(command['inference_hash'])):raise Rejected('INVALID_INFERENCE_REVIEW')
                body['inference_hash'] = command['inference_hash']
            return {'method':'planner.start','params':{'envelope':sign_envelope(body,private,signer)}}
        elif kind == 'planner_cancel':
            exact(command, {'kind','goal_id'})
            goal_id=identifier(command['goal_id'])
            body={'domain':'commons/commons_relay/revoke/v1','agent_id':agent,'grant_id':goal_id,'expires_at':now+300}
            return {'method':'planner.cancel','params':{'goal_id':goal_id,'envelope':sign_envelope(body,private,signer)}}
        elif kind in ('snapshot', 'skills'):
            exact(command, {'kind', 'offset'})
            offset = command['offset']
            if type(offset) is not int or not 0 <= offset <= 100000:
                raise Rejected('INVALID_OWNER_PAGE')
            return {'method': 'owner.snapshot' if kind == 'snapshot' else 'owner.skills',
                    'params': {'offset': offset}}
        if kind == 'skill':
            exact(command, {'kind', 'name'})
            return {'method': 'owner.skill', 'params': {'name': identifier(command['name'])}}
        if kind == 'task':
            exact(command, {'kind', 'task_id'})
            return {'method': 'owner.task', 'params': {'task_id': identifier(command['task_id'])}}
        ttl = command.get('expires_in')
        if type(ttl) is not int or not 30 <= ttl <= 86400:
            raise Rejected('INVALID_OWNER_COMMAND_EXPIRY')
        expiry = int(self.clock()) + ttl
        if kind == 'submit':
            exact(command, {'kind', 'skill', 'arguments', 'expires_in'})
            name = identifier(command['skill'])
            arguments = command['arguments']
            if not isinstance(arguments, dict) or len(canonical(arguments)) > 18000:
                raise Rejected('OWNER_TASK_ARGUMENT_LIMIT')
            registry = default_registry()
            if name in {item['id'] for item in registry.describe()}:
                registry.get(name).validate(arguments)
            # Third-party skills are validated by their registered remote schema.
            # Signing their exact arguments is not a bypass of the agent policy.
            body = {'domain': REQUEST_DOMAIN, 'agent_id': agent,
                    'request_id': secrets.token_hex(16), 'skill': name,
                    'arguments': arguments, 'expires_at': expiry}
            return {'method': 'submit', 'params': {
                'envelope': sign_envelope(body, private, signer), 'public_key': b64(public)}}
        if kind == 'approve':
            exact(command, {'kind', 'task_id', 'intent_hash', 'policy_version', 'expires_in'})
            if not isinstance(command['intent_hash'], str) or not DIGEST.fullmatch(command['intent_hash']):
                raise Rejected('INVALID_APPROVAL_INTENT')
            version = command['policy_version']
            if type(version) is not int or not 1 <= version <= 2147483647:
                raise Rejected('INVALID_APPROVAL_POLICY')
            body = {'domain': APPROVAL_DOMAIN, 'agent_id': agent,
                    'task_id': identifier(command['task_id']), 'intent_hash': command['intent_hash'],
                    'policy_version': version, 'approval_id': secrets.token_hex(16),
                    'decision': 'approve', 'expires_at': expiry}
            return {'method': 'approve', 'params': {'envelope': sign_envelope(body, private, signer)}}
        if kind == 'cancel':
            exact(command, {'kind', 'task_id', 'expires_in'})
            body = {'domain': 'commons/relay/cancel/v1', 'agent_id': agent,
                    'task_id': identifier(command['task_id']), 'expires_at': expiry}
            return {'method': 'cancel', 'params': {'envelope': sign_envelope(body, private, signer)}}
        raise Rejected('OWNER_UI_ACTION_NOT_ALLOWED')

    def handle(self, request: dict) -> dict:
        if request == {'action': 'catalog'}:
            return self.catalog()
        exact(request, {'action', 'profile', 'command'})
        if request['action'] != 'compose':
            raise Rejected('OWNER_UI_ACTION_NOT_ALLOWED')
        _, agent, signer, private, public = self.load(request['profile'])
        command = self.inner_command(request['command'], agent, signer, private, public)
        request_id = 'ui-' + secrets.token_hex(16)
        body = {'domain': OWNER_DOMAIN, 'agent_id': agent, 'request_id': request_id,
                'command': command, 'expires_at': int(self.clock()) + 120}
        envelope = sign_envelope(body, private, signer)
        result = {'agent_id': agent, 'command_id': request_id,
                  'command_kind': request['command']['kind'],
                  'request': {'method': 'owner.send', 'params': {'recipient': agent, 'envelope': envelope}}}
        if command['method'] == 'planner.start':
            # The UI can query this same signed goal after an uncertain transport
            # reply instead of creating another potentially billed conversation.
            result['conversation_id'] = command['params']['envelope']['body']['grant_id']
        return result


def main() -> None:
    os.umask(0o077)
    try:
        root = os.environ.get('COMMONS_RELAY_OWNER_ROOT')
        if not root:
            raise Rejected('OWNER_ROOT_NOT_CONFIGURED')
        frame = sys.stdin.buffer.readline(MAX_FRAME + 2)
        if len(frame) > MAX_FRAME or not frame.endswith(b'\n'):
            raise Rejected('INVALID_OWNER_UI_FRAME')
        result = OwnerUi(Path(root)).handle(parse(frame))
        response = {'success': True, 'result': result}
        if len(canonical(response)) > MAX_FRAME:
            raise Rejected('OWNER_UI_RESPONSE_LIMIT')
    except Rejected as error:
        code = str(error)
        if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,99}', code):
            code = 'OWNER_COMMAND_VALIDATION_FAILED'
        response = {'success': False, 'error': code}
    except Exception:
        response = {'success': False, 'error': 'OWNER_LOCAL_OPERATION_FAILED'}
    sys.stdout.buffer.write(canonical(response) + b'\n')
    sys.stdout.buffer.flush()


if __name__ == '__main__':
    main()
