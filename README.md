# clashctl-docker

Headless **clashctl + mihomo + local subconverter**, published as
`ghcr.io/creekxi2026/clashctl-docker:latest` for **linux/amd64** and **linux/arm64**.
This is a third-party container integration, not an official clashctl image.

## Start

Download `compose.yaml` and `env.example`, copy `env.example` to `.env`, and set
`SUB_URL` to your subscription URL. Run:

```bash
docker compose up -d
docker compose exec -T clashctl clashctl status
docker compose exec -T clashctl clashctl node ls
```

The HTTP/SOCKS mixed proxy is `127.0.0.1:7890` on the Docker host by default.
Edit `PROXY_PORT` in `.env` to change its host port. To access it from another
machine, deliberately set `BIND_IP` to a reachable **private** host address and
restrict access with the host firewall. There is no proxy authentication by
default. Do not publish this proxy to the Internet.
Containers sharing its Compose network can use `http://clashctl:7890`.
`localhost` inside another container is not this service.

## Web dashboard (optional)

The image includes **MetaCubeXD** and **fzf**. The dashboard controls mihomo:
select nodes, run delay tests, change the active mode, and inspect connections,
rules and logs. It does **not** manage clashctl subscription metadata; keep using
`clashctl sub` for adding, selecting and refreshing subscriptions. API changes
are runtime-only unless persisted by mihomo; subsequent config merges/restarts
can overwrite them. The dashboard is unavailable while `clashctl off` is active.
For terminal selection, use `docker compose exec clashctl clashctl node use`
(with a TTY, not `-T`).

Default Compose still publishes only the proxy; the controller listens on
container loopback. To enable browser access, also download `compose.ui.yaml`:

```bash
docker compose -f compose.yaml -f compose.ui.yaml up -d
```

This publishes the authenticated controller and `/ui/` on host loopback port
9090. On a NAS, set `UI_BIND_IP` in `.env` to the NAS's **private LAN IP** and
optionally change `UI_PORT`, then recreate with both Compose files (or select
both files in your NAS Compose UI). Leave `BIND_IP` unchanged if you do not want
to expose the proxy too. This is an explicit opt-in to LAN controller access;
restrict the port with your NAS firewall. Never forward it to the Internet.

Open `http://NAS_PRIVATE_IP:9090/ui/` (use your chosen host port). In the dashboard
connection form, enter API URL `http://NAS_PRIVATE_IP:9090` and the secret from
`/data/controller.secret`. **Browser localhost is your computer, not the NAS.**
Use matching HTTPS routes for both UI and API behind an HTTPS reverse proxy;
HTTPS pages cannot generally call an HTTP API. LAN HTTP does not encrypt the
secret or API traffic; use a trusted network or HTTPS/private tunnel.

A secret is generated at first startup and persisted with mode 0600 in `/data`;
an existing nonempty mixin secret is retained on upgrade. Retrieve it privately
in your NAS container terminal or volume file manager, and enter it locally;
do not paste it into chat, issues, screenshots or logs. Startup never prints it.
To rotate, stop this service, replace `/data/controller.secret` with a strong
nonempty secret owned by UID 1000 (mode 0600), then recreate. Empty or symlinked
secret files fail startup rather than enabling unauthenticated access. Removing
the UI override and recreating restores container-loopback access without
changing subscriptions or the saved secret. Always use the same Compose file
selection for later pull/up commands.

## Subscriptions

The initial URL is imported as `main` only when there is no active subscription.
Changing `SUB_URL` later does not replace an existing active subscription.
Updates run every 86400 seconds; set `SUB_UPDATE_INTERVAL=0` to disable them.

```bash
docker compose exec -T clashctl clashctl sub add -n another --use 'SUBSCRIPTION_URL'
docker compose exec -T clashctl clashctl sub update --all
docker compose exec -T clashctl clashctl sub use main
docker compose exec -T clashctl clashctl node ls 'GROUP'
docker compose exec -T clashctl clashctl node use 'GROUP' 'NODE'
docker compose exec -T clashctl clashctl node delay 'NODE'
docker compose exec -T clashctl clashctl off
docker compose exec -T clashctl clashctl on
docker compose exec -T clashctl clashctl log -n 100
```

Pass all names for noninteractive operation. No `source ~/.bashrc` is needed:
this image provides an executable `clashctl` wrapper. `on`/`off` only control
the proxy process; they do not change host or caller environment variables.
An explicit `off` keeps the container alive (health becomes unhealthy); `on`
starts it again. Docker does not automatically restart unhealthy containers.
Container restart starts the proxy again.

For secret-file input, omit `SUB_URL`, bind a readable file into the container
and set `SUB_URL_FILE` to that container path. The process runs as UID/GID
1000:1000. Never bake subscription URLs into an image, commit them to Git or
paste real links into issue reports. URLs can appear in `.env`, container
metadata when using `SUB_URL`, subscription metadata, and diagnostic logs.

## Storage and behavior

- `/data` is a Docker-managed named volume, **not** a `./data` directory.
- `/data/profiles.yaml` contains subscription URLs; `/data/profiles/` holds
  fetched configs. `/data/mixin.yaml` holds local overrides and
  `/data/runtime.yaml` is the merged runtime config.
- A bind mount is supported if pre-owned/writable by UID 1000. The image does
  not recursively change ownership of your host directories.
- With no subscription, the service starts with `MATCH,REJECT`: it does not
  silently provide a DIRECT bypass. Importing a subscription enables its rules.
- Conversion runs locally, bound to loopback, not through a public converter.
  Plain Clash/Mihomo YAML uses the native path; other supported formats use
  bundled subconverter. Not every provider/protocol supports conversion.
- Subscription download certificate validation is enabled (unlike the pinned
  upstream default). A failed download/validation does not activate that config.
- Supervisor handles process reaping, graceful shutdown and unexpected mihomo
  restarts. Proxy logs are rotated in `/data/mihomo.log`; `docker compose logs`
  shows supervisor/bootstrap state. Sensitive bootstrap errors stay in
  `/data/bootstrap.log`.
- Controller defaults to internal `127.0.0.1:9090`, not published, with a persisted
  secret. Bundled UI assets are served by mihomo; no extra web server, TUN,
  privileged mode, host networking, or Docker socket mount is required.
- Keep the container listener settings in `mixin.yaml` unchanged. Configure
  host bindings through Compose. `upgrade`, `tun`, `ui`, `secret` commands are
  intentionally disabled; update by pulling and recreating the image.

## Pinned components and build

- mihomo v1.19.32, official multi-platform image pinned by digest in Dockerfile
- clashctl commit `b2d4cbd6e4bed4ee59e1a4495f931f6e5d5498bc`
- yq v4.53.3
- asdlokj1qpi233/subconverter v0.9.9
- MetaCubeXD v1.273.1 (release archive pinned by SHA-256)
- fzf (Alpine package)

Downloaded source/binaries are SHA-256 checked. The image preserves upstream
clashctl implementation; the small service adapter replaces host init-system
calls and wraps conversion with a working directory and lock.

GitHub Actions builds on native amd64 and arm64 runners. Integration tests
exercise actual HTTP and SOCKS proxy traffic through a controlled local upstream,
subscription download/update/selection, conversion, persistence, service stop/
start, and crash recovery. Test fixtures contain no user credentials. This is
not verification of any user's real provider account or destination reachability.
Only after both architectures pass does the workflow publish the `latest`
multi-platform index. No automatic scheduled rebuilds or paid runners.

```bash
docker buildx build --load -t clashctl:test .
IMAGE=clashctl:test python3 tests/integration.py
IMAGE=clashctl:test python3 tests/dashboard.py
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

## Upstream update notifications

[Check upstream updates](.github/workflows/upstream.yml) runs on the first day
of each month at 09:23 Asia/Shanghai (GitHub may delay scheduled runs). It checks the default-branch
commit of clash-for-linux-install and the latest stable mihomo release against
this repository's Dockerfile pins. Alpha releases are excluded.

A new upstream revision creates a GitHub Issue with the pinned version, new
version, and upstream comparison links. The same revision is reported once,
including after its issue is closed. No change means no issue or notification.
The workflow does not edit pins, publish images, or update running containers.

To receive notifications, select **Watch → Custom → Issues** on this repository,
and enable GitHub web/email notifications in your account settings. This uses
GitHub's built-in notifications, not Feishu or a local scheduled task. For
workflow failures, configure **Settings → Notifications → Actions** separately.

The workflow uses the built-in `GITHUB_TOKEN`; no extra secret is required.
Manual runs support an optional, clearly marked one-time notification test.
GitHub can disable scheduled workflows in public repositories after 60 days
without repository activity; re-enable the workflow from the Actions page.

```bash
python3 -m unittest discover -s tests -p 'test_upstream_updates.py' -v
python3 scripts/check_upstream.py --repo creekxi2026/clashctl-docker --dry-run
```

## Upstream and licenses

- https://github.com/nelvko/clash-for-linux-install (MIT; included in image)
- https://github.com/MetaCubeX/mihomo (GPL-3.0)
- https://github.com/MetaCubeX/metacubexd (MIT)
- https://github.com/junegunn/fzf (MIT)
- https://github.com/mikefarah/yq (MIT)
- https://github.com/asdlokj1qpi233/subconverter (GPL-3.0)

The Dockerfile and pinned source references provide the reproducible build
recipe. See upstream repositories for their full corresponding sources/licenses.
