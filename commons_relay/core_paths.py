"""Short, owner-private local socket namespace bound to one Core session.

macOS sockaddr_un.sun_path has 104 bytes. Deep project directories must not
become the prefix for Qt's module socket names. No shared socket is deleted.
"""
from pathlib import Path
import hashlib
import os
import stat
from .codec import Rejected


def socket_directory(session: Path, *, create: bool = True) -> Path:
    session = session.expanduser().resolve()
    parent = Path('/tmp').resolve(strict=True)
    tag = hashlib.sha256(os.fsencode(str(session))).hexdigest()[:16]
    private_root = parent / ('kite-' + str(os.getuid()))
    directory = private_root / tag
    for candidate in (private_root, directory):
        if create:
            try:
                candidate.mkdir(mode=0o700)
            except FileExistsError:
                pass
        if candidate.exists() or candidate.is_symlink():
            info = candidate.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise Rejected('CORE_SOCKET_DIRECTORY_NOT_PRIVATE')
    if directory.exists() or directory.is_symlink():
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise Rejected('CORE_SOCKET_DIRECTORY_NOT_PRIVATE')
    elif create:
        raise Rejected('CORE_SOCKET_DIRECTORY_MISSING')
    if len(os.fsencode(str(directory))) + 55 >= 104:
        raise Rejected('CORE_SOCKET_DIRECTORY_TOO_LONG')
    return directory
