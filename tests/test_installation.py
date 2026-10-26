"""Exercise the actual installer with host operations confined to a fixture."""
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


def install_fixture(source, destination):
    source=Path(source);destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=True)
    script=(source/'deploy/install-openeuler.sh').read_text()
    for actual,local in [('/home/sglang-npu-bot',destination/'bot'),
                         ('/etc/sglang-npu-bot',destination/'config'),
                         ('/etc/systemd/system',destination/'units')]:
        script=script.replace(actual,str(local))
    assert ' /etc/' not in script and '\nBOT_HOME=/home/sglang-npu-bot' not in script
    script=re.sub(r'^SOURCE_ROOT=.*$', 'SOURCE_ROOT='+shlex.quote(str(source)),script,count=1,flags=re.M)
    (destination/'units').mkdir(exist_ok=True)
    # Keep real file installation and modes; suppress account/service mutation.
    prelude=r'''
id() { printf '0\n'; }
usermod() { :; }
useradd() { echo 'Unexpected account creation' >&2; return 1; }
systemctl() { :; }
install() {
  local fixture_args=()
  while (( $# )); do
    case "$1" in
      -o|-g) shift 2 ;;
      *) fixture_args+=("$1"); shift ;;
    esac
  done
  command install "${fixture_args[@]}"
}
'''
    result=subprocess.run(['bash','-e'],input=prelude+'\n'+script,text=True,
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError('Fixture installation failed:\n'+result.stdout+result.stderr)
    return destination/'bot'


class InstallationTests(unittest.TestCase):
    def test_installed_python_inventory_and_imports_match_source(self):
        with tempfile.TemporaryDirectory(prefix='npu-ci-installer-') as directory:
            base=Path(directory);bot=install_fixture(ROOT,base/'home/sglang-npu-bot')
            source={p.name:p.read_bytes() for p in (ROOT/'scripts').glob('*.py')}
            installed={p.name:p.read_bytes() for p in (bot/'scripts').glob('*.py')}
            self.assertEqual(set(installed),set(source))
            for name,content in source.items():
                with self.subTest(module=name):self.assertEqual(installed[name],content)
            fixtures={p.name:p.read_bytes() for p in (ROOT/'tests/fixtures').iterdir() if p.is_file()}
            deployed_fixtures={p.name:p.read_bytes() for p in (bot/'tests/fixtures').iterdir() if p.is_file()}
            self.assertEqual(set(deployed_fixtures),set(fixtures))
            for name,content in fixtures.items():
                with self.subTest(fixture=name):self.assertEqual(deployed_fixtures[name],content)
            self.assertEqual({p.name for p in (bot/'tests').glob('test_*.py')},
                             {p.name for p in (ROOT/'tests').glob('test_*.py')})
            # Reinstall from a deployed path: destination rewriting must preserve SOURCE_ROOT.
            bot=install_fixture(bot,base/'updated')
            isolated=base/'isolated';isolated.mkdir()
            env=dict(os.environ,PYTHONPATH=str(bot))
            modules=['analysis_recovery','collection_evidence','complete_report','model_evidence',
                     'preview_report','recover_daily_report','testcase_observations']
            code='import importlib\nfor name in '+repr(modules)+': importlib.import_module("scripts."+name)'
            subprocess.run([sys.executable,'-c',code],cwd=isolated,env=env,check=True)
            subprocess.run(['bash',str(bot/'deploy/self-test.sh')],cwd=isolated,env=env,check=True)

    def test_update_preserves_configuration_and_reports(self):
        with tempfile.TemporaryDirectory(prefix='npu-ci-installer-') as directory:
            base=Path(directory);target=base/'target'
            (target/'config').mkdir(parents=True)
            config=target/'config/bot.env';config.write_text('GITHUB_TOKEN=fixture-placeholder\n')
            reports=target/'bot/reports';reports.mkdir(parents=True)
            report=reports/'saved.json';report.write_text('{"preserve":true}\n')
            bot=install_fixture(ROOT,target)
            self.assertEqual(config.read_text(),'GITHUB_TOKEN=fixture-placeholder\n')
            self.assertEqual(report.read_text(),'{"preserve":true}\n')
            self.assertEqual((bot/'scripts/recover_daily_report.py').stat().st_mode&0o777,0o644)
            self.assertEqual((bot/'scripts/reparse_failure_snapshots.py').stat().st_mode&0o777,0o755)



if __name__=='__main__':
    unittest.main()
