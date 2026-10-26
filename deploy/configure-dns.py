#!/usr/bin/env python3
"""Safe, opt-in NetworkManager DNS configuration with original-value backup.

Never runs during installation; changes network settings only with --apply or
--rollback. Use --device for an explicit reapply (may disturb an SSH session).
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import subprocess
import sys
from pathlib import Path

BACKUP_DIR = Path('/etc/sglang-npu-bot/dns-backups')


def nmcli(*args: str) -> str:
    out = subprocess.run(['nmcli', *args], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def normalize_servers(csv: str) -> str:
    raw = [s.strip() for s in csv.split(',')]
    if not 1 <= len(raw) <= 5 or any(not s for s in raw):
        raise ValueError('Provide 1..5 comma-separated IPv4 DNS server addresses')
    ip4 = [str(ipaddress.IPv4Address(s)) for s in raw]
    if len(set(ip4)) != len(ip4):
        raise ValueError('Duplicate DNS server')
    return ' '.join(ip4)


def get_props(connection: str) -> dict:
    uuid = nmcli('-g', 'connection.uuid', 'connection', 'show', connection)
    if not re.fullmatch(r'[0-9a-fA-F-]{36}', uuid):
        raise RuntimeError('Invalid NetworkManager connection UUID')
    return {'connection': connection, 'uuid': uuid,
            'ipv4.dns': nmcli('-g', 'ipv4.dns', 'connection', 'show', connection),
            'ipv4.ignore-auto-dns': nmcli('-g', 'ipv4.ignore-auto-dns', 'connection', 'show', connection)}


def backup_path(uuid: str, directory: Path = BACKUP_DIR) -> Path:
    return directory / f'{uuid}.json'


def persist_backup(data: dict, directory: Path = BACKUP_DIR) -> Path:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = backup_path(data['uuid'], directory)
    # Do not overwrite the true original settings on a second apply.
    if not path.exists():
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
            f.write('\n')
    return path


def restore(connection: str, directory: Path = BACKUP_DIR) -> dict:
    current = get_props(connection)
    path = backup_path(current['uuid'], directory)
    if not path.exists():
        raise RuntimeError(f'Original DNS settings backup not found: {path}')
    original = json.loads(path.read_text(encoding='utf-8'))
    if original['uuid'] != current['uuid'] or original['connection'] != connection:
        raise RuntimeError('DNS backup does not match selected connection')
    nmcli('connection', 'modify', connection,
          'ipv4.dns', original['ipv4.dns'],
          'ipv4.ignore-auto-dns', original['ipv4.ignore-auto-dns'])
    return original


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--list', action='store_true', help='List active NetworkManager connections')
    mode.add_argument('--apply', action='store_true', help='Back up then change DNS configuration')
    mode.add_argument('--rollback', action='store_true', help='Restore the original DNS settings')
    p = parser
    p.add_argument('--connection', help='NetworkManager connection name')
    p.add_argument('--servers', default='8.8.8.8,223.5.5.5',
                   help='Comma-separated IPv4 DNS servers (default: 8.8.8.8,223.5.5.5)')
    p.add_argument('--device', help='Optional device to run nmcli device reapply on (e.g. enp3s0)')
    args = p.parse_args(argv)
    if not args.list and not args.connection:
        p.error('--connection is required for apply and rollback')
    if (args.apply or args.rollback) and os.geteuid() != 0:
        p.error('Run via sudo as root for apply or rollback')
    try:
        if args.list:
            print(nmcli('-f', 'NAME,UUID,TYPE,DEVICE', 'connection', 'show', '--active'))
        elif args.apply:
            dns = normalize_servers(args.servers)
            original = get_props(args.connection)
            path = persist_backup(original)
            nmcli('connection', 'modify', args.connection,
                  'ipv4.dns', dns, 'ipv4.ignore-auto-dns', 'yes')
            print(f'[DNS] Saved original settings: {path}')
            print(f'[DNS] Updated {args.connection}: ipv4.dns={dns}; ignore-auto-dns=yes')
            print('[DNS] This updates the NetworkManager profile. Use --device to reapply without reconnecting.')
        else:
            restored = restore(args.connection)
            print(f'[DNS] Restored {args.connection}: ipv4.dns={restored["ipv4.dns"] or "<automatic>"}; '
                  f'ignore-auto-dns={restored["ipv4.ignore-auto-dns"]}')
        if args.device and not args.list:
            print(f'[DNS] WARNING: reapplying {args.device} might briefly interrupt remote access.', flush=True)
            nmcli('device', 'reapply', args.device)
            print('[DNS] Device reapply completed. Verify DNS and HTTPS with npu_network_check.py.')
        return 0
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError, KeyError, json.JSONDecodeError) as exc:
        print(f'[DNS] FAILED: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
