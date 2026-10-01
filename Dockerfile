# syntax=docker/dockerfile:1
FROM metacubex/mihomo:v1.19.32@sha256:bac1a74de365ea59270ba134016762c6be05654d55246518de256113bd1c225e AS core
FROM alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6
COPY --from=core /mihomo /mihomo
COPY --from=core /root/.config/mihomo/ /etc/clashctl/
ARG TARGETARCH
LABEL org.opencontainers.image.source="https://github.com/creekxi2026/clashctl-docker"       org.opencontainers.image.description="Headless clashctl + mihomo; persistent subscriptions and noninteractive management"       org.opencontainers.image.licenses="GPL-3.0,MIT"       io.clashctl.upstream.revision="b2d4cbd6e4bed4ee59e1a4495f931f6e5d5498bc"
USER root
RUN apk add --no-cache bash curl wget coreutils findutils grep sed gawk       iproute2 procps util-linux supervisor libstdc++ libgcc ca-certificates     && addgroup -g 1000 clashctl && adduser -D -u 1000 -G clashctl clashctl
RUN set -eux; \
    case "$TARGETARCH" in \
      amd64) subarch=linux64; subsha=b9d6f969300d3c8398f9d970db7436274df0ff3e7c91d935d26bbd79fafc8488; yqsha=fa52a4e758c63d38299163fbdd1edfb4c4963247918bf9c1c5d31d84789eded4 ;; \
      arm64) subarch=aarch64; subsha=76161fc1966b596d2e5daaf0eaeff13e9b981acde5b091e7363cb31f98f8598b; yqsha=578648e463a11c1b6db6010cbf41eafed6bee79466fcffa1bb446672cf7945ea ;; \
      *) exit 1 ;; \
    esac; \
    mkdir -p /opt/clashctl/bin /tmp/upstream /data /etc/clashctl; \
    curl -fSL --retry 3 --max-time 120 https://codeload.github.com/nelvko/clash-for-linux-install/tar.gz/b2d4cbd6e4bed4ee59e1a4495f931f6e5d5498bc -o /tmp/upstream.tar.gz; \
    printf '%s  %s\n' de6c03c4b900eb96f813ded173b3b4d6285ab4b6b530071f513420a5ffe12385 /tmp/upstream.tar.gz | sha256sum -c -; \
    tar -xzf /tmp/upstream.tar.gz -C /tmp/upstream --strip-components=1; \
    cp -R /tmp/upstream/scripts /opt/clashctl/; \
    cp /tmp/upstream/LICENSE /opt/clashctl/LICENSE; \
    cp /tmp/upstream/.env /opt/clashctl/.env; \
    printf '\nCLASHCTL_KERNEL=mihomo\nINIT_TYPE=container\n' >> /opt/clashctl/.env; \
    ln -s /mihomo /opt/clashctl/bin/mihomo; \
    ln -s /data /opt/clashctl/resources; \
    ln -s /data/subconverter /opt/clashctl/bin/subconverter; \
    curl -fSL --retry 3 --max-time 120 "https://github.com/mikefarah/yq/releases/download/v4.53.3/yq_linux_$TARGETARCH" -o /opt/clashctl/bin/yq; \
    printf '%s  %s\n' "$yqsha" /opt/clashctl/bin/yq | sha256sum -c -; \
    chmod 755 /opt/clashctl/bin/yq; \
    curl -fSL --retry 3 --max-time 120 "https://github.com/asdlokj1qpi233/subconverter/releases/download/v0.9.9/subconverter_$subarch.tar.gz" -o /tmp/subconverter.tar.gz; \
    printf '%s  %s\n' "$subsha" /tmp/subconverter.tar.gz | sha256sum -c -; \
    tar -xzf /tmp/subconverter.tar.gz -C /opt; \
    mv /opt/subconverter /opt/subconverter-template; \
     rm -rf /tmp/upstream /tmp/upstream.tar.gz /tmp/subconverter.tar.gz; \
    chown 1000:1000 /data
# Upstream disables TLS checks when fetching subscriptions. Restore verification.
RUN python3 - <<'PY'
from pathlib import Path
p = Path('/opt/clashctl/scripts/lib/convert.sh')
s = p.read_text()
for option in ('--insecure', '--no-check-certificate'):
    assert s.count(option) == 1, 'Upstream drift: review TLS patch'
    s = '\n'.join(line for line in s.split('\n') if option not in line)
# The upstream readiness loop returns its last false timeout check after a
# successful delayed startup; make readiness success explicit.
old = '    done\n}\n\n_stop_convert()'
assert s.count(old) == 1, 'Upstream drift: review converter return fix'
s = s.replace(old, '    done\n    return 0\n}\n\n_stop_convert()')
p.write_text(s)
p = Path('/opt/clashctl/scripts/cmd/sub.sh')
s = p.read_text()
old = '    [ "$use_after_add" = true ] && _sub_use_locked "$name"\n}'
assert s.count(old) == 1, 'Upstream drift: review subscription add return fix'
s = s.replace(old, '    if [ "$use_after_add" = true ]; then _sub_use_locked "$name"; else return 0; fi\n}')
p.write_text(s)
for script in Path('/opt/clashctl/scripts').rglob('*.sh'):
    script.write_text(script.read_text().replace('/usr/bin/rm', '/bin/rm'))
PY
COPY container/zz-container.sh /opt/clashctl/scripts/lib/zz-container.sh
COPY container/default.yaml container/mixin.yaml /etc/clashctl/
COPY container/supervisord.conf /etc/supervisord.conf
COPY container/entrypoint container/clashctl container/update-subscriptions /usr/local/bin/
RUN chmod 755 /usr/local/bin/entrypoint /usr/local/bin/clashctl /usr/local/bin/update-subscriptions     && /opt/clashctl/bin/yq --version && /mihomo -v
ENV CLASHCTL_HOME=/opt/clashctl SUB_UPDATE_INTERVAL=86400
WORKDIR /data
USER 1000:1000
VOLUME ["/data"]
EXPOSE 7890
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3     CMD clashctl status >/dev/null || exit 1
ENTRYPOINT ["/usr/local/bin/entrypoint"]
