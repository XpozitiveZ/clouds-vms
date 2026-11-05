#!/usr/bin/env bash


# сбор ошибок
set -uo pipefail

cd "$(dirname "$0")/.." 

# проверка запуска от root
[ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 1; }

# проверка установленных программ
for t in docker virsh curl jq ssh ssh-keygen; do command -v $t >/dev/null || { echo "Не установлено: $t"; exit 1; }; done

API=http://localhost:8000
BASE=/var/lib/libvirt/images/clouds
IMG_URL=${IMG_URL:-https://cloud-images.ubuntu.com/resolute/current/resolute-server-cloudimg-amd64.img}
IMG="$BASE/images/$(basename "$IMG_URL")"
KEY=/tmp/vm_check_key
FAIL=0
ok()   { 
  echo "[ OK ] $*"
}
bad()  { 
  echo "[FAIL] $*"
  FAIL=1
}
cleanup() {
  echo "--- cleanup"
 ./scripts/cleanup.sh >/dev/null 2>&1; rm -f $KEY $KEY.pub
}
trap cleanup EXIT

# Скачивание образа и создание ключа

mkdir -p "$BASE/images"
chmod 755 "$BASE" "$BASE/images"

[ -f "$IMG" ] || wget -q -O "$IMG" "$IMG_URL" || { bad "скачивание образа"; exit 1; }

rm -f $KEY $KEY.pub
ssh-keygen -q -t ed25519 -N "" -f $KEY

# Сборка докера и проверка работы сервисы

docker compose up -d --build >/dev/null 2>&1 || { bad "docker compose up"; exit 1; }

for i in $(seq 1 30); do
  curl -sf $API/health >/dev/null && break
  sleep 1
done

curl -sf $API/health >/dev/null && ok "API поднялся" || { bad "API не поднялся"; exit 1; }

# Проверка ручек API

code=$(curl -s -o /dev/null -w '%{http_code}' -X POST $API/images -H 'Content-Type: application/json' \
  -d "{\"name\":\"ubuntu\",\"path\":\"$IMG\",\"description\":\"test image\"}")
[ "$code" = 201 ] && ok "образ добавился" || bad "образ не добавился ($code)"
curl -s $API/images | jq -e '.[0].name=="ubuntu"' >/dev/null && ok "образ есть в списке" || bad "образ в списке не найден"

PUB=$(cat $KEY.pub)
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST $API/vms -H 'Content-Type: application/json' \
  -d "{\"name\":\"check1\",\"image\":\"ubuntu\",\"cpus\":1,\"ram_mb\":1024,\"hostname\":\"check-host\",\"ssh_key\":\"$PUB\"}")
[ "$code" = 201 ] && ok "VM успешно создана через API" || bad "создание VM не удалось ($code)"
virsh -c qemu:///system list --all | grep -q vm-check1 && ok "VM visible on host via virsh" || bad "VM not visible in virsh"

code=$(curl -s -o /dev/null -w '%{http_code}' -X POST $API/vms -H 'Content-Type: application/json' \
  -d '{"name":"toobig","image":"ubuntu","cpus":1,"ram_mb":999999}')
[ "$code" = 409 ] && ok "проверка на выход ресурсов за пределы пройдена (409)" || bad "проверка выхода ресурсов за пределы не пройдена ($code)"

IP=""
for i in $(seq 1 90); do
  IP=$(curl -s $API/vms/check1 | jq -r '.ip // empty'); [ -n "$IP" ] && break; sleep 2
done
[ -n "$IP" ] && ok "VM получила IP: $IP" || bad "VM не получила IP"
if [ -n "$IP" ]; then
  H=""
  for i in $(seq 1 60); do
    H=$(ssh -i $KEY -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=3 \
        ubuntu@$IP hostname 2>/dev/null) && break
    sleep 3
  done
  [ "$H" = "check-host" ] && ok "cloud-init: ssh ключ и hostname получены" || bad "ssh/cloud-init (hostname='$H')"
fi

curl -s -X POST "$API/vms/check1/stop?force=true" >/dev/null; sleep 3
[ "$(curl -s $API/vms/check1 | jq -r .state)" = stopped ] && ok "VM остановлена" || bad "VM не остановлена"
curl -s -X POST $API/vms/check1/start >/dev/null; sleep 3
[ "$(curl -s $API/vms/check1 | jq -r .state)" = running ] && ok "VM запущена" || bad "VM не запущена"

docker compose restart >/dev/null 2>&1
for i in $(seq 1 30); do curl -sf $API/health >/dev/null && break; sleep 1; done
[ "$(curl -s $API/vms/check1 | jq -r .state)" = running ] && ok "VM восстановлена после перезапуска" || bad "VM не восстановилась после перезапуска"
virsh -c qemu:///system destroy vm-check1 >/dev/null 2>&1
docker compose restart >/dev/null 2>&1
for i in $(seq 1 30); do curl -sf $API/health >/dev/null && break; sleep 1; done
sleep 3
[ "$(curl -s $API/vms/check1 | jq -r .state)" = running ] && ok "VM восстановлена после перезапуска хоста" || bad "VM не восстановилась после перезапуска хоста"

code=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE $API/vms/check1)
[ "$code" = 200 ] && ok "VM удалена" || bad "VM не смогла быть удалена ($code)"
virsh -c qemu:///system list --all | grep -q vm-check1 && bad "VM всё ещё находится в системе" || ok "VM пропала из системы"

[ $FAIL = 0 ] && echo "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" || echo "ЧАСТЬ ПРОВЕРОК ПРОВАЛИЛАСЬ"
exit $FAIL
