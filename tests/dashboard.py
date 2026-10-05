"""Disposable UI/controller/fzf acceptance; no user data, secrets or LAN ports."""
import hashlib
import json
import os
from pathlib import Path
import pty
import re
import select
import subprocess
import time
import uuid

IMAGE = os.environ.get('IMAGE', 'clashctl:test')
OWNER = 'clashctl-ui-' + uuid.uuid4().hex
containers = []
volume = None
ledger = {'owner': OWNER, 'image': IMAGE, 'containers': [], 'volumes': []}


def run(*args, check=True):
    p = subprocess.run(args, capture_output=True, text=True, timeout=90)
    if check and p.returncode:
        raise AssertionError(f'Command failed: {args}: {p.stderr}')
    return p.stdout.strip()


def docker(*args):
    if args and args[0] == 'logs':
        p = subprocess.run(['docker', *args], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, timeout=90, check=True)
        return p.stdout.strip()
    return run('docker', *args)


def exec_(cid, *args):
    return docker('exec', cid, *args)


def record():
    if os.environ.get('RESOURCE_LEDGER'):
        Path(os.environ['RESOURCE_LEDGER']).write_text(json.dumps(ledger, indent=2))


def start(bind=None):
    assert volume is not None
    args = ['docker', 'create', '--name', OWNER + '-' + str(len(containers)),
            '--label', 'test.owner=' + OWNER, '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '-e', 'SUB_UPDATE_INTERVAL=0',
            '-v', volume + ':/data']
    if bind is not None:
        args += ['-e', 'CONTROLLER_BIND=' + bind, '-p', '127.0.0.1::9090']
    cid = run(*args, IMAGE)
    containers.append(cid)
    ledger['containers'].append(cid)
    record()
    docker('start', cid)
    for _ in range(100):
        status = run('docker', 'exec', cid, 'clashctl', 'status', check=False)
        if 'RUNNING' in status:
            return cid
        state = json.loads(docker('inspect', cid))[0]['State']
        if not state['Running']:
            raise AssertionError('Startup failed: ' + docker('logs', cid))
        time.sleep(.2)
    raise AssertionError('Readiness timeout')


def probe(cid, external=False):
    # Secret stays inside the container: no stdout, argv or host evidence values.
    script = r'''
import json, pathlib, re, urllib.request, urllib.error
secret=pathlib.Path('/data/controller.secret').read_text().strip()
assert len(secret) >= 32
assert pathlib.Path('/data/controller.secret').stat().st_mode & 0o777 == 0o600
base='http://127.0.0.1:9090'
def req(path, auth=False, method='GET', data=None):
    r=urllib.request.Request(base+path, data=data, method=method,
       headers={'Authorization':'Bearer '+secret} if auth else {})
    return urllib.request.urlopen(r)
try:
    req('/version')
    raise AssertionError('Unauthenticated API accepted')
except urllib.error.HTTPError as e:
    assert e.code == 401
assert json.load(req('/version',True))['meta']
html=req('/ui/').read().decode()
assert '<html' in html and '<script' in html
assets=re.findall(r'(?:src|href)="([^"]+\.(?:js|css))"',html)
assert assets
for asset in assets:
    path=asset if asset.startswith('/') else '/ui/'+asset.removeprefix('./')
    assert len(req(path).read()) > 0
old=json.load(req('/configs',True))['mode']
new='direct' if old != 'direct' else 'rule'
req('/configs',True,'PATCH',json.dumps({'mode':new}).encode())
assert json.load(req('/configs',True))['mode'] == new
req('/configs',True,'PATCH',json.dumps({'mode':old}).encode())
assert json.load(req('/configs',True))['mode'] == old
print('PASS: UI HTML/JS/CSS, API 401/authentication and mode change/readback')
'''
    print(exec_(cid, 'python3', '-c', script), flush=True)
    if external:
        info = json.loads(docker('inspect', cid))[0]
        port = info['NetworkSettings']['Ports']['9090/tcp'][0]
        assert port['HostIp'] == '127.0.0.1'
        html = run('curl', '--noproxy', '*', '-fsS', '--max-time', '10',
                   'http://127.0.0.1:' + port['HostPort'] + '/ui/')
        assert '<html' in html
        code = run('curl', '--noproxy', '*', '-s', '-o', '/dev/null', '-w', '%{http_code}',
                   'http://127.0.0.1:' + port['HostPort'] + '/version')
        assert code == '401'
        print('PASS: actual host-loopback UI publication and protected API', flush=True)


def fingerprint(cid):
    return exec_(cid, 'python3', '-c',
                 'import hashlib,pathlib; print(hashlib.sha256(pathlib.Path("/data/controller.secret").read_bytes()).hexdigest())')


def fzf_pty(cid):
    master, slave = pty.openpty()
    import fcntl
    import struct
    import termios
    import tty
    tty.setraw(slave)
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 100, 0, 0))
    p = subprocess.Popen(['docker', 'exec', '-it', '-e', 'TERM=xterm', cid, 'bash', '-c',
        "printf 'alpha\\nbeta\\n' | fzf --no-clear --no-mouse --height=10 > /data/fzf-selection"],
        stdin=slave, stdout=slave, stderr=slave)
    os.close(slave)
    try:
        deadline = time.monotonic() + 15
        seen = b''
        while time.monotonic() < deadline and b'alpha' not in seen:
            if select.select([master], [], [], .2)[0]:
                chunk = os.read(master, 65536)
                seen += chunk
                if b'\x1b[6n' in chunk:
                    os.write(master, b'\x1b[1;1R')
        assert b'alpha' in seen, 'fzf did not render in PTY'
        os.write(master, b'beta')
        deadline = time.monotonic() + 15
        entered = False
        while p.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], .2)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if b'\x1b[6n' in chunk:
                    os.write(master, b'\x1b[1;1R')
                if not entered and b'beta' in chunk:
                    time.sleep(.2)
                    os.write(master, b'\r')
                    entered = True
        assert p.wait(timeout=2) == 0
        assert exec_(cid, 'cat', '/data/fzf-selection') == 'beta'
        print('PASS: real fzf PTY render, search and Enter selection', flush=True)
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
        os.close(master)


try:
    volume = docker('volume', 'create', '--label', 'test.owner=' + OWNER, OWNER + '-data')
    ledger['volumes'].append(volume)
    record()
    cid = start()
    listeners = exec_(cid, 'ss', '-lnt')
    assert '127.0.0.1:9090' in listeners and '0.0.0.0:9090' not in listeners
    probe(cid)
    secret_hash = fingerprint(cid)
    assert exec_(cid, 'bash', '-c', "printf 'alpha\\nbeta\\n' | fzf --filter=beta") == 'beta'
    fzf_pty(cid)
    docker('stop', cid)
    cid = start('0.0.0.0')
    assert fingerprint(cid) == secret_hash
    listeners = exec_(cid, 'ss', '-lnt')
    assert '0.0.0.0:9090' in listeners or '*:9090' in listeners
    probe(cid, external=True)
    docker('stop', cid)
    cid = start()
    assert fingerprint(cid) == secret_hash
    assert '127.0.0.1:9090' in exec_(cid, 'ss', '-lnt')
    probe(cid)
    print('PASS: recreation persists secret; removing opt-in restores loopback', flush=True)
    # Fail closed on empty saved secrets, without printing the original value.
    docker('stop', cid)
    helper = docker('create', '--label', 'test.owner=' + OWNER,
        '-v', volume + ':/data', '--entrypoint', 'sh', IMAGE,
        '-c', ': > /data/controller.secret')
    containers.append(helper)
    ledger['containers'].append(helper)
    record()
    docker('start', '-a', helper)
    failed = docker('create', '--label', 'test.owner=' + OWNER, '-v', volume + ':/data', IMAGE)
    containers.append(failed)
    ledger['containers'].append(failed)
    record()
    docker('start', failed)
    assert docker('wait', failed) == '1'
    assert 'must not be empty' in docker('logs', failed)
    print('PASS: empty secret fails closed', flush=True)
    helper = docker('create', '--label', 'test.owner=' + OWNER,
        '-v', volume + ':/data', '--entrypoint', 'sh', IMAGE, '-c',
        'rm /data/controller.secret; ln -s /data/mixin.yaml /data/controller.secret')
    containers.append(helper)
    ledger['containers'].append(helper)
    record()
    docker('start', '-a', helper)
    failed = docker('create', '--label', 'test.owner=' + OWNER, '-v', volume + ':/data', IMAGE)
    containers.append(failed)
    ledger['containers'].append(failed)
    record()
    docker('start', failed)
    assert docker('wait', failed) == '1'
    assert 'must not be a symlink' in docker('logs', failed)
    print('PASS: symlinked secret fails closed', flush=True)
    failed = docker('create', '--label', 'test.owner=' + OWNER,
                    '-e', 'CONTROLLER_BIND=invalid', '-v', volume + ':/data', IMAGE)
    containers.append(failed)
    ledger['containers'].append(failed)
    record()
    docker('start', failed)
    assert docker('wait', failed) == '1'
    assert 'CONTROLLER_BIND must be' in docker('logs', failed)
    print('PASS: invalid controller binding fails closed', flush=True)
    helper = docker('create', '--label', 'test.owner=' + OWNER,
        '-v', volume + ':/data', '--entrypoint', 'sh', IMAGE,
        '-c', 'rm /data/controller.secret')
    containers.append(helper)
    ledger['containers'].append(helper)
    record()
    docker('start', '-a', helper)
    cid = start()
    assert fingerprint(cid) == secret_hash
    probe(cid)
    print('PASS: upgrading a volume preserves its existing mixin secret', flush=True)
    print('ALL DASHBOARD CHECKS PASSED', flush=True)
finally:
    for cid in containers:
        actual = json.loads(docker('inspect', cid))[0]
        assert actual['Id'] == cid and actual['Config']['Labels']['test.owner'] == OWNER
        docker('rm', '-f', cid)
    if volume:
        actual = json.loads(docker('volume', 'inspect', volume))[0]
        assert actual['Labels']['test.owner'] == OWNER
        docker('volume', 'rm', volume)
    ledger['cleaned'] = True
    record()
