#!/usr/bin/env python3
"""Fetch pinned Qt SDK data into a chosen directory; execute no downloaded script.

Only the official HTTPS URLs in native/qt-archives.json are accepted. SHA256 is
checked before extraction. Requires the system bsdtar/libarchive utility.
This SDK is a build dependency, not part of the project's release bundle.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import pathlib
import platform
import shutil
import subprocess
import tempfile

ROOT=pathlib.Path(__file__).resolve().parent.parent

def sha256(path: pathlib.Path) -> str:
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()

def run(destination: pathlib.Path, selected: str) -> None:
    manifest=json.loads((ROOT/'native/qt-archives.json').read_text())
    archives=manifest['platforms'][selected]
    destination.mkdir(parents=True,exist_ok=True)
    if destination.is_symlink(): raise RuntimeError('Refusing a symlink SDK destination')
    cache=destination/'archives'
    if cache.is_symlink(): raise RuntimeError('Refusing a symlink archive cache')
    cache.mkdir(exist_ok=True)
    tar=shutil.which('bsdtar')
    if not tar: raise RuntimeError('Install the system libarchive-tools/bsdtar package first')
    for item in archives:
        name=item['name']; url=item['url']
        if pathlib.PurePosixPath(name).name != name or not url.startswith('https://download.qt.io/online/qtsdkrepository/'):
            raise RuntimeError('Untrusted archive metadata')
        archive=cache/name
        if archive.is_symlink(): raise RuntimeError('Refusing symlink archive')
        if not archive.exists() or sha256(archive)!=item['sha256']:
            with tempfile.NamedTemporaryFile(dir=cache,delete=False,prefix='qt-download-') as tmp:
                temporary=pathlib.Path(tmp.name)
            try:
                subprocess.run(['curl','--fail','--silent','--show-error','--location','--proto','=https','--retry','2','--max-time','1200',url,'--output',str(temporary)],check=True)
                if temporary.stat().st_size != item['bytes'] or sha256(temporary)!=item['sha256']:
                    raise RuntimeError('Pinned Qt archive verification failed: '+name)
                temporary.replace(archive)
            finally:
                temporary.unlink(missing_ok=True)
        entries=subprocess.check_output([tar,'-tf',str(archive)],text=True).splitlines()
        for entry in entries:
            path=pathlib.PurePosixPath(entry)
            if path.is_absolute() or '..' in path.parts:
                raise RuntimeError('Unexpected path in Qt archive')
        print('Verified',name,flush=True)
    prefix=destination/'prefix'
    if prefix.exists():
        receipt=prefix/'.commons-qt-pins.json'
        if receipt.is_file() and json.loads(receipt.read_text())==archives:
            print('Previously extracted verified SDK:',prefix)
            return
        raise RuntimeError('Refusing to merge into an unknown/nonempty SDK prefix')
    temporary=pathlib.Path(tempfile.mkdtemp(dir=destination,prefix='qt-extract-'))
    try:
        for item in archives:
            # libarchive retains its default protections against absolute paths,
            # traversal and writes through symlinked parent directories.
            # Qt's ICU add-on archive contains bare libicu*.so names. The
            # official installer places it in Qt's lib directory; preserve that
            # layout rather than leaving the libraries at the prefix root.
            extract_to = temporary / 'lib' if item['name'].startswith('icu-') else temporary
            extract_to.mkdir(exist_ok=True)
            subprocess.run([tar,'-xf',str(cache/item['name']),'-C',str(extract_to),'--no-same-owner'],check=True)
        (temporary/'.commons-qt-pins.json').write_text(json.dumps(archives,indent=2)+'\n')
        temporary.rename(prefix)
    finally:
        if temporary.exists():shutil.rmtree(temporary)
    print('QT_PREFIX='+str(prefix.resolve()))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination',type=pathlib.Path)
    parser.add_argument('--platform',choices=['linux-x86_64','macos-arm64'])
    args=parser.parse_args()
    host=('macos-arm64' if platform.system()=='Darwin' and platform.machine() in ('arm64','aarch64') else 'linux-x86_64' if platform.system()=='Linux' and platform.machine()=='x86_64' else None)
    selected=args.platform or host
    if selected is None:parser.error('Unsupported host; specify an explicit supported SDK platform')
    run(args.destination.absolute(),selected)
