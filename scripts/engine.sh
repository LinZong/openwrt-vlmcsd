#!/bin/sh
# Runs only after explicit apply. Persistent journal; APK is never edited by hand.
set -e
umask 077
HERE=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
ROOT=/root/vlmcsd-deploy
. /usr/share/libubox/jshn.sh
clock() { cut -d. -f1 /proc/uptime; }
state() { local t; t=$(mktemp "$HERE/state.XXXXXX"); printf '%s\n' "$1" > "$t"; mv "$t" "$HERE/state"; sync; }
event() {
 local tmp
 tmp=$(mktemp "$HERE/event.XXXXXX")
 json_init; json_add_string transaction "${HERE##*/}"; json_add_string phase "$1"; json_add_string message "${2:-}"; json_add_int uptime "$(clock)"; json_add_string time "$(date -u +%FT%TZ)"; json_dump > "$tmp"
 cat "$tmp" >> "$HERE/events.jsonl"; rm -f "$tmp"
 logger -t vlmcsd-deploy "$1 ${2:-}"
}
setting() { jsonfilter -i "$HERE/request.json" -e "@.$1"; }
# A deployment-wide flock and APK's own lock serialize mutations.
lock() { mkdir -p "$ROOT"; exec 9>"$ROOT/lock"; flock -n 9; }
record_hashes() {
 apk query --from installed --format json --fields name,version,depends '*' > "$HERE/after-packages"
 : > "$HERE/after-hashes"
 for f in /etc/config/vlmcsd /etc/config/luci /etc/config/dhcp /etc/apk/world; do
  if [ -f "$f" ]; then sha256sum "$f" >> "$HERE/after-hashes"; else printf 'absent  %s\n' "$f" >> "$HERE/after-hashes"; fi
 done
}
backup() {
 mkdir -p "$HERE/before" "$HERE/rollback-packages"
 for f in etc/config/vlmcsd etc/config/luci etc/config/dhcp etc/apk/world etc/passwd etc/group root/vlmcsd-deploy/ownership.json; do
  if [ -e "/$f" ]; then mkdir -p "$HERE/before/$(dirname "$f")"; cp -p "/$f" "$HERE/before/$f"; fi
 done
 grep '^vlmcsd:' /etc/shadow > "$HERE/before/shadow-entry" || true
 apk query --from installed --format json --fields name,version '*' > "$HERE/before/packages.json"
 /bin/sh "$HERE/snapshot.sh" > "$HERE/before/snapshot.json"
 [ ! -e /etc/rc.d/S95vlmcsd ] || touch "$HERE/before/enabled"
 if ubus call service list '{"name":"vlmcsd"}' | grep -q '"running": true'; then touch "$HERE/before/running"; fi
}
guard_install() {
 # Standalone boot recovery is deliberately outside the application packages.
 [ ! -e /etc/init.d/vlmcsd-recover ] || return 1
 cat > /etc/init.d/vlmcsd-recover <<INIT
#!/bin/sh /etc/rc.common
START=94
USE_PROCD=1
start_service() {
 procd_open_instance
 procd_set_param command /bin/sh '$HERE/engine.sh' watch
 procd_set_param respawn 3600 2 3
 procd_close_instance
}
INIT
 chmod 755 /etc/init.d/vlmcsd-recover
 /etc/init.d/vlmcsd-recover enable
 /etc/init.d/vlmcsd-recover start
}
guard_remove() {
 # Do not stop our own watcher while it is finishing a rollback.
 /etc/init.d/vlmcsd-recover disable 2>/dev/null || true
 rm -f /etc/init.d/vlmcsd-recover
 ubus call service delete '{"name":"vlmcsd-recover"}' >/dev/null 2>&1 || true
}
apk_local() {
 apk --no-network --repositories-file "$HERE/empty-repositories" --keys-dir "$HERE/keys" --wait 10 "$@"
}
check_drift() {
 if [ -s "$HERE/after-packages" ]; then
  apk query --from installed --format json --fields name,version,depends '*' > "$HERE/rollback-current-packages"
  cmp "$HERE/after-packages" "$HERE/rollback-current-packages" || { event conflict 'Installed package state changed concurrently'; return 1; }
 fi
 [ ! -s "$HERE/after-hashes" ] || {
  while read -r expected f; do
   if [ -f "$f" ]; then current=$(sha256sum "$f" | cut -d' ' -f1); else current=absent; fi
   [ "$expected" = "$current" ] || { event conflict "$f changed concurrently"; return 1; }
  done < "$HERE/after-hashes"
 }
}
restore_file() {
 local f="$1"
 if [ -e "$HERE/before/$f" ]; then cp -p "$HERE/before/$f" "/$f"; else rm -f "/$f"; fi
}

rollback_body() {
 event rollback 'Restoring previous packages and configuration'
 check_drift || return 1
 [ ! -x /etc/init.d/vlmcsd ] || /etc/init.d/vlmcsd stop || true
 # Old packages were validated and staged before any change; reinstall through APK.
 if [ -s "$HERE/old-installed" ]; then
  set -- "$HERE"/rollback-packages/*.apk
  [ -f "$1" ] || return 1
  apk_local add "$@" || return 1
 fi
 if [ -s "$HERE/new-names" ]; then
  # Simulation was approved in prepare; no unrelated autoremoval is permitted.
  apk_local del --simulate $(cat "$HERE/new-names") > "$HERE/rollback-simulation.log" 2>&1 || return 1
  /bin/sh "$HERE/check-removals.sh" "$HERE/rollback-simulation.log" "$HERE/new-names" || return 1
  apk_local del $(cat "$HERE/new-names") || return 1
 fi
 [ ! -x /etc/init.d/vlmcsd ] || /etc/init.d/vlmcsd stop || return 1
 restore_file etc/config/vlmcsd || return 1
 restore_file etc/config/luci || return 1
 restore_file etc/config/dhcp || return 1
 restore_file root/vlmcsd-deploy/ownership.json || return 1
 # World is restored only when its membership equals the prepared transaction result.
 cp -p "$HERE/before/etc/apk/world" /etc/apk/world
 # Remove only account entries introduced by this deployment, never overwrite passwd/group.
 for pair in passwd group; do
  if ! grep -q '^vlmcsd:' "$HERE/before/etc/$pair"; then
   sed -i '/^vlmcsd:/d' "/etc/$pair"
  elif ! grep -q '^vlmcsd:' "/etc/$pair"; then
   grep '^vlmcsd:' "$HERE/before/etc/$pair" >> "/etc/$pair"
  fi
 done
 if [ -s "$HERE/before/shadow-entry" ]; then
  grep -q '^vlmcsd:' /etc/shadow || cat "$HERE/before/shadow-entry" >> /etc/shadow
 else
  sed -i '/^vlmcsd:/d' /etc/shadow
 fi
 if [ -x /etc/init.d/vlmcsd ]; then
  if [ -f "$HERE/before/enabled" ]; then /etc/init.d/vlmcsd enable; else /etc/init.d/vlmcsd disable; fi
  [ ! -f "$HERE/before/running" ] || /etc/init.d/vlmcsd start || return 1
 fi
 /etc/init.d/rpcd reload || return 1
 if [ "$(setting dns)" = 1 ]; then /etc/init.d/dnsmasq reload || return 1; fi
 /bin/sh "$HERE/snapshot.sh" > "$HERE/rollback-snapshot.json"
}
rollback() {
 [ "$(cat "$HERE/state")" != rolled_back ] || return 0
 state rolling_back
 # Independent budget; timeout executable is staged even if the package is removed.
 if "$HERE/timeout" -k 5 120 /bin/sh "$HERE/engine.sh" rollback-body 9>&- >> "$HERE/worker.log" 2>&1; then
  state rolled_back; event rolled_back 'Previous state restored'; guard_remove
 else
  state recovery_failed; event recovery_failed 'Manual recovery required; backups retained'; return 1
 fi
}
health() {
 local p
 /usr/libexec/vlmcsd-helper validate
 ubus call service list '{"name":"vlmcsd"}' | grep -q '"running": true'
 for p in 4 5 6; do
  "$HERE/timeout" -k 1 10 /usr/libexec/vlmcsd-helper selftest "$p" > "$HERE/protocol-$p.log" 2>&1
  grep -q 'Response from KMS server' "$HERE/protocol-$p.log"
 done
 ubus call luci.vlmcsd status > "$HERE/status.json"
 jsonfilter -i "$HERE/status.json" -e '@.running' | grep -qx true
 listeners=$(jsonfilter -i "$HERE/status.json" -e '@.listeners[*]')
 [ "$(printf '%s\n' "$listeners" | wc -l)" -eq 2 ]
 printf '%s\n' "$listeners" | grep -q '^127.0.0.1:'
 ! printf '%s\n' "$listeners" | grep -Eq '^0\.0\.0\.0:|^\['

 for name in rpcd uhttpd dnsmasq; do ubus call service list "{\"name\":\"$name\"}" | grep -q '"running": true'; done
}
apply_body() {
 event installing
 if [ "$(setting operation)" = deploy ]; then
  apk_local add "$HERE"/packages/*.apk
  record_hashes
  uci set vlmcsd.main.enabled=1
  uci set "vlmcsd.main.interface=$(setting interface)"
  uci set "vlmcsd.main.port=$(setting service_port)"
  uci set "vlmcsd.main.dns=$(setting dns)"
  uci commit vlmcsd
  record_hashes
  /etc/init.d/vlmcsd enable
  /etc/init.d/vlmcsd restart
  record_hashes
  /etc/init.d/rpcd reload
  # Wait only for the RPC plugin registration, bounded by the health deadline.
  "$HERE/timeout" -k 1 10 /bin/sh -c 'until ubus -t 1 list luci.vlmcsd | grep -qx luci.vlmcsd; do sleep 1; done'
  health
 else
  [ ! -x /etc/init.d/vlmcsd ] || /etc/init.d/vlmcsd stop
  apk_local del $(cat "$HERE/remove-names")
  if [ "$(setting purge)" = true ]; then
   rm -f /etc/config/vlmcsd /etc/config/vlmcsd.apk-save /etc/config/vlmcsd.apk-new
  elif [ -f "$HERE/before/etc/config/vlmcsd" ]; then
   cp -p "$HERE/before/etc/config/vlmcsd" /etc/config/vlmcsd
  fi
  record_hashes
  /etc/init.d/rpcd reload
  if [ "$(jsonfilter -i "$ROOT/ownership.json" -e '@.account_created' 2>/dev/null)" = true ]; then
   sed -i '/^vlmcsd:/d' /etc/passwd
   sed -i '/^vlmcsd:/d' /etc/shadow
  fi
  if [ "$(jsonfilter -i "$ROOT/ownership.json" -e '@.group_created' 2>/dev/null)" = true ]; then
   sed -i '/^vlmcsd:/d' /etc/group
  fi
  rm -f "$ROOT/ownership.json"
  ! apk query --from installed --format json vlmcsd 2>/dev/null | grep -q '"name"'
 fi
 /bin/sh "$HERE/snapshot.sh" > "$HERE/after-snapshot.json"
 event healthy
}
preflight() {
 sha256sum -c "$HERE/expected-hashes"
 while IFS= read -r f; do [ -z "$f" ] || [ ! -e "$f" ]; done < "$HERE/expected-absent"
 current=$(apk query --from installed --format json --fields name,version,depends '*' 2>/dev/null)
 printf '%s' "$current" > "$HERE/current-packages"
 cmp "$HERE/expected-packages" "$HERE/current-packages"
 [ -z "$(uci changes)" ]
 [ ! -e /etc/init.d/vlmcsd-recover ]
 if [ "$(setting operation)" = deploy ]; then
  apk_local verify "$HERE"/packages/*.apk
  apk_local add --simulate "$HERE"/packages/*.apk > "$HERE/install-simulation.log" 2>&1
  awk '/ (Installing|Upgrading|Downgrading|Reinstalling) / {print $3}' "$HERE/install-simulation.log" > "$HERE/install-simulation.names"
  while IFS= read -r name; do grep -Fxq "$name" "$HERE/allowed-names"; done < "$HERE/install-simulation.names"
  ! grep -Eq ' (Removing|Purging) ' "$HERE/install-simulation.log"
 else
  apk_local del --simulate $(cat "$HERE/remove-names") > "$HERE/remove-simulation.log" 2>&1
  /bin/sh "$HERE/check-removals.sh" "$HERE/remove-simulation.log" "$HERE/remove-names"
 fi
 if [ -s "$HERE/old-installed" ]; then apk_local verify "$HERE"/rollback-packages/*.apk; fi
}
prepare_body() {
 preflight
 backup
 touch "$HERE/backup-complete"
}
apply() {
 lock
 [ "$(cat "$HERE/state")" = staged ] || { event rejected 'Transaction already executed'; return 1; }
 echo $$ > "$HERE/executor-pid"
 state preparing; event preparing
 start=$(clock); echo "$start" > "$HERE/start-uptime"; cat /proc/sys/kernel/random/boot_id > "$HERE/boot-id"
 # The preparation budget is part of the same 120-second deadline.
 if ! "$HERE/timeout" -k 1 20 /bin/sh "$HERE/engine.sh" prepare-body 9>&- >> "$HERE/worker.log" 2>&1; then
  state rejected; event rejected 'Preflight or backup failed; application unchanged'; return 1
 fi
 guard_install
 state applying
 setsid "$HERE/timeout" -k 5 "$((120 - $(clock) + start))" /bin/sh "$HERE/engine.sh" apply-body 9>&- >> "$HERE/worker.log" 2>&1 &
 worker=$!
 echo "$worker" > "$HERE/worker-pid"
 if wait "$worker"; then
  if [ "$(setting operation)" = deploy ]; then
   jsonfilter -i "$HERE/request.json" -e '@.ownership' > "$ROOT/ownership.json"
  fi
  record_hashes
  state succeeded; event succeeded 'Health checks passed; no confirmation wait'; guard_remove
 else
  event failed 'Deployment failed or exceeded 120 seconds'
  rollback
 fi
}
watch() {
 while :; do
  s=$(cat "$HERE/state" 2>/dev/null || echo preparing)
  case "$s" in succeeded|rolled_back) exit 0;; recovery_failed) exit 1;; esac
  oldboot=$(cat "$HERE/boot-id" 2>/dev/null || true)
  start=$(cat "$HERE/start-uptime" 2>/dev/null || echo 0)
  executor=$(cat "$HERE/executor-pid" 2>/dev/null || echo 0)
  rebooted=0
  [ "$oldboot" = "$(cat /proc/sys/kernel/random/boot_id)" ] || rebooted=1
  if [ "$rebooted" = 1 ] || ! kill -0 "$executor" 2>/dev/null || [ "$(clock)" -ge "$((start + 120))" ]; then
   if [ "$rebooted" != 1 ]; then
    worker=$(cat "$HERE/worker-pid" 2>/dev/null || echo 0)
    [ "$worker" -le 1 ] || kill -TERM -- "-$worker" 2>/dev/null || true
    if [ "$(clock)" -ge "$((start + 125))" ]; then
     [ "$executor" -le 1 ] || kill -KILL "$executor" 2>/dev/null || true
    fi
   fi
   # The outer executor normally owns the lock; never run APK concurrently.
   if (exec 9>"$ROOT/lock"; flock -n 9); then
    lock; rollback; exit $?
   fi
  fi
  sleep 1
 done
}
case "${1:-}" in
 apply) apply;; prepare-body) prepare_body;; apply-body) apply_body;; rollback-body) rollback_body;;
 rollback) lock; rollback;; watch) watch;; *) exit 2;;
esac
