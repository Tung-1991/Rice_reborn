"""Linux launcher behavior under fake service commands; never touches the host service."""
import os
from pathlib import Path
import shlex
import subprocess

import pytest

pytestmark=pytest.mark.skipif(os.name!='posix',reason='Bash behavior is exercised on Linux/WSL')
SCRIPT=Path(__file__).resolve().parents[2]/'deploy/rice-reborn.sh'


def shell(code,workdir,input=None):
    return subprocess.run(['bash','-c','source '+shlex.quote(str(SCRIPT))+'\n'+code],cwd=workdir,text=True,capture_output=True,timeout=10,input=input)


def test_launcher_syntax_and_simple_menu_operations(tmp_path):
    result=subprocess.run(['bash','-n',str(SCRIPT)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    result=shell('for number in 1 2 3 4; do menu_action "$number"; done',tmp_path)
    assert result.stdout.splitlines()==['open','update','overview','maintenance']


@pytest.mark.parametrize('installed',[True,False])
def test_open_installs_once_and_only_starts_existing_app(tmp_path,installed):
    result=shell(f'''
require_root() {{ :; }}
app_installed() {{ return {0 if installed else 1}; }}
install_or_clone() {{ echo INSTALL; }}
service_command() {{ echo "SERVICE $1"; }}
open_app
''',tmp_path)
    assert result.returncode==0,result.stderr
    assert ('INSTALL' in result.stdout)==(not installed)
    assert ('SERVICE start' in result.stdout)==installed


def test_menu_navigation_password_and_exit_never_stop_backend(tmp_path):
    result=shell('''
require_root() { :; }
app_installed() { return 1; }
run_menu_action() {
  if [[ "$1" == maintenance ]]; then maintenance_menu
  else printf 'ACTION=%s\\n' "$1"; fi
}
menu
''',tmp_path,input='4\n3\n0\n0\n')
    assert result.returncode==0,result.stderr
    assert 'ACTION=password' in result.stdout and 'ACTION=stop' not in result.stdout
    assert 'Chưa cài đặt' in result.stdout and 'Rice vẫn chạy' in result.stdout


def test_failed_action_reports_failure_and_keeps_menu_available(tmp_path):
    result=shell('''
require_root() { :; }
app_installed() { return 1; }
run_menu_action() { return 77; }
menu
''',tmp_path,input='1\n0\n')
    assert result.returncode==0,result.stderr
    assert 'Chưa hoàn tất.' in result.stdout and 'Hoàn tất.' not in result.stdout


def test_status_before_install_does_not_query_or_change_service(tmp_path):
    result=shell('''
require_root() { :; }
app_installed() { return 1; }
systemctl() { exit 93; }
journalctl() { exit 94; }
quick_status
''',tmp_path)
    assert result.returncode==0 and 'Chưa cài Rice.' in result.stdout


@pytest.mark.parametrize('choice,mode',[('1','ssh'),('2','direct'),('3','https')])
def test_network_menu_changes_only_selected_access_mode(tmp_path,choice,mode):
    result=shell('''
show_access() { :; }
with_stopped() { printf 'ACCESS=%s\\n' "$*"; }
network_menu
''',tmp_path,input=choice+'\n')
    assert result.returncode==0,result.stderr
    assert f'ACCESS=access-set --mode {mode} --allow-ips' in result.stdout


def test_readonly_check_never_starts_service_or_installs(tmp_path):
    result=shell("""
require_root() { :; }
runtime() { printf 'runtime %s\\n' "$*"; }
apt-get() { exit 91; }
dnf() { exit 92; }
systemctl() { exit 93; }
timedatectl() { printf 'yes\\n'; }
check_environment
""",tmp_path)
    assert result.returncode==0,result.stderr
    assert 'maintenance check' in result.stdout and 'pip check' in result.stdout


def make_release(app,name):
    path=app/'venvs'/name
    (path/'bin').mkdir(parents=True)
    executable=path/'bin/python'
    executable.write_text('#!/bin/sh\nexit 0\n')
    executable.chmod(0o755)
    return path


@pytest.mark.parametrize('healthy',[True,False])
def test_environment_switch_and_failed_health_rolls_back(tmp_path,healthy):
    app=tmp_path/'app'; app.mkdir()
    old=app/'.venv'; (old/'bin').mkdir(parents=True)
    (old/'bin/python').write_text('OLD')
    candidate=make_release(app,'candidate')
    (app/'data').mkdir(); sentinel=app/'data/financial-sentinel'
    sentinel.write_text('KEEP')
    unit=tmp_path/'rice-reborn.service'; unit.write_text('OLD UNIT')
    calls=tmp_path/'calls'
    code=f'''
APP_DIR={shlex.quote(str(app))}
UNIT_PATH={shlex.quote(str(unit))}
service_active() {{ return 0; }}
systemctl() {{ printf '%s\\n' "$*" >> {shlex.quote(str(calls))}; }}
wait_healthy() {{ return {0 if healthy else 1}; }}
if activate_environment {shlex.quote(str(candidate))}; then echo RESULT=OK; else echo RESULT=FAILED; fi
'''
    result=shell(code,tmp_path)
    assert result.returncode==0,result.stderr
    assert sentinel.read_text()=='KEEP'
    if healthy:
        assert (app/'.venv').resolve()==candidate
        assert 'ProtectSystem=strict' in unit.read_text()
        assert f'ReadWritePaths={app}/data' in unit.read_text()
        assert 'RESULT=OK' in result.stdout
    else:
        assert (app/'.venv').is_symlink()
        assert ((app/'.venv')/'bin/python').read_text()=='OLD'
        assert unit.read_text()=='OLD UNIT'
        assert 'RESULT=FAILED' in result.stdout and '[ROLLBACK]' in result.stderr
    assert calls.read_text().splitlines()[0]=='stop rice-reborn.service'


def test_candidate_outside_app_rejected_before_service_stop(tmp_path):
    outside=make_release(tmp_path/'outside','candidate')
    result=shell(f'''
APP_DIR={shlex.quote(str(tmp_path/'app'))}
service_active() {{ echo SHOULD_NOT_RUN; return 0; }}
if activate_environment {shlex.quote(str(outside))}; then exit 99; fi
''',tmp_path)
    assert result.returncode==0 and 'SHOULD_NOT_RUN' not in result.stdout


def test_dirty_checkout_never_pulls_or_installs(tmp_path):
    result=shell("""
require_root() { :; }
resolve_source() { pwd; }
ensure_git() { :; }
git() {
  case "$*" in
    *rev-parse*) pwd ;;
    *status*) printf ' M changed.py\\n' ;;
    *pull*) echo SHOULD_NOT_PULL; exit 95 ;;
  esac
}
if update_app; then exit 99; fi
""",tmp_path)
    assert result.returncode==0,result.stderr
    assert 'SHOULD_NOT_PULL' not in result.stdout

def test_zip_clone_failure_keeps_current_source_and_service(tmp_path):
    app=tmp_path/'app'; app.mkdir()
    sentinel=app/'keep'; sentinel.write_text('KEEP')
    result=shell(f'''
APP_DIR={shlex.quote(str(app))}
require_root() {{ :; }}
resolve_source() {{ pwd; }}
ensure_git() {{ :; }}
systemctl() {{ exit 94; }}
git() {{ case "$*" in *rev-parse*) return 1 ;; clone*) return 77 ;; esac; }}
update_app
''',tmp_path,input='https://example.invalid/source.git\n')
    assert result.returncode==77 and sentinel.read_text()=='KEEP'

def test_repository_url_does_not_accept_embedded_credentials(tmp_path):
    result=shell('read_repo_url',tmp_path,input='https://name:secret@example.invalid/repo.git\n')
    assert result.returncode!=0 and 'secret' not in result.stdout
