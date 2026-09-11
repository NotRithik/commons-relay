#!/usr/bin/env python3
"""Fetch a SHA-256-pinned Logos 0.2.3 Linux runtime for native module linkage.

Extracts the official archive/AppImage as data; it does not run Logos or install
system files. Requires Python 3.12+ and unsquashfs when the archive is an AppImage.
"""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
import struct
import subprocess
import tarfile
import tempfile
import urllib.request

ASSETS = {
    'linux-amd64': ('logosctl-x86_64-linux.tar.gz', 89234137, '41c2dffd080c6720c82ed4d0663dd39cbfc1c6aa114434179764732f2ade3096'),
    'linux-arm64': ('logosctl-aarch64-linux.tar.gz', 89018592, 'f1ed1debcac20a9943ae2786021f574e439af42f853db0e753a365acb45eb3c3'),
}
BASE = 'https://github.com/logos-co/logos-logoscore-cli/releases/download/0.2.3/'


def digest(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    if sys.version_info < (3,12): raise SystemExit('Python 3.12 or newer is required for safe archive extraction')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--platform', choices=ASSETS, default='linux-arm64' if platform.machine() in ('arm64', 'aarch64') else 'linux-amd64')
    args = parser.parse_args()
    output = args.output.expanduser().absolute()
    if output.is_symlink(): raise SystemExit('Refusing a symlink output directory')
    output.mkdir(parents=True, exist_ok=True)
    final = output / 'runtime'
    if final.exists():
        saved = json.loads((output / 'paths.json').read_text())
        if saved.get('platform') != args.platform or any(digest(Path(p)) != h for p,h in saved['library_hashes'].items()):
            raise SystemExit('Existing runtime differs; inspect it instead of replacing it')
        print(json.dumps(saved, indent=2)); return
    name, size, expected = ASSETS[args.platform]
    archive = output / name
    if not archive.exists():
        temporary = archive.with_suffix('.download')
        if temporary.exists(): raise SystemExit('Partial download exists; inspect before retrying')
        request = urllib.request.Request(BASE + name, headers={'User-Agent':'Kite-pinned-native-build/1'})
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open('xb') as stream:
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > size: raise SystemExit('Runtime download exceeded its pinned size')
                stream.write(chunk)
        if temporary.stat().st_size != size or digest(temporary) != expected:
            raise SystemExit('Pinned runtime archive verification failed')
        temporary.rename(archive)
    if archive.is_symlink() or archive.stat().st_size != size or digest(archive) != expected:
        raise SystemExit('Existing archive does not match the pinned official release')
    with tempfile.TemporaryDirectory(prefix='extract-', dir=output) as work:
        unpacked = Path(work) / 'unpacked'; unpacked.mkdir()
        with tarfile.open(archive, 'r:gz') as tar:
            members = tar.getmembers()
            if len(members) > 20000 or sum(m.size for m in members) > 2 * 1024**3:
                raise SystemExit('Runtime archive exceeds extraction limits')
            for member in members:
                name_path = PurePosixPath(member.name)
                if name_path.is_absolute() or '..' in name_path.parts:
                    raise SystemExit('Unsafe runtime archive path')
            tar.extractall(unpacked, filter='data')
        libraries = list(unpacked.rglob('liblogos_qt_host.so'))
        if not libraries:
            images = list(unpacked.rglob('*.AppImage'))
            if len(images) != 1 or images[0].is_symlink(): raise SystemExit('Expected one official runtime AppImage')
            image = images[0]; data = image.read_bytes(); candidates = []
            at = data.find(b'hsqs')
            while at != -1:
                if at + 96 <= len(data):
                    block = struct.unpack_from('<I',data,at+12)[0]
                    major = struct.unpack_from('<H',data,at+28)[0]
                    used = struct.unpack_from('<Q',data,at+40)[0]
                    if major == 4 and 4096 <= block <= 1048576 and block & (block-1) == 0 and 96 <= used <= len(data)-at:
                        candidates.append(at)
                at = data.find(b'hsqs',at+4)
            if len(candidates) != 1: raise SystemExit('Ambiguous AppImage filesystem; no extraction attempted')
            if not shutil.which('unsquashfs'): raise SystemExit('Install squashfs-tools on Linux to extract the verified runtime; the downloaded archive is preserved')
            subprocess.run(['unsquashfs','-no-progress','-o',str(candidates[0]),'-d',str(unpacked/'appimage'),str(image)], check=True, timeout=180, stdout=subprocess.DEVNULL)
            libraries = list(unpacked.rglob('liblogos_qt_host.so'))
        if len(libraries) != 1 or not (libraries[0].parent/'liblogos_protocol.so').is_file():
            raise SystemExit('Matching Logos runtime libraries were not found')
        relative = libraries[0].parent.relative_to(unpacked)
        for p in [libraries[0],libraries[0].parent/'liblogos_protocol.so']:
            if not p.resolve().is_relative_to(unpacked.resolve()): raise SystemExit('Library escapes extracted runtime')
        unpacked.rename(final)
    lib = final / relative
    result = {'version':'0.2.3','platform':args.platform,'archive_sha256':expected,
              'library_dir':str(lib),'runtime_executed':False,
              'library_hashes':{str(lib/n):digest(lib/n) for n in ['liblogos_qt_host.so','liblogos_protocol.so']}}
    (output/'paths.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__ == '__main__': main()
