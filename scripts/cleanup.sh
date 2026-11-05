#!/usr/bin/env bash

cd "$(dirname "$0")/.."
BASE=/var/lib/libvirt/images/clouds
docker compose down -v >/dev/null 2>&1
for d in $(virsh -c qemu:///system list --all --name | grep -E '^(vm-|base-vm$)'); do
  virsh -c qemu:///system destroy "$d" >/dev/null 2>&1
  virsh -c qemu:///system undefine "$d" --nvram >/dev/null 2>&1 || virsh -c qemu:///system undefine "$d" >/dev/null 2>&1
done
rm -rf "$BASE/vms" "$BASE/base"
[ "$1" = "--all" ] && rm -rf "$BASE"
echo "cleanup done"
