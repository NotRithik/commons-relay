"""Ed25519 through the installed OpenSSL implementation; no custom cryptography."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from .codec import Rejected, canonical, b64, unb64, parse

DER_PREFIX=bytes.fromhex('302a300506032b6570032100')

def protected_directory(path: Path) -> Path:
    path=path.absolute()
    if path.is_symlink():raise Rejected('SYMLINK_DIRECTORY')
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    if stat.S_IMODE(path.stat().st_mode)&0o077:raise Rejected('DIRECTORY_NOT_PRIVATE')
    return path.resolve()

class Ed25519:
    def __init__(self,scratch: Path,openssl: str | None = None):
        self.scratch=protected_directory(scratch)
        candidate=openssl or shutil.which('openssl')
        if not candidate or not Path(candidate).is_file():raise Rejected('OPENSSL_NOT_AVAILABLE')
        self.openssl=str(Path(candidate).resolve())
    def _run(self,args:list[str], *, allow_failure=False) -> subprocess.CompletedProcess:
        env={'PATH':'/usr/bin:/bin','HOME':str(self.scratch),'TMPDIR':str(self.scratch),
             'OPENSSL_CONF':'/dev/null'}
        p=subprocess.run([self.openssl,*args],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
        if p.returncode and not allow_failure:raise Rejected('CRYPTO_OPERATION_FAILED')
        return p
    def generate(self,path: Path) -> bytes:
        if path.exists() or path.is_symlink():raise Rejected('KEY_ALREADY_EXISTS')
        if not path.parent.resolve().is_relative_to(self.scratch):raise Rejected('KEY_PATH_OUTSIDE_STATE')
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
        self._run(['genpkey','-algorithm','ED25519','-out',str(path)])
        path.chmod(0o600)
        return self.public(path)
    def public(self,path: Path) -> bytes:
        self._check_private(path)
        result=self._run(['pkey','-in',str(path),'-pubout','-outform','DER']).stdout
        if len(result)!=44 or not result.startswith(DER_PREFIX):raise Rejected('INVALID_ED25519_KEY')
        return result[len(DER_PREFIX):]
    def _check_private(self,path:Path):
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode)&0o077:
            raise Rejected('PRIVATE_KEY_PERMISSIONS')
        if not path.resolve().is_relative_to(self.scratch):raise Rejected('KEY_PATH_OUTSIDE_STATE')
    def sign(self,path:Path,message:bytes)->bytes:
        self._check_private(path)
        if not message or len(message)>131072:raise Rejected('INVALID_SIGNATURE_INPUT')
        with tempfile.TemporaryDirectory(dir=self.scratch) as d:
            message_path=Path(d)/'message';message_path.write_bytes(message)
            return self._run(['pkeyutl','-sign','-rawin','-inkey',str(path),'-in',str(message_path)]).stdout
    def verify(self,public:bytes,message:bytes,signature:bytes)->bool:
        if len(public)!=32 or len(signature)!=64 or not message or len(message)>131072:return False
        with tempfile.TemporaryDirectory(dir=self.scratch) as d:
            d=Path(d);(d/'public.der').write_bytes(DER_PREFIX+public);(d/'message').write_bytes(message);(d/'sig').write_bytes(signature)
            result=self._run(['pkeyutl','-verify','-pubin','-keyform','DER','-inkey',str(d/'public.der'),
                              '-rawin','-in',str(d/'message'),'-sigfile',str(d/'sig')],allow_failure=True)
            return result.returncode==0

def key_id(public:bytes)->str:
    if len(public)!=32:raise Rejected('INVALID_ED25519_KEY')
    return 'ed25519:'+hashlib.sha256(public).hexdigest()

def sign_envelope(body:dict,private:Path,crypto:Ed25519)->dict:
    public=crypto.public(private)
    return {'key_id':key_id(public),'body':body,'signature':b64(crypto.sign(private,canonical(body)))}

def verify_envelope(envelope:dict,public:bytes,crypto:Ed25519)->dict:
    if not isinstance(envelope,dict) or set(envelope)!={'key_id','body','signature'}:
        raise Rejected('INVALID_ENVELOPE')
    if envelope['key_id']!=key_id(public) or not isinstance(envelope['body'],dict):raise Rejected('WRONG_SIGNER')
    if not crypto.verify(public,canonical(envelope['body']),unb64(envelope['signature'],64)):
        raise Rejected('INVALID_SIGNATURE')
    return envelope['body']
