# tile_registry.py
# Discovers, probes, and manages AURA tile definitions at runtime.
# No UI framework dependency — safe to import from aura.py or tools.
#
# Tile files live in ~/aura/tiles/*.py.  Each must define a TILE dict and
# may define a DataSource class.
#
# Public API:
#   init()
#   get_all()           -> {tile_id: tile_def}
#   get_available()     -> {tile_id: tile_def}
#   get(tile_id)        -> tile_def | None
#   is_available(tid)   -> bool
#   get_by_category(c)  -> {tile_id: tile_def}
#   activate(tile_id, instance_id, datasource)
#   deactivate(instance_id)
#   get_active()        -> {instance_id: entry}
#   get_datasource(iid) -> datasource | None
#   get_state_summary() -> {instance_id: {tile_id, state}}
#   get_aura_context()  -> str
#   control(iid, ctrl_id, params) -> str
#   query(filter_dict)  -> [result]
#   reprobe()
#   summary()

import os
import sys
import threading
import importlib.util

_tiles = {}     # tile_id -> tile_def dict (definition + runtime meta)
_active = {}    # instance_id -> {"tile_id": ..., "datasource": ...}
_lock = threading.Lock()
_initialised = False


# ---------------------------------------------------------------------------
# Init / discovery
# ---------------------------------------------------------------------------

def init():
    """Discover and probe all tile files.  Safe to call multiple times."""
    global _initialised
    if _initialised:
        return
    _discover()
    _initialised = True


def _discover():
    tiles_dir = os.path.expanduser("~/aura/tiles")
    if not os.path.isdir(tiles_dir):
        return
    for filename in sorted(os.listdir(tiles_dir)):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue
        tile_id = filename[:-3]
        path = os.path.join(tiles_dir, filename)
        _load_tile_file(tile_id, path)


def _load_tile_file(tile_id, path):
    """Load one tile file.  Records error against the tile and continues on failure."""
    try:
        spec = importlib.util.spec_from_file_location(f"tiles.{tile_id}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        if not hasattr(mod, "TILE"):
            return  # Not a tile file — silently skip

        tile_def = dict(mod.TILE)
        tile_def["_module"] = mod
        tile_def["_path"] = path
        tile_def["_error"] = None
        tile_def["_available"] = False
        tile_def["_unavailable_reasons"] = []
        tile_def["_datasource_class"] = getattr(mod, "DataSource", None)

        _probe(tile_def)
        _tiles[tile_id] = tile_def

    except Exception as exc:
        _tiles[tile_id] = {
            "id": tile_id,
            "name": tile_id,
            "category": "",
            "_path": path,
            "_error": str(exc),
            "_available": False,
            "_unavailable_reasons": [f"Load error: {exc}"],
            "_datasource_class": None,
            "_module": None,
        }


def _probe(tile_def):
    """Run all availability checks and update _available / _unavailable_reasons."""
    reasons = []
    requires = tile_def.get("requires", {})

    # --- Python packages ---
    for pkg in requires.get("packages", []):
        try:
            __import__(pkg)
        except ImportError:
            reasons.append(f"Missing package: {pkg}")

    # --- Hardware probes ---
    # Each entry may be a bare callable or a dict {"name": str, "probe": callable}
    for probe in requires.get("hardware", []):
        try:
            if callable(probe):
                if not probe():
                    reasons.append(
                        f"Hardware probe failed: {getattr(probe, '__name__', repr(probe))}"
                    )
            elif isinstance(probe, dict):
                fn = probe.get("probe")
                name = probe.get("name", "hardware")
                if callable(fn):
                    if not fn():
                        reasons.append(f"Hardware not found: {name}")
                else:
                    reasons.append(f"Hardware probe '{name}' is not callable")
        except Exception as exc:
            name = probe.get("name", "hardware") if isinstance(probe, dict) else repr(probe)
            reasons.append(f"Hardware probe error ({name}): {exc}")

    # --- Required config keys ---
    for key in requires.get("config", []):
        try:
            import db
            db.init_db()
            val = db.get(key)
            if not val:
                reasons.append(f"Config key not set: {key}")
        except Exception as exc:
            reasons.append(f"Config check error ({key}): {exc}")

    tile_def["_unavailable_reasons"] = reasons
    tile_def["_available"] = len(reasons) == 0


# ---------------------------------------------------------------------------
# Tile queries
# ---------------------------------------------------------------------------

def get_all():
    """All tiles without load errors."""
    return {tid: t for tid, t in _tiles.items() if not t.get("_error")}


def get_available():
    """Tiles that passed all availability checks."""
    return {tid: t for tid, t in _tiles.items() if t.get("_available")}


def get(tile_id):
    return _tiles.get(tile_id)


def is_available(tile_id):
    t = _tiles.get(tile_id)
    return bool(t and t.get("_available"))


def get_by_category(category):
    return {tid: t for tid, t in _tiles.items() if t.get("category") == category}


# ---------------------------------------------------------------------------
# Active instance management
# ---------------------------------------------------------------------------

def activate(tile_id, instance_id, datasource=None):
    """Register an active tile instance (called by the UI when a tile is shown)."""
    with _lock:
        _active[instance_id] = {
            "tile_id": tile_id,
            "datasource": datasource,
        }


def deactivate(instance_id):
    """Remove an active tile instance."""
    with _lock:
        _active.pop(instance_id, None)


def get_active():
    with _lock:
        return dict(_active)


def get_datasource(instance_id):
    with _lock:
        entry = _active.get(instance_id)
        return entry["datasource"] if entry else None


# ---------------------------------------------------------------------------
# State / context
# ---------------------------------------------------------------------------

def get_state_summary():
    """Collect get_state() from every active datasource."""
    result = {}
    with _lock:
        snapshot = dict(_active)
    for instance_id, entry in snapshot.items():
        ds = entry.get("datasource")
        if ds and hasattr(ds, "get_state"):
            try:
                state = ds.get_state()
                result[instance_id] = {"tile_id": entry["tile_id"], "state": state}
            except Exception as exc:
                result[instance_id] = {
                    "tile_id": entry["tile_id"],
                    "state": None,
                    "error": str(exc),
                }
    return result


def get_aura_context():
    """
    Build a human-readable context string from all active tiles that have an
    aura_context template.  Templates are Python str.format() strings whose
    placeholders match state keys returned by the tile's DataSource.get_state().
    """
    parts = []
    with _lock:
        snapshot = dict(_active)
    for instance_id, entry in snapshot.items():
        tile_id = entry["tile_id"]
        tile_def = _tiles.get(tile_id, {})
        template = tile_def.get("aura_context")
        if not template:
            continue
        state = {}
        ds = entry.get("datasource")
        if ds and hasattr(ds, "get_state"):
            try:
                state = ds.get_state() or {}
            except Exception:
                pass
        try:
            parts.append(template.format(**state))
        except (KeyError, ValueError):
            parts.append(template)  # Fallback: include unformatted template
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Control routing
# ---------------------------------------------------------------------------

def control(instance_id, control_id, params=None):
    """
    Route a control command to the named active tile's DataSource.control().
    Returns a human-readable result string.
    """
    with _lock:
        entry = _active.get(instance_id)
    if not entry:
        return f"No active tile with instance_id '{instance_id}'"
    ds = entry.get("datasource")
    if not ds:
        return f"Tile '{entry['tile_id']}' has no datasource"
    if not hasattr(ds, "control"):
        return f"Tile '{entry['tile_id']}' does not support control commands"
    try:
        return str(ds.control(control_id, params or {}))
    except Exception as exc:
        return f"Control error in '{entry['tile_id']}': {exc}"


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def query(filter_dict=None):
    """
    Return active tiles matching all criteria in filter_dict.
    Supported keys:
        tile_id   – exact tile_id match
        category  – tile category
        type      – data.type field
        state.<k> – match state key k against a value
    Returns a list of dicts: {instance_id, tile_id, name, category, state}.
    """
    filter_dict = filter_dict or {}
    with _lock:
        snapshot = dict(_active)

    results = []
    for instance_id, entry in snapshot.items():
        tile_id = entry["tile_id"]
        tile_def = _tiles.get(tile_id, {})

        match = True
        for key, val in filter_dict.items():
            if key == "tile_id":
                if tile_id != val:
                    match = False
            elif key == "category":
                if tile_def.get("category") != val:
                    match = False
            elif key == "type":
                if tile_def.get("data", {}).get("type") != val:
                    match = False
            elif key.startswith("state."):
                state_key = key[6:]
                ds = entry.get("datasource")
                if ds and hasattr(ds, "get_state"):
                    try:
                        state = ds.get_state() or {}
                        if state.get(state_key) != val:
                            match = False
                    except Exception:
                        match = False
                else:
                    match = False
            if not match:
                break

        if match:
            state = None
            ds = entry.get("datasource")
            if ds and hasattr(ds, "get_state"):
                try:
                    state = ds.get_state()
                except Exception:
                    pass
            results.append({
                "instance_id": instance_id,
                "tile_id": tile_id,
                "name": tile_def.get("name", tile_id),
                "category": tile_def.get("category", ""),
                "state": state,
            })
    return results


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

def reprobe():
    """Re-run availability checks for all registered tiles without reloading files."""
    for tile_def in _tiles.values():
        if tile_def.get("_error"):
            continue
        _probe(tile_def)


def summary():
    """Print a human-readable table of all tiles and their availability."""
    print(f"\n=== Tile Registry ({len(_tiles)} registered) ===")
    for tile_id, tile_def in sorted(_tiles.items()):
        if tile_def.get("_error"):
            status = "ERROR"
            detail = tile_def["_error"]
        elif tile_def.get("_available"):
            status = "OK   "
            detail = tile_def.get("description", "")[:60]
        else:
            status = "N/A  "
            reasons = tile_def.get("_unavailable_reasons", [])
            detail = "; ".join(reasons)
        name = tile_def.get("name", tile_id)
        cat  = tile_def.get("category", "")
        print(f"  [{status}] {tile_id:<18} {name:<22} ({cat:<10}) {detail}")

    with _lock:
        n_active = len(_active)
    print(f"\nActive instances: {n_active}")
    print("=" * 50 + "\n")
