#!/usr/bin/env bash

set -euo pipefail
BASE=/var/lib/libvirt/images/clouds
IMG_URL=${IMG_URL:-https://cloud-images.ubuntu.com/resolute/current/resolute-server-cloudimg-amd64.img}
NAME=base-vm
KEY=${SSH_PUB_KEY_FILE:-$HOME/.ssh/id_ed25519.pub}

[ -f "$KEY" ] || { echo "no ssh public key: $KEY (ssh-keygen -t ed25519)"; exit 1; }
mkdir -p "$BASE/images" "$BASE/base"; chmod 755 "$BASE" "$BASE/images" "$BASE/base"
IMG="$BASE/images/$(basename "$IMG_URL")"
[ -f "$IMG" ] || wget -O "$IMG" "$IMG_URL"

cat > "$BASE/base/user-data" <<EOF
#cloud-config
hostname: $NAME
users:
  - name: ubuntu
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - $(cat "$KEY")
EOF
printf 'instance-id: %s\nlocal-hostname: %s\n' "$NAME" "$NAME" > "$BASE/base/meta-data"
cloud-localds "$BASE/base/seed.iso" "$BASE/base/user-data" "$BASE/base/meta-data"
qemu-img create -f qcow2 -F qcow2 -b "$IMG" "$BASE/base/disk.qcow2" 10G

virt-install --connect qemu:///system --name "$NAME" \
  --memory 1024 --vcpus 1 --import \
  --disk "$BASE/base/disk.qcow2" \
  --disk "$BASE/base/seed.iso,device=cdrom" \
  --osinfo detect=on,name=ubuntu24.04 \
  --network network=default,model=virtio \
  --graphics none --noautoconsole
echo "Подождите ~60 секунд, затем выполните: 'virsh -c qemu:///system domifaddr $NAME ; ssh ubuntu@<ip>'"
