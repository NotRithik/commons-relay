#!/usr/bin/env python3
"""Build pinned Rust code offline without embedding local checkout/cache paths."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def remap_flags(root: Path, cargo_home: Path, rustup_home: Path) -> list[str]:
    return [
        f"--remap-path-prefix={root.resolve()}=/commons",
        f"--remap-path-prefix={cargo_home.resolve()}=/cargo",
        f"--remap-path-prefix={rustup_home.resolve()}=/rustup",
    ]

def flags(kind: str, root: Path, cargo_home: Path, rustup_home: Path) -> list[str]:
    value = remap_flags(root, cargo_home, rustup_home)
    if kind == 'guest':
        value += ['--cfg', 'getrandom_backend="custom"', '-C', 'passes=lower-atomic',
                  '-C', 'link-arg=-Ttext=0x00200800', '-C', 'panic=abort', '-C', 'debuginfo=0']
    return value

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=['host', 'guest'])
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--target-dir', type=Path, required=True)
    parser.add_argument('--guest-rustc', type=Path)
    parser.add_argument('--package', action='append', default=[])
    parser.add_argument('--features')
    args = parser.parse_args()
    manifest = args.manifest.resolve(strict=True)
    if manifest.name != 'Cargo.toml':
        parser.error('manifest must be a Cargo.toml file')
    if args.kind == 'guest' and not args.guest_rustc:
        parser.error('guest builds require the pinned guest compiler')
    env = os.environ.copy()
    cargo_home = Path(env.get('CARGO_HOME', Path.home() / '.cargo'))
    rustup_home = Path(env.get('RUSTUP_HOME', Path.home() / '.rustup'))
    env['CARGO_ENCODED_RUSTFLAGS'] = '\x1f'.join(flags(args.kind, ROOT, cargo_home, rustup_home))
    env.pop('RUSTFLAGS', None)
    env['CARGO_NET_OFFLINE'] = 'true'
    env['CARGO_INCREMENTAL'] = '0'
    env['CARGO_TARGET_DIR'] = str(args.target_dir.resolve())
    env['CARGO_PROFILE_DEV_DEBUG'] = '0'
    env['CARGO_PROFILE_DEV_STRIP'] = 'debuginfo'
    command = ['cargo', '+1.94.0', 'build', '--locked', '--offline', '--manifest-path', str(manifest)]
    for package in args.package:
        command += ['-p', package]
    if args.features:
        command += ['--features', args.features]
    if args.kind == 'guest':
        env['RUSTC'] = str(args.guest_rustc.resolve(strict=True))
        command += ['--release', '--target', 'riscv32im-risc0-zkvm-elf']
    subprocess.run(command, cwd=manifest.parent, env=env, check=True)

if __name__ == '__main__':
    main()
