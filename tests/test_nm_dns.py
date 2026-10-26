"""NetworkManager DNS configuration tests. Pure mocks; NEVER change host DNS."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

P = Path(__file__).resolve().parent.parent / 'deploy' / 'configure-dns.py'
spec = importlib.util.spec_from_file_location('nm_dns', P)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

DATA = {'connection': 'Wired connection 1', 'uuid': 'db82a2fd-d401-4010-b404-d633f341dbd4',
        'ipv4.dns': '8.8.8.8', 'ipv4.ignore-auto-dns': 'no'}


class TestNmDns(unittest.TestCase):
    def test_normalize_servers(self):
        self.assertEqual(mod.normalize_servers(' 8.8.8.8, 223.5.5.5 '), '8.8.8.8 223.5.5.5')
        with self.assertRaises(ValueError): mod.normalize_servers('8.8.8.8,8.8.8.8')
        with self.assertRaises(ValueError): mod.normalize_servers('8.8.8.8,host.example')

    def test_backup_keeps_original_on_second_apply(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            a = mod.persist_backup(DATA, directory)
            self.assertEqual(a.stat().st_mode & 0o777, 0o600)
            changed = dict(DATA, **{'ipv4.dns': '1.1.1.1'})
            mod.persist_backup(changed, directory)
            self.assertEqual(json.loads(a.read_text())['ipv4.dns'], '8.8.8.8')

    def test_rollback_restores_values(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            mod.persist_backup(DATA, directory)
            with patch.object(mod, 'get_props', return_value=dict(DATA)), \
                 patch.object(mod, 'nmcli') as run:
                result = mod.restore(DATA['connection'], directory)
            self.assertEqual(result['ipv4.dns'], '8.8.8.8')
            run.assert_called_once_with('connection', 'modify', DATA['connection'],
                                        'ipv4.dns', '8.8.8.8', 'ipv4.ignore-auto-dns', 'no')

    def test_rollback_without_backup_is_safe(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(mod, 'get_props', return_value=DATA), \
                 patch.object(mod, 'nmcli') as cmd:
                with self.assertRaisesRegex(RuntimeError, 'backup not found'):
                    mod.restore(DATA['connection'], Path(d))
                cmd.assert_not_called()

    def test_list_only_no_modify(self):
        with patch.object(mod, 'nmcli', return_value='NAME  UUID  TYPE  DEVICE') as run:
            self.assertEqual(mod.main(['--list']), 0)
            run.assert_called_once_with('-f', 'NAME,UUID,TYPE,DEVICE', 'connection', 'show', '--active')

    def test_apply_explicit_and_reapply_opt_in(self):
        with patch.object(mod.os, 'geteuid', return_value=0), \
             patch.object(mod, 'get_props', return_value=DATA), \
             patch.object(mod, 'persist_backup', return_value=Path('/etc/backup.json')), \
             patch.object(mod, 'nmcli') as cmd:
            self.assertEqual(mod.main(['--apply', '--connection', DATA['connection'],
                                       '--servers', '8.8.8.8,223.5.5.5']), 0)
            cmd.assert_called_once_with('connection', 'modify', DATA['connection'],
                                        'ipv4.dns', '8.8.8.8 223.5.5.5', 'ipv4.ignore-auto-dns', 'yes')


if __name__ == '__main__':
    unittest.main()
