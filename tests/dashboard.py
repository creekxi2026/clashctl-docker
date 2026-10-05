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
ledger = {'owner': OWNER, 'image': IMAGE, 'containers': [], 'volumes': [], 'compose_projects': []}


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


def start(bind=None, secret=None):
    assert volume is not None
    args = ['docker', 'create', '--name', OWNER + '-' + str(len(containers)),
            '--label', 'test.owner=' + OWNER, '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '-e', 'SUB_UPDATE_INTERVAL=0',
            '-v', volume + ':/data']
    if secret is not None:
        args += ['-e', 'UI_SECRET=' + secret]
    if bind is not None:
        args += ['-e', 'CONTROLLER_BIND=' + bind, '-p', '127.0.0.1::9090']
    cid = run(*args, IMAGE)
    containers.append(cid)
    ledger['containers'].append(cid)
    record()
    docker('start', cid)
    return wait_ready(cid)


def wait_ready(cid):
    for _ in range(100):
        status = run('docker', 'exec', cid, 'clashctl', 'status', check=False)
        if 'RUNNING' in status:
            return cid
        state = json.loads(docker('inspect', cid))[0]['State']
        if not state['Running']:
            raise AssertionError('Startup failed: ' + docker('logs', cid))
        time.sleep(.2)
    raise AssertionError('Readiness timeout')


def start_compose_fixture(secret):
    import tempfile
    assert volume is not None
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
        env_file = Path(tmp) / '.env'
        env_file.write_text("UI_SECRET='" + secret + "'\nSUB_UPDATE_INTERVAL=0\n")
        override = Path(tmp) / 'fixture.yaml'
        override.write_text('services:\n  clashctl:\n    image: ' + json.dumps(IMAGE) +
            '\n    network_mode: none\n    ports: !reset []\n    labels:\n      test.owner: ' + OWNER +
            '\nvolumes:\n  data:\n    external: true\n    name: ' + volume + '\n')
        # No start yet: inspect the real .env -> Compose -> container value first.
        project = OWNER + '-dotenv'
        ledger['compose_projects'].append(project)
        record()
        args = ['docker', 'compose', '--project-name', project, '--env-file', str(env_file),
                '-f', str(root / 'compose.yaml'), '-f', str(override)]
        env = {k: v for k, v in os.environ.items() if k not in
               ('UI_SECRET', 'SUB_URL', 'SUB_UPDATE_INTERVAL', 'COMPOSE_FILE', 'BIND_IP', 'PROXY_PORT')}
        p = subprocess.run([*args, 'create', '--pull', 'never'], env=env, capture_output=True, text=True, timeout=90)
        assert p.returncode == 0, 'Fixture Compose create failed: ' + p.stderr
        cid = subprocess.check_output([*args, 'ps', '-aq'], env=env, text=True).strip()
        info = json.loads(docker('inspect', cid))[0]
        cid = info['Id']
        assert info['Config']['Labels']['test.owner'] == OWNER
        assert info['Config']['Labels']['com.docker.compose.project'] == project
        containers.append(cid)
        ledger['containers'].append(cid)
        record()
        values = dict(item.split('=', 1) for item in info['Config']['Env'])
        assert values['UI_SECRET'] == secret, 'Compose changed literal fixture bytes'
        docker('start', cid)
        return wait_ready(cid)


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


def explicit_probe(cid, expected, rejected):
    # Public isolated fixtures only; never use handoff credentials here.
    script = r'''
import json, os, pathlib, re, urllib.request, urllib.error
expected, rejected = os.environ['TEST_EXPECTED'], os.environ['TEST_REJECTED']
assert pathlib.Path('/data/controller.secret').read_text() == expected
assert pathlib.Path('/data/controller.secret').stat().st_mode & 0o777 == 0o600
base = 'http://127.0.0.1:9090'
def req(path, key=None):
    return urllib.request.urlopen(urllib.request.Request(base+path,
        headers={'Authorization': 'Bearer '+key} if key else {}))
assert json.load(req('/version', expected))['meta']
for key in (None, rejected):
    try:
        req('/version', key)
        raise AssertionError('Wrong key accepted')
    except urllib.error.HTTPError as e:
        assert e.code == 401
html = req('/ui/').read().decode()
for data in [html] + [req(asset if asset.startswith('/') else '/ui/'+asset.removeprefix('./')).read().decode()
        for asset in re.findall(r'(?:src|href)="([^"]+\.(?:js|css))"', html)]:
    assert expected not in data and rejected not in data
for filename in ('mixin.yaml', 'runtime.yaml'):
    import subprocess
    selected = subprocess.check_output(['/opt/clashctl/bin/yq', '.secret', '/data/'+filename], text=True).strip()
    assert selected == expected
print('PASS: explicit key authenticates, old key rejected, private storage/config and no asset leakage')
'''
    print(docker('exec', '-e', 'TEST_EXPECTED=' + expected, '-e', 'TEST_REJECTED=' + rejected,
                 cid, 'python3', '-c', script), flush=True)
    logs = docker('logs', cid)
    assert expected not in logs and rejected not in logs


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
    failed = docker('create', '--label', 'test.owner=' + OWNER,
                    '-e', 'UI_SECRET=fixture-only-7d89e4c06a315bf291e836b7c94da250',
                    '-v', volume + ':/data', IMAGE)
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
    failed = docker('create', '--label', 'test.owner=' + OWNER,
                    '-e', 'UI_SECRET=fixture-only-7d89e4c06a315bf291e836b7c94da250',
                    '-v', volume + ':/data', IMAGE)
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
    # Override an upgrade's saved key, then exercise fresh storage and rotation.
    docker('stop', cid)
    first = 'fixture-only-7d89e4c06a315bf291e836b7c94da250$literal#suffix'
    second = 'fixture-only-52cdad8b063fe041c792e548903f6ba1'
    cid = start('0.0.0.0', first)
    explicit_probe(cid, first, second)
    docker('stop', cid)
    volume = docker('volume', 'create', '--label', 'test.owner=' + OWNER, OWNER + '-explicit')
    ledger['volumes'].append(volume)
    record()
    cid = start_compose_fixture(first)
    explicit_probe(cid, first, second)
    docker('stop', cid)
    print('PASS: real .env to Compose to image startup preserves literal dollar/hash key', flush=True)
    for value, expected, rejected in ((first, first, second), (second, second, first),
                                     (second, second, first), (None, second, first), ('', second, first)):
        cid = start('0.0.0.0', value)
        explicit_probe(cid, expected, rejected)
        probe(cid, external=True)
        docker('stop', cid)
    print('PASS: fresh explicit key, intentional rotation, same-key recreation and unset/empty retention', flush=True)
    print('ALL DASHBOARD CHECKS PASSED', flush=True)
finally:
    errors = []
    ledger['cleaned'] = False
    candidates = dict.fromkeys(containers)
    for project in ledger['compose_projects']:
        try:
            discovered = docker('ps', '-aq', '--no-trunc',
                                '--filter', 'label=com.docker.compose.project=' + project,
                                '--filter', 'label=test.owner=' + OWNER)
            for cid in discovered.split():
                candidates[cid] = project
        except Exception as error:
            errors.append(f'Project {project}: {error}')
    for cid, project in candidates.items():
        try:
            actual = json.loads(docker('inspect', cid))[0]
            labels = actual['Config']['Labels']
            assert actual['Id'] == cid and labels['test.owner'] == OWNER, 'Container identity/owner mismatch'
            actual_project = labels.get('com.docker.compose.project')
            if project is not None:
                assert actual_project == project, 'Container project mismatch'
            elif actual_project is not None:
                assert actual_project in ledger['compose_projects'], 'Container project mismatch'
            docker('rm', '-f', cid)
        except Exception as error:
            errors.append(f'Container {cid}: {error}')
    for volume in ledger['volumes']:
        try:
            actual = json.loads(docker('volume', 'inspect', volume))[0]
            assert actual['Name'] == volume and actual['Labels']['test.owner'] == OWNER, 'Volume identity/owner mismatch'
            docker('volume', 'rm', volume)
        except Exception as error:
            errors.append(f'Volume {volume}: {error}')
    ledger['cleaned'] = not errors
    try:
        record()
    except Exception as error:
        ledger['cleaned'] = False
        errors.append(f'Ledger: {error}')
    if errors:
        raise AssertionError('Cleanup failed:\n' + '\n'.join(errors))
