#!/usr/bin/env bash
# Loaded after upstream service.sh; preserve upstream sub/node implementations.
service_start() { supervisorctl -c /etc/supervisord.conf start mihomo; }
service_stop() {
    service_is_active || return 0
    supervisorctl -c /etc/supervisord.conf stop mihomo
}
service_restart() { supervisorctl -c /etc/supervisord.conf restart mihomo; }
service_status() { supervisorctl -c /etc/supervisord.conf status mihomo; }
service_is_active() { service_status 2>/dev/null | grep -q ' RUNNING '; }
service_sudo_start() { service_start; }
service_sudo_stop() { service_stop; }
service_log() { if [ "$#" -eq 0 ]; then tail -n 100 /data/mihomo.log; else tail "$@" /data/mihomo.log; fi; }
service_read_log() { cat /data/mihomo.log; }
service_follow_log() { tail -n 0 -f /data/mihomo.log; }
# Keep ports stable rather than silently selecting ports Docker does not publish.
_detect_proxy_port() { return 0; }
_detect_ext_addr() { EXT_IP=127.0.0.1; EXT_PORT=9090; }
# Serialize converter use and launch from its assets directory.
_container_convert() {
    local dest=$1 url=$2
    BIN_SUBCONVERTER_PORT=25500
    (cd /data/subconverter && _download_convert_config_upstream "$dest" "$url")
}
