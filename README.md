# Clouds

Сервис управления виртуальными машинами QEMU/KVM: HTTP API (FastAPI) в Docker-контейнере,
который через сокет libvirt хоста создаёт, останавливает и удаляет ВМ. ВМ видны на хосте через `virsh`.

Проверено на: Ubuntu Server 26.04, libvirt/QEMU из штатных репозиториев (виртуалка Hyper-V с вложенной виртуализацией).

## Реализованные пункты

| Пункт | Описание | Где |
|---|---|---|
| База | Запуск ВМ без GUI (`virt-install`) + описание | `scripts/base-vm.sh`, раздел «Базовое задание» |
| 1 | HTTP API: создание / остановка / запуск / удаление ВМ | `app/main.py` |
| 2 | cloud-init: user_data, SSH-ключ, hostname (seed-ISO NoCloud) | `app/vm.py` |
| 5 | Приложение в контейнере управляет ВМ через сокет хостового libvirt | `docker-compose.yml` |
| 6 | Восстановление состояния после перезапуска контейнера/хоста | `reconcile()` в `app/main.py` |
| 7 | Регистрация образов (название, путь, описание) | `POST /images` |
| 9 | Квоты на суммарные vCPU / RAM (HTTP 409) | `MAX_VCPUS`, `MAX_RAM_MB` |
| 10 | Простой веб-интерфейс | `static/index.html`, открыть `http://localhost/` |
| 11 | Один скрипт проверки всего | `scripts/check.sh` |
| 12 | Скрипт очистки | `scripts/cleanup.sh` |

## Требования

Ubuntu 26.04 с `/dev/kvm`:

```bash
sudo apt install -y qemu-system-x86 qemu-utils libvirt-daemon-system libvirt-clients virtinst \
    cloud-image-utils cpu-checker curl jq docker.io docker-compose-v2
sudo adduser $USER libvirt && sudo adduser $USER kvm   # затем перелогиниться
kvm-ok
sudo virsh -c qemu:///system net-list --all            # сеть default должна быть active
```

## Базовое задание: ВМ без GUI

```bash
ssh-keygen -t ed25519            # если ключа ещё нет
sudo ./scripts/base-vm.sh
virsh -c qemu:///system list --all
virsh -c qemu:///system domifaddr base-vm   # через ~1 мин появится IP
ssh ubuntu@<ip>
```

Ожидаемый результат: ВМ `base-vm` в состоянии running, вход по SSH-ключу под пользователем `ubuntu`.
Скрипт скачивает cloud-образ Ubuntu, создаёт qcow2-диск поверх него, собирает seed.iso с cloud-init и запускает `virt-install --graphics none`.

## Запуск приложения

```bash
sudo mkdir -p /var/lib/libvirt/images/clouds/images
sudo chmod -R 755 /var/lib/libvirt/images/clouds
sudo wget -P /var/lib/libvirt/images/clouds/images \
  https://cloud-images.ubuntu.com/resolute/current/resolute-server-cloudimg-amd64.img
docker compose up -d --build
```

Веб-интерфейс: `http://localhost/`, документация API: `http://localhost/docs`.

## API

```bash
# регистрация образа (путь должен быть внутри /var/lib/libvirt/images/clouds/images)
curl -X POST localhost:8000/images -H 'Content-Type: application/json' \
  -d '{"name":"ubuntu","path":"/var/lib/libvirt/images/clouds/images/resolute-server-cloudimg-amd64.img","description":"Ubuntu 26.04"}'

# создание ВМ
curl -X POST localhost:8000/vms -H 'Content-Type: application/json' \
  -d '{"name":"vm1","image":"ubuntu","cpus":1,"ram_mb":1024,"hostname":"vm1","ssh_key":"ssh-ed25519 AAAA..."}'

curl localhost:8000/vms                       # список и состояние, IP
curl -X POST localhost:8000/vms/vm1/stop      # ?force=true — жёсткая остановка
curl -X POST localhost:8000/vms/vm1/start
curl -X DELETE localhost:8000/vms/vm1
```

В `POST /vms` можно передать собственный `user_data` (текст `#cloud-config`), пример: `cloud-init/user-data.example`.

## Проверка и очистка

```bash
sudo ./scripts/check.sh      # полный тест; в конце выводит "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" и сам чистит за собой
sudo ./scripts/cleanup.sh    # удалить ВМ, диски, контейнер и том (--all удаляет и образы)
```

`check.sh` проверяет: запуск API, регистрацию образа, создание ВМ, видимость в `virsh`, квоты (409), IP, SSH по ключу и hostname из cloud-init,
остановку/запуск, восстановление состояния после рестарта контейнера и после остановки ВМ, удаление.

## Заметки

- Контейнер монтирует `/var/run/libvirt/libvirt-sock` хоста: доступ к нему равен root-доступу к гипервизору.
- Каталог `/var/lib/libvirt/images/clouds` смонтирован по одинаковому пути на хосте и в контейнере.
- Состояние (образы, ВМ, желаемое состояние) хранится в SQLite в томе `vm-data`; сами ВМ — постоянные домены libvirt.
