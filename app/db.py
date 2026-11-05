import sqlite3
from . import config


def _conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _query(sql, args=()):
    with _conn() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def _exec(sql, args=()):
    with _conn() as c:
        c.execute(sql, args)


def init():
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS images(
            name TEXT PRIMARY KEY, path TEXT NOT NULL, description TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS vms(
            name TEXT PRIMARY KEY, image TEXT NOT NULL, cpus INTEGER NOT NULL,
            ram_mb INTEGER NOT NULL, disk_gb INTEGER NOT NULL, hostname TEXT,
            desired TEXT NOT NULL DEFAULT 'running');
        """)


# ---------- images ----------
def image_list():
    return _query("SELECT * FROM images")


def image_get(name):
    rows = _query("SELECT * FROM images WHERE name=?", (name,))
    return rows[0] if rows else None


def image_exists(name):
    return image_get(name) is not None


def image_add(name, path, description="", status="ready"):
    _exec("INSERT INTO images(name, path, description, status) VALUES(?,?,?,?)",
          (name, path, description, status))


def image_set_status(name, status, error=""):
    _exec("UPDATE images SET status=?, error=? WHERE name=?", (status, error, name))


def image_delete(name):
    _exec("DELETE FROM images WHERE name=?", (name,))


# ---------- VMs ----------
def vm_list():
    return _query("SELECT * FROM vms")


def vm_get(name):
    rows = _query("SELECT * FROM vms WHERE name=?", (name,))
    return rows[0] if rows else None


def vm_exists(name):
    return vm_get(name) is not None


def vm_add(name, image, cpus, ram_mb, disk_gb, hostname, desired="running"):
    _exec("INSERT INTO vms(name, image, cpus, ram_mb, disk_gb, hostname, desired) "
          "VALUES(?,?,?,?,?,?,?)",
          (name, image, cpus, ram_mb, disk_gb, hostname, desired))


def vm_set_desired(name, desired):
    _exec("UPDATE vms SET desired=? WHERE name=?", (desired, name))


def vm_get_desired(name):
    row = vm_get(name)
    return row["desired"] if row else None


def vm_delete(name):
    _exec("DELETE FROM vms WHERE name=?", (name,))


def vm_uses_image(image):
    return bool(_query("SELECT 1 FROM vms WHERE image=? LIMIT 1", (image,)))


def vm_usage():
    """Total vCPU and RAM of all VMs (for quotas)."""
    row = _query("SELECT COALESCE(SUM(cpus),0) AS cpus, COALESCE(SUM(ram_mb),0) AS ram_mb FROM vms")[0]
    return row

