"""Install domain-separated messaging child keys from a fresh LEZ root identity.

This runs at deployment, outside the planner. The wallet's nullifier/spending key
is not copied to the agent profile. Existing identities are never overwritten.
"""
from pathlib import Path
import base64
import ctypes as c
import os
import re
from .codec import Rejected,parse,canonical,b64
from .file_crypto import Sodium
from .signing import Ed25519,protected_directory

PKCS8_ED25519_PREFIX=bytes.fromhex('302e020100300506032b657004220420')
def create_from_wallet_export(vault_root:Path,export:Path,crypto:Sodium):
    if export.is_symlink() or not export.is_file() or export.stat().st_size>4096 or export.stat().st_mode&0o077:raise Rejected('IDENTITY_EXPORT_PERMISSIONS')
    value=parse(export.read_bytes())
    required={'schema','root_account','root_npk','address','signing_seed','encryption_seed'}
    if not isinstance(value,dict) or set(value)!=required or value['schema']!=1:raise Rejected('INVALID_IDENTITY_EXPORT')
    for name in ['root_account','root_npk','signing_seed','encryption_seed']:
        if not isinstance(value[name],str) or not re.fullmatch('[0-9a-f]{64}',value[name]):raise Rejected('INVALID_IDENTITY_EXPORT')
    if value['address']!='lez-'+value['root_npk']:raise Rejected('IDENTITY_ADDRESS_MISMATCH')
    vault_root=protected_directory(vault_root)
    keys=protected_directory(vault_root/'keys')
    for name in ['identity.pem','box-secret','box-public']:
        if (keys/name).exists():raise Rejected('EXISTING_MESSAGING_IDENTITY_PRESERVED')
    der=PKCS8_ED25519_PREFIX+bytes.fromhex(value['signing_seed'])
    encoded=base64.b64encode(der).decode()
    pem=('-----BEGIN PRIVATE KEY-----\n'+encoded+'\n-----END PRIVATE KEY-----\n').encode()
    def write(path,data):
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    write(keys/'identity.pem',pem)
    signer=Ed25519(keys);signing_public=signer.public(keys/'identity.pem')
    public=c.create_string_buffer(32);secret=c.create_string_buffer(32)
    function=crypto.lib.crypto_box_seed_keypair;function.argtypes=[c.c_void_p,c.c_void_p,c.c_char_p];function.restype=c.c_int
    if function(public,secret,bytes.fromhex(value['encryption_seed']))!=0:raise Rejected('IDENTITY_KEY_DERIVATION_FAILED')
    write(keys/'box-public',bytes(public.raw));write(keys/'box-secret',bytes(secret.raw));crypto.lib.sodium_memzero(secret,32)
    result={'schema':1,'root_account':value['root_account'],'root_npk':value['root_npk'],'address':value['address'],
            'signing_key':b64(signing_public),'box_key':b64(bytes(public.raw)),
            'derivation':'HKDF-SHA256, pinned LEZ root nullifier secret, separate Ed25519/X25519 domains'}
    (vault_root/'identity-public.json').write_bytes(canonical(result));(vault_root/'identity-public.json').chmod(0o600)
    return result
