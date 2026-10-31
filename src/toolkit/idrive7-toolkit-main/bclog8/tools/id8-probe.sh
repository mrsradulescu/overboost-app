#!/bin/sh
# id8-probe.sh - READ-ONLY look at an MGU head unit: does it have what the idrive7-apps kit needs?
#
# Changes nothing. Writes one text file, /tmp/id8-probe.txt (it is gone after a reboot).
# Run it on the unit over SSH (see below), copy the file back and send it to Claude.
#
#   scp  <ssh options> id8-probe.sh root@<unit-ip>:/tmp/
#   ssh  <ssh options> root@<unit-ip> "sh /tmp/id8-probe.sh"
#   scp  <ssh options> root@<unit-ip>:/tmp/id8-probe.txt .
#
# It prints no VIN, keys, certificates or provisioning data. Network addresses (ip addr) are in
# the output; blank them if you prefer.

OUT=/tmp/id8-probe.txt
CAT=/etc/oap/someip/someip-interfaces.json
APPS=/usr/lib/oap-mainapp/preinstalled_apps

have() { command -v "$1" >/dev/null 2>&1; }
sec() { printf '\n===== %s =====\n' "$1"; }

{
sec "probe"
echo "id8-probe v2  $(date 2>&1)"

sec "system"
uname -a 2>&1
cat /etc/os-release 2>&1 | head -20
grep -m1 'model name' /proc/cpuinfo 2>&1
echo "cpus: $(grep -c '^processor' /proc/cpuinfo 2>&1)"
head -2 /proc/meminfo 2>&1

sec "disk and mounts"
df -h 2>&1 | head -20
grep -E ' / | /var/opt| /usr | /etc ' /proc/mounts 2>&1

sec "node and tools"
for b in node python3 tcpdump strace nft ip ss busybox systemctl; do
  if have "$b"; then printf '%s: %s\n' "$b" "$(command -v "$b")"; else printf '%s: -\n' "$b"; fi
done
node --version 2>&1

sec "app platform (OAP)"
ls -ld /var/opt/public/oap /usr/lib/oap-mainapp /etc/oap /etc/oap/someip 2>&1
echo "-- mainapp"
ls /usr/lib/oap-mainapp 2>&1 | head -30
echo "-- preinstalled apps"
ls "$APPS" 2>&1 | head -80
echo "-- installed apps folders"
ls /var/opt/public/oap 2>&1 | head -40

sec "services"
if have systemctl; then
  systemctl list-units --no-pager --all 2>&1 | grep -E 'hmi|oap|winston|wayland|someip|tasu|renderd|weston' | head -40
fi
echo "-- processes"
ps 2>&1 | grep -E 'Hmi|hmi|oap|someip|node|winston|weston' | grep -v grep | head -30

sec "graphics (what the GPU renderer needs)"
ls -l /run/wayland 2>&1
ls /dev/dri 2>&1
ls /usr/lib 2>&1 | grep -i -E 'egl|gles|wayland|ivi' | head -10

sec "SOME/IP catalog"
ls -l "$CAT" 2>&1
if [ -f "$CAT" ]; then
  echo "services in catalog: $(grep -o '"serviceId"' "$CAT" | wc -l)"
  echo "-- interfaces the kit's apps use (count of mentions; 0 = missing on this unit)"
  for i in controlelements.FrontCCU KombiInterface_2 checkcontrol.CheckControlMaster vehicle.CDS vehicle.CDS2 \
           vehicle.Vehicle2 drivingexcitement.FES misc.MDrive infrastructure.VehicleInformation \
           tasu.InternalTasu; do
    printf '%s: %s\n' "$i" "$(grep -c "$i" "$CAT")"
  done
  echo "-- UDS gateway for the ECU reading (all four must be >0 for it to work)"
  for i in InternalTasu sendTasuRequest sendTasuResponse SystemFunctionsInternalTasuInst1; do
    printf '%s: %s\n' "$i" "$(grep -c "$i" "$CAT")"
  done
  echo "-- native SOME/IP module and per-app interface folders (the second way to reach the gateway)"
  find /usr /opt /var/opt -maxdepth 6 \( -name 'someip*.node' -o -name 'someipInterfaces' \) 2>/dev/null | head -10
  echo "-- interface names that could carry BC / steering wheel / cluster / trip data"
  grep -o '"[A-Za-z0-9_.]*\(Kombi\|Cluster\|Button\|Steer\|Lenk\|Trip\|Board\|Tacho\|Control\|Switch\|Stalk\)[A-Za-z0-9_.]*"' "$CAT" 2>&1 | sort -u | head -120
fi

sec "HMI variant and widget templates used by BMW's own apps"
n=0
for t in "$APPS"/*.tgz; do
  [ -f "$t" ] || continue
  n=$((n + 1)); [ "$n" -gt 3 ] && break
  echo "-- $t"
  tar tzf "$t" 2>/dev/null | grep -E 'rhmiData/(application.json|uiDescriptions/[^/]+/(application.xml)?)$' | head -8
  p=$(tar tzf "$t" 2>/dev/null | grep -E 'rhmiData/application.json$' | head -1)
  [ -n "$p" ] && { echo "[application.json, first 2500 bytes]"; tar xzOf "$t" "$p" 2>/dev/null | head -c 2500; echo; }
  x=$(tar tzf "$t" 2>/dev/null | grep -E 'uiDescriptions/[^/]+/application.xml$' | head -1)
  [ -n "$x" ] && { echo "[widget templates in $x]"; tar xzOf "$t" "$x" 2>/dev/null | grep -o 'widget="[A-Za-z0-9_]*"' | sort -u | head -60; }
done

sec "network interfaces"
if have ip; then ip -br addr 2>&1 | head -30; else ifconfig 2>&1 | head -40; fi

sec "end"
echo "done"
} > "$OUT" 2>&1

echo "written: $OUT ($(wc -c < "$OUT") bytes)"
