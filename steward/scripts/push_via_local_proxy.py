"""Push one GitHub HTTPS branch through a forwarded local proxy without storing credentials."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit


def read_token(path: Path) -> str:
    """Read the single GitHub token in an env file or labelled token line."""
    tokens = set(re.findall(r'(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]+',
                            path.read_text(encoding='utf-8-sig')))
    if len(tokens) != 1:
        raise ValueError('The token file must contain exactly one distinct GitHub token')
    return tokens.pop()


def credential(token_file: str, username: str, operation: str) -> int:
    """Answer Git's credential pipe for github.com; ignore store/erase operations."""
    if operation != 'get':
        return 0
    fields = {}
    for line in sys.stdin:
        if not line.strip():
            break
        key, value = line.rstrip('\n').split('=', 1)
        fields[key] = value
    if fields.get('protocol') != 'https' or fields.get('host') != 'github.com':
        return 1
    token = read_token(Path(token_file))
    sys.stdout.write(f'username={username}\npassword={token}\n\n')
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--branch', default='feat-pi05-intergration')
    parser.add_argument('--remote', default='origin')
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--proxy', default='http://127.0.0.1:17892')
    parser.add_argument('--token-file', type=Path, default=Path('/home/huawei/hqk/workspace/.env'))
    parser.add_argument('--username', default='kason-huang')
    parser.add_argument('--dry-run', action='store_true', help='Authenticate and check the push without updating the branch')
    args = parser.parse_args(argv)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('GIT_TRACE') and k != 'GIT_CURL_VERBOSE'}
    env['GIT_TERMINAL_PROMPT'] = '0'
    def git_read(*command):
        return subprocess.check_output(['git', *command], cwd=args.repo, env=env,
                                       text=True, timeout=30).strip()

    if git_read('branch', '--show-current') != args.branch:
        raise ValueError('Check out the branch named by --branch before pushing')
    local_head = git_read('rev-parse', 'HEAD')
    if git_read('status', '--porcelain', '--untracked-files=no'):
        raise ValueError('Commit or stash tracked working-tree changes before pushing')
    url = urlsplit(git_read('remote', 'get-url', '--push', args.remote))
    if url.scheme != 'https' or url.hostname != 'github.com' or url.username or url.password:
        raise ValueError('This helper requires a GitHub HTTPS remote without embedded credentials')
    read_token(args.token_file)  # Check the file before starting network work; never print the value.
    with tempfile.TemporaryDirectory(prefix='steward-git-auth-') as directory:
        helper = Path(directory) / 'credential.py'
        helper.write_text('import runpy,sys\n'
            f"module=runpy.run_path({str(Path(__file__).resolve())!r})\n"
            f"sys.exit(module['credential']({str(args.token_file.resolve())!r}, {args.username!r}, sys.argv[-1]))\n")
        helper.chmod(0o700)
        command = ['git', '-c', 'http.proxy=' + args.proxy,
                   '-c', 'credential.helper=',
                   '-c', 'credential.helper=!' + shlex.join([sys.executable, str(helper)])]
        ref = 'refs/heads/' + args.branch
        push = command + ['push'] + (['--dry-run'] if args.dry_run else [])
        subprocess.run(push + [args.remote, f'{ref}:{ref}'], cwd=args.repo, env=env,
                       check=True, timeout=180)
        if args.dry_run:
            print('Dry-run completed; no remote branch was updated.')
        else:
            remote_head = subprocess.check_output(command + ['ls-remote', args.remote, ref],
                cwd=args.repo, env=env, text=True, timeout=30).split()[0]
            if remote_head != local_head:
                raise ValueError('Remote verification differs from the local commit; inspect both refs')
            print(f'Verified {args.remote}/{args.branch}: {remote_head}')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'Push failed: {error}', file=sys.stderr)
        raise SystemExit(1)
