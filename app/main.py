import os
import re
import shutil
import threading
import urllib.request
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from . import config, db, vm


app = FastAPI(title="Clouds")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
FILE_RE = re.compile(r"[^A-Za-z0-9._-]")
KEY_PREFIXES = ("ssh-", "ecdsa-", "sk-")


class ImageIn(BaseModel):
    name: str
    path: str
    description: str = ""


class ImageUrlIn(BaseModel):
    name: str
    url: str
    description: str = ""


class VmIn(BaseModel):
    name: str
    image: str
    cpus: int = Field(1, ge=1)
    ram_mb: int = Field(1024, ge=256)
    disk_gb: int = Field(10, ge=1)
    hostname: str | None = None
    ssh_key: str | None = None
    user_data: str | None = None


def reconcile():
    """Restore state after restart of the service or the host (item 6)."""
    c = vm.connect()
    try:
        for row in db.vm_list():
            n = row["name"]
            if vm.lookup(c, n) is None:
                if os.path.exists(os.path.join(vm.vm_dir(n), "disk.qcow2")):
                    vm.define_and_start(c, n, row["cpus"], row["ram_mb"], start=False)
                else:
                    db.vm_delete(n)
                    continue
                
            if row["desired"] == "running":
                vm.start(c, n)
    finally:
        c.close()


@app.on_event("startup")
def startup():
    os.makedirs(config.IMAGES_DIR, exist_ok=True)
    os.makedirs(config.VMS_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    db.init()
    reconcile()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "..", "static", "index.html"))

# ---------- images (item 7) ----------
def _check_new_image(name):
    if not NAME_RE.match(name):
        raise HTTPException(400, "image name must match [a-z0-9-]{1,31}")
    
    if db.image_exists(name):
        raise HTTPException(409, "image already exists")


def _safe_path(filename):
    fn = FILE_RE.sub("_", os.path.basename(filename or "")) or "image.img"

    path = os.path.join(config.IMAGES_DIR, fn)
    if os.path.exists(path):
        raise HTTPException(409, f"file {fn} already exists in images dir")
    
    return path


@app.post("/images", status_code=201)
def add_image(i: ImageIn):
    """Register a file that already in the images directory."""
    _check_new_image(i.name)

    real = os.path.realpath(i.path)
    if not real.startswith(config.IMAGES_DIR + os.sep):
        raise HTTPException(400, f"image must be inside {config.IMAGES_DIR}")
    
    if not os.path.isfile(real):
        raise HTTPException(400, "file not found")
    
    try:
        vm.image_format(real)
    except RuntimeError as e:
        raise HTTPException(400, f"not a disk image: {e}")
    db.image_add(i.name, real, i.description)

    return {"name": i.name, "path": real, "description": i.description}


@app.post("/images/upload", status_code=201)
def upload_image(name: str = Form(...), description: str = Form(""),
                 file: UploadFile = File(...)):
    """Upload an image file from the user's computer."""
    _check_new_image(name)
    path = _safe_path(file.filename)

    try:
        with open(path, "wb") as out:
            shutil.copyfileobj(file.file, out, 1024 * 1024)

        os.chmod(path, 0o644)
        vm.image_format(path)
    except Exception as e:
        if os.path.exists(path):
            os.remove(path)

        raise HTTPException(400, f"upload failed or not a disk image: {e}")
    db.image_add(name, path, description)

    return {"name": name, "path": path, "description": description}


def _download(name, url, path):
    tmp = path + ".part"
    try:
        with urllib.request.urlopen(url, timeout=30) as r, open(tmp, "wb") as out:
            shutil.copyfileobj(r, out, 1024 * 1024)

        vm.image_format(tmp)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        db.image_set_status(name, "ready")
    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)

        db.image_set_status(name, "error", str(e)[:300])


@app.post("/images/download", status_code=202)
def download_image(i: ImageUrlIn):
    """Download an image by URL in the background (status: downloading -> ready/error)."""
    _check_new_image(i.name)

    if not i.url.startswith(("http://", "https://")):
        raise HTTPException(400, "only http(s) URLs are supported")
    
    path = _safe_path(i.url.split("?")[0].rsplit("/", 1)[-1])
    db.image_add(i.name, path, i.description, status="downloading")
    threading.Thread(target=_download, args=(i.name, i.url, path), daemon=True).start()

    return {"name": i.name, "status": "downloading"}


@app.get("/images")
def list_images():
    return db.image_list()


@app.delete("/images/{name}")
def delete_image(name: str):
    img = db.image_get(name)
    if not img:
        raise HTTPException(404, "image not found")
    
    if db.vm_uses_image(name):
        raise HTTPException(409, "image is used by a VM")
    
    if img["status"] == "downloading":
        raise HTTPException(409, "image is being downloaded")
    
    path = img["path"]
    if os.path.realpath(path).startswith(config.IMAGES_DIR + os.sep) and os.path.isfile(path):
        os.remove(path)

    db.image_delete(name)

    return {"name": name, "action": "deleted"}


# ---------- quotas (item 9) ----------
def check_quota(cpus, ram_mb):
    used = db.vm_usage()
    if used["cpus"] + cpus > config.MAX_VCPUS or used["ram_mb"] + ram_mb > config.MAX_RAM_MB:
        raise HTTPException(
            409, f"quota exceeded: used {used['cpus']} vCPU/{used['ram_mb']} MB, "
                 f"limit {config.MAX_VCPUS} vCPU/{config.MAX_RAM_MB} MB")


# ---------- VMs (item 1) ----------
@app.post("/vms", status_code=201)
def create_vm(v: VmIn):
    if not NAME_RE.match(v.name):
        raise HTTPException(400, "name must match [a-z0-9-]{1,31}")
    if db.vm_exists(v.name):
        raise HTTPException(409, "vm already exists")
    
    img = db.image_get(v.image)
    if not img:
        raise HTTPException(404, "image not registered")
    if img["status"] != "ready":
        raise HTTPException(409, f"image is not ready (status: {img['status']})")
    
    keys = vm.split_keys(v.ssh_key)
    if any(not k.startswith(KEY_PREFIXES) for k in keys):
        raise HTTPException(400, "ssh_key must be a public key (ssh-ed25519 / ssh-rsa / ...)")
    
    check_quota(v.cpus, v.ram_mb)
    hostname = v.hostname or v.name
    c = vm.connect()

    try:
        vm.make_disk(v.name, img["path"], v.disk_gb)
        vm.make_seed(v.name, hostname, v.ssh_key, v.user_data)
        db.vm_add(v.name, v.image, v.cpus, v.ram_mb, v.disk_gb, hostname)
        vm.define_and_start(c, v.name, v.cpus, v.ram_mb)
    except Exception as e:
        vm.delete(c, v.name)
        db.vm_delete(v.name)
        raise HTTPException(500, str(e))
    finally:
        c.close()
    
    return get_vm(v.name)


@app.get("/vms")
def list_vms():
    c = vm.connect()
    try:
        return [vm.info(c, r["name"], r) for r in db.vm_list()]
    finally:
        c.close()


def _row(name):
    r = db.vm_get(name)
    if not r:
        raise HTTPException(404, "vm not found")
    
    return r


@app.get("/vms/{name}")
def get_vm(name: str):
    row = _row(name)
    c = vm.connect()
    try:
        return vm.info(c, name, row)
    finally:
        c.close()


@app.post("/vms/{name}/stop")
def stop_vm(name: str, force: bool = False):
    """Graceful ACPI shutdown; forced power-off after STOP_TIMEOUT s or at once with force=true."""
    _row(name)
    db.vm_set_desired(name, "stopped")

    c = vm.connect()
    try:
        vm.stop(c, name, force)
    finally:
        c.close()
    if not force:
        vm.force_off_later(name, lambda: db.vm_get_desired(name) == "stopped")
    
    return {"name": name, "action": "stop", "force": force,
            "note": None if force else f"forced power-off in {config.STOP_TIMEOUT}s if guest ignores ACPI"}


@app.post("/vms/{name}/start")
def start_vm(name: str):
    _row(name)
    db.vm_set_desired(name, "running")

    c = vm.connect()
    try:
        vm.start(c, name)
    finally:
        c.close()
        
    return {"name": name, "action": "start"}


@app.delete("/vms/{name}")
def delete_vm(name: str):
    _row(name)

    c = vm.connect()
    try:
        vm.delete(c, name)
    finally:
        c.close()

    db.vm_delete(name)

    return {"name": name, "action": "deleted"}
