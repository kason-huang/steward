"""Credential handling and proxy push commands keep tokens out of Git configuration."""
import importlib.util
import io
from pathlib import Path
import subprocess
import sys

import pytest

SPEC = importlib.util.spec_from_file_location('proxy_push', Path(__file__).parents[1] / 'scripts/push_via_local_proxy.py')
push = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(push)


def test_token_parser_handles_labelled_env_and_rejects_ambiguity(tmp_path):
    path = tmp_path / '.env'
    path.write_text('\ufeffGitHub token: ghp_unitTestCredential\nDEEPSEEK_API_KEY=sk_other\n')
    assert push.read_token(path) == 'ghp_unitTestCredential'
    path.write_text('GITHUB_TOKEN=ghp_one\nOTHER=github_pat_two\n')
    with pytest.raises(ValueError, match='exactly one'):
        push.read_token(path)


def test_credential_get_is_limited_to_github_and_store_does_nothing(tmp_path, monkeypatch):
    token = tmp_path / '.env'; token.write_text('GITHUB_TOKEN=ghp_unitTestCredential')
    output = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', output)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('protocol=https\nhost=example.com\n\n'))
    assert push.credential(str(token), 'kason-huang', 'get') == 1
    assert output.getvalue() == ''
    monkeypatch.setattr(sys, 'stdin', io.StringIO('protocol=https\nhost=github.com\n\n'))
    assert push.credential(str(token), 'kason-huang', 'get') == 0
    assert output.getvalue() == 'username=kason-huang\npassword=ghp_unitTestCredential\n\n'
    output.seek(0); output.truncate()
    assert push.credential(str(token), 'kason-huang', 'store') == 0
    assert output.getvalue() == ''


@pytest.mark.parametrize('dry_run,failure', [(True, False), (False, False), (False, True)])
def test_push_verifies_refs_and_removes_helper_without_storing_token(tmp_path, monkeypatch, dry_run, failure):
    token = tmp_path / '.env'; token.write_text('ghp_unitTestCredential')
    calls, helpers = [], []
    def read(command, **kwargs):
        calls.append(command)
        if 'branch' in command: return 'feat-pi05-intergration\n'
        if 'rev-parse' in command: return 'abc123\n'
        if 'status' in command: return ''
        if 'get-url' in command: return 'https://github.com/kason-huang/steward.git\n'
        if 'ls-remote' in command: return 'abc123\trefs/heads/feat-pi05-intergration\n'
        raise AssertionError(command)
    def run(command, **kwargs):
        calls.append(command)
        setting = next(x for x in command if x.startswith('credential.helper=!'))
        import shlex
        helper = Path(shlex.split(setting.split('!', 1)[1])[-1])
        helpers.append(helper)
        assert helper.exists() and 'ghp_unitTestCredential' not in helper.read_text()
        assert '--force' not in command and '--force-with-lease' not in command
        assert ('--dry-run' in command) == dry_run
        if failure: raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(push.subprocess, 'check_output', read)
    monkeypatch.setattr(push.subprocess, 'run', run)
    args = ['--repo', str(tmp_path), '--token-file', str(token)] + (['--dry-run'] if dry_run else [])
    if failure:
        with pytest.raises(subprocess.CalledProcessError): push.main(args)
    else:
        assert push.main(args) == 0
        assert any('ls-remote' in c for c in calls) == (not dry_run)
    assert all(not h.exists() for h in helpers)
    assert all('ghp_unitTestCredential' not in str(c) for c in calls)
