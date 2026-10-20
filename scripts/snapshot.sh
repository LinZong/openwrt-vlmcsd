#!/bin/sh
# Read-only target inventory. No cache refresh and no package scripts.
set -e
. /usr/share/libubox/jshn.sh
json_init
json_add_object board
for key in DISTRIB_RELEASE DISTRIB_REVISION DISTRIB_TARGET DISTRIB_ARCH; do
 value=$(sed -n "s/^$key='\(.*\)'/\1/p" /etc/openwrt_release)
 json_add_string "$key" "$value"
done
json_add_string machine_id "$(cat /etc/machine-id 2>/dev/null || cat /sys/class/dmi/id/product_uuid 2>/dev/null || cat /sys/class/net/br-lan/address)"
json_close_object
json_add_object settings
for option in interface port dns; do
 json_add_string "$option" "$(uci -q get "vlmcsd.main.$option" || true)"
done
json_close_object
json_add_string ownership "$(cat /root/vlmcsd-deploy/ownership.json 2>/dev/null || echo '{}')"
json_add_string group "$(grep '^vlmcsd:' /etc/group || true)"
json_add_string account "$(grep '^vlmcsd:' /etc/passwd || true)"
json_add_string boot_id "$(cat /proc/sys/kernel/random/boot_id)"
json_add_string packages "$(apk query --from installed --format json --fields name,version,depends '*' 2>/dev/null)"
json_add_string world "$(cat /etc/apk/world)"
json_add_string pending "$(uci changes)"
json_add_string listening "$(ss -lntp 2>/dev/null || netstat -lnt 2>/dev/null || true)"
json_add_string lan "$(ubus call network.interface.lan status)"
json_add_string observed_status "$(ubus call luci.vlmcsd status 2>/dev/null || echo '{}')"
json_add_string service "$(ubus call service list '{"name":"vlmcsd"}')"
json_add_string enabled "$(test -e /etc/rc.d/S95vlmcsd && echo 1 || echo 0)"
json_add_object hashes
for f in /etc/config/vlmcsd /etc/config/luci /etc/config/dhcp /etc/config/network /etc/config/firewall /etc/config/system /etc/passwd /etc/group /etc/shadow /etc/apk/world; do
 if [ -f "$f" ]; then sum=$(sha256sum "$f"); sum=${sum%% *}; else sum=absent; fi
 json_add_string "$f" "$sum"
done
json_close_object
json_add_string available_kib "$(df -Pk /root | tail -1 | awk '{print $4}')"
json_dump
