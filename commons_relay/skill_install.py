"""Install an operator-reviewed, hash-pinned skill without editing the module."""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
from .codec import Rejected, canonical, parse
from .external_skills import ExternalSkills
from .signing import protected_directory


def inspect_package(manifest: Path):
    path = manifest.expanduser().absolute()
    if path.is_symlink() or not path.is_file(): raise Rejected('EXTENSION_MANIFEST_INVALID')
    loader = ExternalSkills.__new__(ExternalSkills)
    loader.root = path.parent.resolve(strict=True)
    spec = loader._manifest(path.name)
    return path, spec, parse(path.read_bytes())


def _atomic(path: Path, data: bytes, mode=0o600):
    if path.is_symlink(): raise Rejected('EXTENSION_INSTALL_SYMLINK')
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data); output.flush(); os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists(): temporary.unlink()


def install_skill(profile: Path, manifest: Path, credentials: dict[str, Path] | None = None, *, dry_run=False):
    path, spec, package = inspect_package(manifest)
    credentials = credentials or {}
    if set(credentials) - set(spec.credentials): raise Rejected('UNDECLARED_EXTENSION_CREDENTIAL')
    # Validate explicit credential inputs without displaying their contents.
    secret_bytes = {}
    for name, source in credentials.items():
        source = source.expanduser().absolute()
        if source.is_symlink(): raise Rejected('EXTENSION_CREDENTIAL_PERMISSIONS')
        try:
            fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 8192:
                    raise Rejected('EXTENSION_CREDENTIAL_PERMISSIONS')
                value = stream.read(8193)
                text = value.decode('utf8').rstrip('\r\n')
                if not text or any(c in text for c in ('\x00', '\r', '\n')): raise Rejected('EXTENSION_CREDENTIAL_INVALID')
                secret_bytes[name] = value
        except (OSError, UnicodeError): raise Rejected('EXTENSION_CREDENTIAL_REQUIRED') from None
    report = {'skill': spec.id, 'executable_sha256': spec.executable_sha256,
              'public_eligible': spec.public, 'credential_names': list(spec.credentials),
              'credentials_printed': False, 'core_modified': False, 'program_executed': False}
    if dry_run: return {'dry_run': True, **report}
    profile = protected_directory(profile)
    fd = os.open(profile / '.relay-process.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise Rejected('STOP_AGENT_BEFORE_INSTALLING_SKILLS') from None
        ledger = profile / 'ledger/state.sqlite'
        if ledger.is_file():
            with sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True) as db:
                if db.execute("SELECT 1 FROM tasks WHERE state IN ('working','submitted','unknown','input-required') LIMIT 1").fetchone():
                    raise Rejected('FINISH_PENDING_TASKS_BEFORE_INSTALLING_SKILLS')
        existing = ExternalSkills(profile)
        packages = []
        config = profile / 'extensions.json'
        if config.is_file():
            for filename in parse(config.read_bytes())['manifests']:
                old_path, old_spec, old_package = inspect_package(existing.root / filename)
                if old_spec.id != spec.id: packages.append((old_spec, old_package))
        if len(packages) >= 32: raise Rejected('EXTENSION_LIMIT')
        packages.append((spec, package))
        secret_root = profile / 'extension-secrets' / hashlib.sha256(spec.id.encode()).hexdigest()
        for name in spec.credentials:
            if name not in secret_bytes and not (secret_root / name).is_file():
                raise Rejected('EXTENSION_CREDENTIAL_REQUIRED')
        destination = protected_directory(profile / 'extensions')
        manifests = []
        for current, data in packages:
            executable_name = 'skill-' + current.executable_sha256[:32]
            stored = {**data, 'executable': executable_name}
            name = 'skill-' + hashlib.sha256(canonical(stored)).hexdigest()[:32] + '.json'
            executable_bytes=current.executable.read_bytes()
            if hashlib.sha256(executable_bytes).hexdigest()!=current.executable_sha256:
                raise Rejected('EXTENSION_CHANGED_DURING_INSTALL')
            _atomic(destination / executable_name, executable_bytes, 0o700)
            _atomic(destination / name, canonical(stored))
            manifests.append(name)
        if secret_bytes:
            protected_directory(profile / 'extension-secrets'); protected_directory(secret_root)
            for name, value in secret_bytes.items(): _atomic(secret_root / name, value)
        checker=ExternalSkills.__new__(ExternalSkills);checker.root=destination.resolve(strict=True)
        for name in manifests:checker._manifest(name)
        _atomic(config, canonical({'manifests': manifests}))
        installed = ExternalSkills(profile)
        if not installed.has(spec.id): raise Rejected('EXTENSION_INSTALL_VERIFICATION_FAILED')
        return {'installed': True, 'restart_required': True, **report}
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)
