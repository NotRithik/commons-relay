#!/usr/bin/env python3
"""Fetch SHA256-pinned official local-proving prerequisites, without executing them.

No global installer, rustup mutation, model API, hosted prover or wallet is used.
The generated paths.json is consumed by the explicit build/run script.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import pathlib
import platform
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

REPO=pathlib.Path(__file__).resolve().parent.parent
PIN=json.loads((REPO/'integration/prerequisites.json').read_text())

def sha256(path):
    digest=hashlib.sha256()
    with path.open('rb') as reader:
        for chunk in iter(lambda:reader.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()

def safe_extract(archive,destination):
    if archive.name.endswith('.zip'):
        with zipfile.ZipFile(archive) as source:
            for item in source.infolist():
                path=pathlib.PurePosixPath(item.filename)
                mode=item.external_attr >> 16
                if path.is_absolute() or '..' in path.parts or (mode & 0o170000)==0o120000:
                    raise RuntimeError('Unsafe archive member: '+item.filename)
            source.extractall(destination)
            for item in source.infolist():
                target=destination/item.filename
                if target.is_file() and item.external_attr >> 16 & 0o111:target.chmod(0o755)
    else:
        with tarfile.open(archive,'r:*') as source:
            # Python's data filter rejects traversal, external symlinks and devices.
            source.extractall(destination,filter='data')

def locate(root,name,parent=None):
    hits=[p for p in root.rglob(name) if p.is_file() and (parent is None or p.parent.name==parent)]
    if len(hits)!=1:raise RuntimeError('Expected exactly one '+name+' under '+str(root))
    return hits[0].resolve()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=pathlib.Path)
    parser.add_argument('--platform',choices=list(PIN['platforms']))
    args=parser.parse_args()
    selected=args.platform or ('macos-arm64' if platform.system()=='Darwin' and platform.machine() in ('arm64','aarch64') else 'linux-x86_64' if platform.system()=='Linux' and platform.machine()=='x86_64' else None)
    if selected not in PIN['platforms']:parser.error('Unsupported platform')
    base=args.directory.absolute()
    if base.is_symlink():raise RuntimeError('Refusing symlink dependency directory')
    base.mkdir(parents=True,exist_ok=True)
    cache=base/'archives';cache.mkdir(exist_ok=True)
    if cache.is_symlink():raise RuntimeError('Refusing symlink archive cache')
    # Relay's local proof demo builds no custom RISC-V guest, so it deliberately
    # skips the much larger guest Rust archive. The upstream LEZ programs and
    # privacy circuit are pinned by the LEZ revision; local proving needs only
    # r0vm, the Logos circuit archive and rapidsnark.
    dirs={}
    for kind in ['prover','circuits','rapidsnark']:
        item=PIN['platforms'][selected][kind]
        if not item['url'].startswith('https://github.com/') or pathlib.Path(item['name']).name!=item['name']:
            raise RuntimeError('Invalid pinned archive metadata')
        archive=cache/item['name']
        if archive.is_symlink():raise RuntimeError('Refusing symlink archive')
        if not archive.exists() or archive.stat().st_size!=item['bytes'] or sha256(archive)!=item['sha256']:
            temporary=archive.with_suffix(archive.suffix+'.download')
            subprocess.run(['curl','--fail','--silent','--show-error','--location','--proto','=https','--retry','2','--max-time','1800',item['url'],'--output',str(temporary)],check=True)
            if temporary.stat().st_size!=item['bytes'] or sha256(temporary)!=item['sha256']:
                raise RuntimeError('Pinned asset verification failed: '+kind)
            temporary.replace(archive)
        destination=base/kind;receipt=destination/'.commons-pinned-asset.json'
        if destination.exists():
            if not receipt.is_file() or json.loads(receipt.read_text())!=item:
                raise RuntimeError('Refusing unknown existing dependency directory: '+str(destination))
        else:
            staging=pathlib.Path(tempfile.mkdtemp(prefix=kind+'-',dir=base))
            try:
                safe_extract(archive,staging)
                (staging/'.commons-pinned-asset.json').write_text(json.dumps(item,indent=2)+'\n')
                staging.rename(destination)
            finally:
                if staging.exists():shutil.rmtree(staging)
        dirs[kind]=destination
        print('Verified official asset:',kind,item['sha256'],flush=True)
    result={'schema_version':1,'platform':selected,'r0vm':str(locate(dirs['prover'],'r0vm')),
        'lbc_root':str(locate(dirs['circuits'],'libpol.a').parent.parent),
        'rapidsnark_lib':str(locate(dirs['rapidsnark'],'librapidsnark.a').parent),
        'host_rust':PIN['host_rust'],'lez_revision':PIN['lez_revision']}
    (base/'paths.json').write_text(json.dumps(result,indent=2)+'\n')
    print('Dependency data ready. No downloaded executable or install script was run.',flush=True)
    print('PATHS_JSON='+str(base/'paths.json'))

if __name__=='__main__':main()
