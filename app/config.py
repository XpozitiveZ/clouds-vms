import os

BASE = os.environ.get("VM_BASE", "/var/lib/libvirt/images/clouds")
IMAGES_DIR = os.path.join(BASE, "images")
VMS_DIR = os.path.join(BASE, "vms")
DB_PATH = os.environ.get("VM_DB", "/data/clouds.db")
LIBVIRT_URI = os.environ.get("LIBVIRT_URI", "qemu:///system")
MAX_VCPUS = int(os.environ.get("MAX_VCPUS", "4"))
MAX_RAM_MB = int(os.environ.get("MAX_RAM_MB", "4096"))
STOP_TIMEOUT = int(os.environ.get("STOP_TIMEOUT", "30"))  # Жёсткий таймаут остановки
PREFIX = "vm-"  # префикс для вм сервиса
