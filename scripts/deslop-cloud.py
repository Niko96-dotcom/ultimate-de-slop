#!/usr/bin/env python3
"""File handoff between the deterministic loop and a host's native subagents.

No provider CLI, network service, credentials, or third-party packages required.
The parent services requests; existing stage code retains validation and ownership.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

SCRIPT = Path(__file__).resolve()


def atomic(path, value, *, exclusive=False):
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    if exclusive:
        try:
            os.link(temp, path)
        finally:
            temp.unlink(missing_ok=True)
    else:
        temp.replace(path)


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (ProcessLookupError, ValueError, TypeError):
        return False


def root():
    return Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip()).resolve()


def locked(path):
    handle = path.open('a')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise ValueError('Another cloud controller is active; poll it instead.')
    return handle


def request(args):
    repo = root()
    folder = args.prompt.resolve().parent
    path = folder / 'native-request.json'
    response = folder / 'native-response.json'
    ident = uuid.uuid4().hex
    payload = dict(id=ident, pid=os.getpid(), status='pending', root=str(repo),
                   kind=args.kind, readonly=args.readonly, prompt=str(args.prompt.resolve()),
                   schema=str(args.schema.resolve()), response=str(response),
                   created_at=time.time(), expires_at=time.time() + args.timeout)
    # The role stage has a unique run directory; never reuse an old response.
    response.unlink(missing_ok=True)
    atomic(path, payload)
    def interrupted(signum, frame):
        raise InterruptedError('Native request cancelled')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        while time.time() < payload['expires_at']:
            if (repo / '.deslop/stop').exists():
                raise InterruptedError('Stop file present')
            if response.exists():
                answer = json.loads(response.read_text())
                if answer.get('id') != ident or not isinstance(answer.get('result'), dict):
                    raise ValueError('Response must match this request and contain a JSON object result')
                payload['status'] = 'completed'
                print(json.dumps(answer['result']), flush=True)
                return 0
            time.sleep(.2)
        raise TimeoutError('Native subagent response deadline exceeded')
    except (InterruptedError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        payload['status'] = 'cancelled'
        payload['error'] = str(exc)
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        atomic(path, payload)


def pending(repo):
    items = []
    for path in sorted((repo / '.deslop/runs').glob('*/native-request.json')):
        item = json.loads(path.read_text())
        if item.get('status') == 'pending' and alive(item.get('pid')) and time.time() < item['expires_at']:
            # Never accept a response destination supplied by mutable request JSON.
            item['response'] = str(path.parent / 'native-response.json')
            if item.get('root') != str(repo.resolve()):
                raise ValueError('Native request belongs to another checkout')
            items.append(item)
    return items


def submit(repo, ident, result_file):
    items = [item for item in pending(repo) if item['id'] == ident]
    if len(items) != 1:
        raise ValueError('No live pending request with that ID; do not replay stale results')
    item = items[0]
    response = Path(item['response'])
    if response.exists():
        raise ValueError('Response already submitted')
    result = json.loads(result_file.read_text())
    if not isinstance(result, dict):
        raise ValueError('Subagent result must be a JSON object')
    atomic(response, {'id': ident, 'result': result}, exclusive=True)
    return {'submitted': ident}


def supervise(repo, arguments):
    # An inherited lock prevents duplicate launches even before this process starts.
    lock_fd = int(os.environ['DESLOP_CLOUD_LOCK_FD'])
    session_file = repo / '.deslop/cloud-session.json'
    session = dict(pid=os.getpid(), status='running', started_at=time.time(), arguments=arguments)
    atomic(session_file, session)
    env = os.environ.copy()
    env['DESLOP_HARNESS'] = 'native'
    env.pop('DESLOP_CLOUD_LOCK_FD', None)
    command = [str(SCRIPT.parent / 'deslop-loop.sh'), *arguments]
    if arguments and arguments[0] == '--review-only':
        command = [str(SCRIPT.parent / 'deslop-review.sh'), *arguments[1:]]
    review_lock = False
    try:
        if arguments and arguments[0] == '--review-only':
            from deslop_loop_support import acquire_loop_lock
            acquire_loop_lock(repo)
            review_lock = True
        process = subprocess.Popen(command, cwd=repo, env=env)
        def stop(signum, frame):
            (repo / '.deslop/stop').touch()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        session['exit_code'] = process.wait()
    except Exception as exc:
        session.update(exit_code=1, error=str(exc))
    finally:
        if review_lock:
            from deslop_loop_support import release_loop_lock
            release_loop_lock(repo)
        session.update(status='finished', ended_at=time.time())
        atomic(session_file, session)
        os.close(lock_fd)
    return session['exit_code']


def start(repo, arguments):
    folder = repo / '.deslop'
    folder.mkdir(exist_ok=True)
    (folder / 'tmp').mkdir(exist_ok=True)
    handle = locked(folder / 'cloud-controller.lock')
    session_file = folder / 'cloud-session.json'
    old = json.loads(session_file.read_text()) if session_file.exists() else {}
    if old.get('status') in ('starting', 'running') and alive(old.get('pid')):
        handle.close()
        raise ValueError('Cloud controller is already running; poll instead')
    env = os.environ.copy()
    env['DESLOP_CLOUD_LOCK_FD'] = str(handle.fileno())
    atomic(session_file, {'status': 'starting', 'started_at': time.time()})
    try:
        with (folder / 'cloud-loop.log').open('ab') as log:
            proc = subprocess.Popen([sys.executable, str(SCRIPT), 'supervise', '--', *arguments],
                                    cwd=repo, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    start_new_session=True, pass_fds=(handle.fileno(),))
        # Supervisor owns the session record; parent does not race its updates.
        return {'started_pid': proc.pid, 'next': 'poll', 'log': str(folder / 'cloud-loop.log')}
    except OSError as exc:
        atomic(session_file, {'status': 'finished', 'exit_code': 1, 'error': str(exc)})
        raise
    finally:
        handle.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    for name in ('start', 'supervise'):
        commands.add_parser(name).add_argument('arguments', nargs=argparse.REMAINDER)
    p = commands.add_parser('poll')
    p.add_argument('--wait', type=float, default=0, help='wait up to 30 seconds for a request or completion')
    p = commands.add_parser('submit')
    p.add_argument('id')
    p.add_argument('result', type=Path)
    commands.add_parser('stop')
    p = commands.add_parser('request')
    p.add_argument('--prompt', type=Path, required=True)
    p.add_argument('--schema', type=Path, required=True)
    p.add_argument('--kind', required=True)
    p.add_argument('--readonly', action='store_true')
    p.add_argument('--timeout', type=float, default=5400)
    args = parser.parse_args()
    root_path = root()
    if args.action == 'request':
        return request(args)
    if args.action in ('start', 'supervise'):
        arguments = args.arguments
        if arguments[:1] == ['--']:
            arguments = arguments[1:]
        arguments = arguments or ['--until-clean']
        if args.action == 'supervise':
            return supervise(root_path, arguments)
        result = start(root_path, arguments)
    elif args.action == 'submit':
        result = submit(root_path, args.id, args.result)
    elif args.action == 'stop':
        (root_path / '.deslop').mkdir(exist_ok=True)
        (root_path / '.deslop/stop').touch()
        result = {'stop_requested': True, 'instruction': 'Cancel any native subagent and wait for it to stop before recovery.'}
    else:
        deadline = time.monotonic() + min(30, max(0, args.wait))
        while True:
            session_path = root_path / '.deslop/cloud-session.json'
            session = json.loads(session_path.read_text()) if session_path.exists() else {'status': 'not_started'}
            if session.get('status') == 'running' and not alive(session.get('pid')):
                session['status'] = 'interrupted'
            result = {'session': session, 'requests': pending(root_path)}
            if result['requests'] or session['status'] in ('finished', 'interrupted') or time.monotonic() >= deadline:
                break
            time.sleep(.2)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f'deslop-cloud: {exc}', file=sys.stderr)
        raise SystemExit(1)
