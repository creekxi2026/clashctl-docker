"""Container acceptance tests: real processes, network requests and persistence."""
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

IMAGE = os.environ.get("IMAGE", "clashctl:test")
NAME = "clashctl-test-" + uuid.uuid4().hex[:10]
FIXTURE = NAME + "-fixture"
NETWORK = NAME + "-network"
VOLUME = NAME + "-data"
ROOT = Path(__file__).resolve().parent


def run(*args, ok=True):
    p = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=150)
    if ok and p.returncode:
        raise AssertionError(f"{args!r} failed ({p.returncode}): {p.stdout}")
    return p


def docker(*args, **kwargs):
    return run("docker", *args, **kwargs)


def cli(*args, **kwargs):
    return docker("exec", NAME, "clashctl", *args, **kwargs)


def wait_ready():
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        p = cli("status", ok=False)
        if p.returncode == 0 and "RUNNING" in p.stdout:
            return
        time.sleep(0.5)
    raise AssertionError("mihomo did not become RUNNING")


def value(expr, path="/data/profiles.yaml"):
    return docker("exec", NAME, "/opt/clashctl/bin/yq", expr, path).stdout.strip()


def proxy(mode="http"):
    port = json.loads(docker("inspect", NAME).stdout)[0]["NetworkSettings"]["Ports"]["7890/tcp"][0]["HostPort"]
    args = ["curl", "--silent", "--show-error", "--fail", "--max-time", "10", "--noproxy", ""]
    if mode == "http":
        args += ["--proxy", f"http://127.0.0.1:{port}"]
    else:
        args += ["--socks5-hostname", f"127.0.0.1:{port}"]
    p = run(*args, "http://target.invalid/origin")
    assert p.stdout == "proxy-hop-ok", p.stdout


def start():
    docker("run", "-d", "--name", NAME, "--network", NETWORK,
           "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
           "-e", "SUB_URL=http://fixture:18082/sub", "-e", "SUB_UPDATE_INTERVAL=0",
           "-v", VOLUME + ":/data", "-p", "127.0.0.1::7890", IMAGE)
    wait_ready()


def check(label):
    print("PASS:", label, flush=True)


try:
    docker("network", "create", NETWORK)
    docker("volume", "create", VOLUME)
    docker("run", "-d", "--name", FIXTURE, "--network", NETWORK, "--network-alias", "fixture",
           "--entrypoint", "python3", "-v", str(ROOT) + ":/tests:ro", IMAGE, "/tests/fixture.py")
    # Readiness is exercised from the actual fixture container, not guessed by sleep.
    for attempt in range(40):
        p = docker("exec", FIXTURE, "curl", "-fsS", "http://127.0.0.1:18082/sub", ok=False)
        if p.returncode == 0:
            break
        time.sleep(0.25)
    else:
        raise AssertionError("fixture did not start")
    start()
    assert docker("exec", NAME, "id", "-u").stdout.strip() == "1000"
    assert value(".use") == "main"
    assert value(".profiles | length") == "1"
    assert "No such file or directory" not in docker("exec", NAME, "cat", "/data/bootstrap.log").stdout
    check("non-root startup, initial URL import and supervisor readiness")
    proxy()
    proxy("socks")
    check("HTTP and SOCKS5 traffic traverse the selected real upstream proxy")
    assert "PROXY" in cli("node", "ls").stdout
    cli("node", "use", "PROXY", "hop-b")
    proxies = json.loads(docker("exec", NAME, "curl", "-fsS", "http://127.0.0.1:9090/proxies/PROXY").stdout)
    assert proxies["now"] == "hop-b"
    proxy()
    check("noninteractive node selection, API readback and post-switch traffic")
    docker("exec", FIXTURE, "curl", "-fsS", "http://127.0.0.1:18082/bump")
    cli("sub", "update", "main")
    assert value(".test-generation", "/data/runtime.yaml") == "2"
    proxy()
    check("subscription HTTP re-download and updated runtime activation")
    before = value(".profiles | length")
    assert cli("sub", "add", "--raw", "-n", "bad", "http://fixture:18082/bad", ok=False).returncode != 0
    assert value(".profiles | length") == before
    proxy()
    check("invalid subscription rejected without disrupting active proxy")
    cli("sub", "add", "--convert", "-n", "converted", "http://fixture:18082/convert")
    converted = value('.profiles[] | select(.name == "converted") | .path')
    assert int(value(".proxies | length", converted)) > 0
    check("bundled subconverter converts a Base64 Shadowsocks fixture")
    cli("off")
    assert cli("status", ok=False).returncode != 0
    assert docker("inspect", "-f", "{{.State.Running}}", NAME).stdout.strip() == "true"
    cli("on")
    wait_ready()
    proxy()
    check("explicit stop/start works without stopping the container")
    old_pid = docker("exec", NAME, "pgrep", "-x", "mihomo").stdout.strip()
    docker("exec", NAME, "kill", "-KILL", old_pid)
    time.sleep(2)
    wait_ready()
    new_pid = docker("exec", NAME, "pgrep", "-x", "mihomo").stdout.strip()
    assert old_pid != new_pid
    proxy()
    check("unexpected core crash is recovered by supervisor")
    cli("sub", "rename", "main", "retained")
    docker("stop", "--time", "15", NAME)
    state = json.loads(docker("inspect", NAME).stdout)[0]["State"]
    assert state["ExitCode"] == 0, state
    docker("rm", NAME)
    start()
    assert value(".use") == "retained"
    assert value(".profiles | length") == "2"
    proxy()
    check("graceful stop and persistent subscriptions across container recreation")
    # Only proxy port is published; API and converter remain container-loopback.
    api = docker("exec", NAME, "ss", "-lnt").stdout
    assert "127.0.0.1:9090" in api and "0.0.0.0:9090" not in api
    assert cli("tun", ok=False).returncode == 2
    check("internal-only controller and no TUN privileges")
    print("ALL INTEGRATION CHECKS PASSED", flush=True)
except Exception:
    for container in (NAME, FIXTURE):
        print(docker("logs", container, ok=False).stdout)
    for file in ("bootstrap.log", "config-check.log", "mihomo.log", "subconverter/latest.log"):
        print(docker("exec", NAME, "tail", "-n", "60", "/data/" + file, ok=False).stdout)
    raise
finally:
    docker("rm", "-fv", NAME, FIXTURE, ok=False)
    docker("volume", "rm", VOLUME, ok=False)
    docker("network", "rm", NETWORK, ok=False)
