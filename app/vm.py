import json
import os
import shutil
import subprocess
import threading
import time
import libvirt
from . import config

STATES = {
    libvirt.VIR_DOMAIN_NOSTATE: "unknown",
    libvirt.VIR_DOMAIN_RUNNING: "running",
    libvirt.VIR_DOMAIN_BLOCKED: "blocked",
    libvirt.VIR_DOMAIN_PAUSED: "paused",
    libvirt.VIR_DOMAIN_SHUTDOWN: "shutting-down",
    libvirt.VIR_DOMAIN_SHUTOFF: "stopped",
    libvirt.VIR_DOMAIN_CRASHED: "crashed",
}


def connect():
    return libvirt.open(config.LIBVIRT_URI)


def dname(name):
    return config.PREFIX + name


def vm_dir(name):
    return os.path.join(config.VMS_DIR, name)


def lookup(c, name):
    try:
        return c.lookupByName(dname(name))
    except libvirt.libvirtError:
        return None


def domain_xml(name, cpus, ram_mb, disk, seed):
    return f"""<domain type='kvm'>
  <name>{dname(name)}</name>
  <memory unit='MiB'>{ram_mb}</memory>
  <vcpu>{cpus}</vcpu>
  <os><type arch='x86_64' machine='q35'>hvm</type><boot dev='hd'/></os>
  <features><acpi/><apic/></features>
  <cpu mode='host-passthrough'/>
  <clock offset='utc'/>
  <on_reboot>restart</on_reboot>
  <devices>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='{disk}'/>
      <target dev='vda' bus='virtio'/>
    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{seed}'/>
      <target dev='sda' bus='sata'/>
      <readonly/>
    </disk>
    <interface type='network'>
      <source network='default'/>
      <model type='virtio'/>
    </interface>
    <serial type='pty'><target port='0'/></serial>
    <console type='pty'><target type='serial' port='0'/></console>
  </devices>
</domain>"""


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}: {r.stderr.strip()}")
    return r.stdout


def image_format(path):
    """Validates the file as a disk image and returns its format."""
    return json.loads(_run(["qemu-img", "info", "--output=json", path]))["format"]


def make_disk(name, image_path, disk_gb):
    d = vm_dir(name)
    os.makedirs(d, exist_ok=True)
    os.chmod(d, 0o755)
    disk = os.path.join(d, "disk.qcow2")
    _run(["qemu-img", "create", "-f", "qcow2", "-F", image_format(image_path),
          "-b", image_path, disk, f"{disk_gb}G"])
    return disk


def split_keys(ssh_key):
    return [k.strip() for k in (ssh_key or "").splitlines() if k.strip()]


def default_user_data(hostname, ssh_key):
    keys = split_keys(ssh_key)
    keys_yaml = ""
    if keys:
        keys_yaml = "\n    ssh_authorized_keys:" + "".join(f"\n      - {k}" for k in keys)
    return f"""#cloud-config
hostname: {hostname}
manage_etc_hosts: true
users:
  - name: ubuntu
    sudo: ALL=(ALL) NOPASSWD:ALL
    lock_passwd: false
    passwd: '*'{keys_yaml}
"""


def make_seed(name, hostname, ssh_key, user_data):
    d = vm_dir(name)
    ud = os.path.join(d, "user-data")
    md = os.path.join(d, "meta-data")
    with open(ud, "w") as f:
        f.write(user_data or default_user_data(hostname, ssh_key))
    with open(md, "w") as f:
        f.write(f"instance-id: {dname(name)}\nlocal-hostname: {hostname}\n")
    seed = os.path.join(d, "seed.iso")
    _run(["cloud-localds", seed, ud, md])
    return seed


def define_and_start(c, name, cpus, ram_mb, start=True):
    d = vm_dir(name)
    xml = domain_xml(name, cpus, ram_mb, os.path.join(d, "disk.qcow2"),
                     os.path.join(d, "seed.iso"))
    dom = c.defineXML(xml)  # persistent domain
    if start and not dom.isActive():
        dom.create()
    return dom


def info(c, name, row):
    dom = lookup(c, name)
    state, ip = "missing", None
    if dom is not None:
        state = STATES.get(dom.state()[0], "unknown")
        if dom.isActive():
            try:
                ifs = dom.interfaceAddresses(libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_LEASE)
                for v in ifs.values():
                    for a in v.get("addrs") or []:
                        if a["type"] == libvirt.VIR_IP_ADDR_TYPE_IPV4:
                            ip = a["addr"]
            except libvirt.libvirtError:
                pass
    return {**row, "state": state, "ip": ip}


def stop(c, name, force=False):
    """force=True: power off at once; otherwise ACPI shutdown request."""
    dom = lookup(c, name)
    if dom and dom.isActive():
        dom.destroy() if force else dom.shutdown()


def force_off_later(name, still_wanted):
    """ACPI shutdown is ignored by guests without acpid (e.g. minimal Alpine):
    power the VM off after STOP_TIMEOUT if it is still running."""
    def worker():
        deadline = time.time() + config.STOP_TIMEOUT
        c = connect()
        try:
            while time.time() < deadline:
                dom = lookup(c, name)
                if dom is None or not dom.isActive():
                    return
                time.sleep(1)
            dom = lookup(c, name)
            if dom is not None and dom.isActive() and still_wanted():
                dom.destroy()
        finally:
            c.close()
    threading.Thread(target=worker, daemon=True).start()


def start(c, name):
    dom = lookup(c, name)
    if dom and not dom.isActive():
        dom.create()


def delete(c, name):
    dom = lookup(c, name)
    if dom:
        if dom.isActive():
            dom.destroy()
        dom.undefine()
    shutil.rmtree(vm_dir(name), ignore_errors=True)
