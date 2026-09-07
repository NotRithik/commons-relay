"""File access beneath explicit roots, refusing symlink traversal and overwrites."""
from __future__ import annotations
from contextlib import contextmanager
import os
from pathlib import Path,PurePosixPath
import secrets
import stat
from .codec import Rejected

class FileRoot:
    def __init__(self,root:Path):
        if root.is_symlink() or not root.is_dir():raise Rejected('INVALID_FILE_ROOT')
        self.root=root.resolve()
    def parts(self,relative:str)->tuple[str,...]:
        if not isinstance(relative,str) or not 1<=len(relative)<=1000 or '\x00' in relative or '\\' in relative:
            raise Rejected('INVALID_FILE_PATH')
        p=PurePosixPath(relative)
        pieces=relative.split('/')
        if p.is_absolute() or any(x in ['', '.', '..'] for x in pieces) or len(pieces)>16:
            raise Rejected('FILE_PATH_ESCAPES_ROOT')
        if any(len(x.encode())>240 for x in pieces):raise Rejected('FILE_NAME_TOO_LONG')
        return tuple(pieces)
    @contextmanager
    def parent(self,relative:str):
        parts=self.parts(relative);fd=os.open(self.root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            for name in parts[:-1]:
                nextfd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=nextfd
            yield fd,parts[-1]
        except OSError as exc:raise Rejected('FILE_PATH_UNAVAILABLE') from None
        finally:os.close(fd)
    @contextmanager
    def read(self,relative:str,maximum:int):
        with self.parent(relative) as (parent,name):
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=parent)
            try:
                meta=os.fstat(fd)
                if not stat.S_ISREG(meta.st_mode) or meta.st_size>maximum:raise Rejected('SOURCE_FILE_LIMIT_OR_TYPE')
                with os.fdopen(fd,'rb',closefd=False) as f:yield f
            finally:os.close(fd)
    @contextmanager
    def create(self,relative:str):
        with self.parent(relative) as (parent,name):
            try:os.stat(name,dir_fd=parent,follow_symlinks=False)
            except FileNotFoundError:pass
            else:raise Rejected('DESTINATION_ALREADY_EXISTS')
            temp='.commons_relay-'+secrets.token_hex(16)+'.tmp'
            fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
            committed=False
            try:
                with os.fdopen(fd,'wb',closefd=False) as f:
                    yield f
                    f.flush();os.fsync(fd)
                # Hard-link commit is atomic and refuses a destination created
                # after the first check. No incomplete plaintext is published.
                os.link(temp,name,src_dir_fd=parent,dst_dir_fd=parent,follow_symlinks=False)
                committed=True;os.fsync(parent)
            finally:
                os.close(fd)
                try:os.unlink(temp,dir_fd=parent)
                except FileNotFoundError:pass
