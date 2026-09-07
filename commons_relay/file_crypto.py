"""File encryption using libsodium secretstream, with bounded chunk framing.

This is a format wrapper around a vetted library, not a new cipher. Keys remain
in the agent's protected vault; tool results contain only public file references.
"""
from __future__ import annotations
import ctypes as c
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import secrets
import struct
from typing import BinaryIO
from .codec import canonical,parse,Rejected

MAGIC=b'CSTVLT1\x00'
CHUNK=65536
MAX_FILE=256*1024*1024
AAD=b'commons/commons_relay/file-v1'

class Sodium:
    def __init__(self,library:Path|str|None=None):
        path=str(library) if library else ctypes.util.find_library('sodium')
        if not path:raise Rejected('SODIUM_NOT_AVAILABLE')
        self.lib=c.CDLL(path)
        self._bind('sodium_init',[],c.c_int)
        self._bind('sodium_memzero',[c.c_void_p,c.c_size_t],None)
        if self.lib.sodium_init()<0:raise Rejected('SODIUM_INITIALIZATION_FAILED')
        prefix='crypto_secretstream_xchacha20poly1305_'
        for name in ['statebytes','headerbytes','keybytes','abytes']:
            self._bind(prefix+name,[],c.c_size_t)
        for name in ['tag_message','tag_final']:
            self._bind(prefix+name,[],c.c_ubyte)
        self.state_size=getattr(self.lib,prefix+'statebytes')()
        self.header_size=getattr(self.lib,prefix+'headerbytes')()
        self.key_size=getattr(self.lib,prefix+'keybytes')()
        self.overhead=getattr(self.lib,prefix+'abytes')()
        self.final=getattr(self.lib,prefix+'tag_final')()
        if (self.header_size,self.key_size,self.overhead)!=(24,32,17) or not 1<=self.state_size<=4096:
            raise Rejected('UNSUPPORTED_SECRETSTREAM_ABI')
        self._bind(prefix+'init_push',[c.c_void_p,c.c_void_p,c.c_char_p],c.c_int)
        self._bind(prefix+'init_pull',[c.c_void_p,c.c_char_p,c.c_char_p],c.c_int)
        self._bind(prefix+'push',[c.c_void_p,c.c_void_p,c.POINTER(c.c_ulonglong),c.c_char_p,c.c_ulonglong,c.c_char_p,c.c_ulonglong,c.c_ubyte],c.c_int)
        self._bind(prefix+'pull',[c.c_void_p,c.c_void_p,c.POINTER(c.c_ulonglong),c.POINTER(c.c_ubyte),c.c_char_p,c.c_ulonglong,c.c_char_p,c.c_ulonglong],c.c_int)
        self._bind('crypto_box_keypair',[c.c_void_p,c.c_void_p],c.c_int)
        self._bind('crypto_box_seal',[c.c_void_p,c.c_char_p,c.c_ulonglong,c.c_char_p],c.c_int)
        self._bind('crypto_box_seal_open',[c.c_void_p,c.c_char_p,c.c_ulonglong,c.c_char_p,c.c_char_p],c.c_int)
    def _bind(self,name,args,result):
        f=getattr(self.lib,name);f.argtypes=args;f.restype=result
    def box_keypair(self)->tuple[bytes,bytes]:
        public=c.create_string_buffer(32);secret=c.create_string_buffer(32)
        if self.lib.crypto_box_keypair(public,secret)!=0:raise Rejected('KEYGEN_FAILED')
        value=(bytes(public.raw),bytes(secret.raw));self.lib.sodium_memzero(secret,32);return value
    def seal(self,public:bytes,message:bytes)->bytes:
        if len(public)!=32 or len(message)>65536:raise Rejected('INVALID_SHARE_INPUT')
        out=c.create_string_buffer(len(message)+48)
        if self.lib.crypto_box_seal(out,message,len(message),public)!=0:raise Rejected('SEAL_FAILED')
        return bytes(out.raw)
    def open_sealed(self,public:bytes,secret:bytes,message:bytes)->bytes:
        if len(public)!=32 or len(secret)!=32 or not 48<=len(message)<=65584:raise Rejected('INVALID_SEALED_INPUT')
        out=c.create_string_buffer(max(1,len(message)-48))
        if self.lib.crypto_box_seal_open(out,message,len(message),public,secret)!=0:raise Rejected('SHARE_AUTHENTICATION_FAILED')
        value=bytes(out.raw[:len(message)-48]);self.lib.sodium_memzero(out,len(out));return value
    def encrypt(self,source:BinaryIO,destination:BinaryIO,metadata:dict,key:bytes,maximum=MAX_FILE)->dict:
        if len(key)!=32:raise Rejected('INVALID_FILE_KEY')
        if type(maximum)is not int or not 0<=maximum<=MAX_FILE:raise Rejected('INVALID_FILE_LIMIT')
        meta=canonical(metadata)
        if len(meta)>4096:raise Rejected('FILE_METADATA_TOO_LARGE')
        state=c.create_string_buffer(self.state_size);header=c.create_string_buffer(24)
        if self.lib.crypto_secretstream_xchacha20poly1305_init_push(state,header,key)!=0:raise Rejected('ENCRYPTION_INITIALIZATION_FAILED')
        digest=hashlib.sha256();written=0
        def write(data):
            nonlocal written
            destination.write(data);digest.update(data);written+=len(data)
        def push(data,tag):
            out=c.create_string_buffer(len(data)+self.overhead);size=c.c_ulonglong()
            if self.lib.crypto_secretstream_xchacha20poly1305_push(state,out,c.byref(size),data,len(data),AAD,len(AAD),tag)!=0:
                raise Rejected('FILE_ENCRYPTION_FAILED')
            write(struct.pack('<I',size.value));write(bytes(out.raw[:size.value]))
        total=0
        try:
            write(MAGIC+header.raw);push(meta,0)
            while True:
                block=source.read(CHUNK)
                if not block:break
                total+=len(block)
                if total>maximum:raise Rejected('FILE_LIMIT_EXCEEDED')
                push(block,0)
            push(b'',self.final)
            return {'plaintext_bytes':total,'ciphertext_bytes':written,'ciphertext_sha256':digest.hexdigest(),'format':'commons-secretstream-v1'}
        finally:self.lib.sodium_memzero(state,self.state_size)
    def decrypt(self,source:BinaryIO,destination:BinaryIO,key:bytes,maximum=MAX_FILE)->dict:
        if len(key)!=32:raise Rejected('INVALID_FILE_KEY')
        if type(maximum)is not int or not 0<=maximum<=MAX_FILE:raise Rejected('INVALID_FILE_LIMIT')
        if source.read(len(MAGIC))!=MAGIC:raise Rejected('UNKNOWN_FILE_FORMAT')
        header=source.read(24)
        if len(header)!=24:raise Rejected('TRUNCATED_FILE_HEADER')
        state=c.create_string_buffer(self.state_size)
        if self.lib.crypto_secretstream_xchacha20poly1305_init_pull(state,header,key)!=0:raise Rejected('INVALID_FILE_HEADER')
        total=0;metadata=None
        try:
            while True:
                frame=source.read(4)
                if len(frame)!=4:raise Rejected('TRUNCATED_ENCRYPTED_STREAM')
                size=struct.unpack('<I',frame)[0]
                if not self.overhead<=size<=CHUNK+self.overhead:raise Rejected('INVALID_ENCRYPTED_CHUNK_SIZE')
                ciphertext=source.read(size)
                if len(ciphertext)!=size:raise Rejected('TRUNCATED_ENCRYPTED_CHUNK')
                out=c.create_string_buffer(max(1,size-self.overhead));length=c.c_ulonglong();tag=c.c_ubyte()
                if self.lib.crypto_secretstream_xchacha20poly1305_pull(state,out,c.byref(length),c.byref(tag),ciphertext,size,AAD,len(AAD))!=0:
                    raise Rejected('FILE_AUTHENTICATION_FAILED')
                plain=bytes(out.raw[:length.value]);self.lib.sodium_memzero(out,len(out))
                if metadata is None:
                    if tag.value!=0 or len(plain)>4096:raise Rejected('INVALID_FILE_METADATA')
                    metadata=parse(plain)
                    if not isinstance(metadata,dict):raise Rejected('INVALID_FILE_METADATA')
                elif tag.value==self.final:
                    if plain or source.read(1):raise Rejected('TRAILING_ENCRYPTED_DATA')
                    return {'metadata':metadata,'plaintext_bytes':total}
                elif tag.value==0:
                    total+=len(plain)
                    if total>maximum:raise Rejected('FILE_LIMIT_EXCEEDED')
                    destination.write(plain)
                else:raise Rejected('UNEXPECTED_STREAM_TAG')
        finally:self.lib.sodium_memzero(state,self.state_size)
