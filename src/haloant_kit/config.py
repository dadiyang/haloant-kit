"""Configuration management — reads .env defaults and per-user config from users.json.

Generic version for all haloant projects. No trading/broker-specific logic.

Public API:
    DATA_DIR             — resolved data directory (Path)
    resolve_user_id()    — resolve user identity from arg or agent_config.json
    resolve_data_dir()   — resolve data directory from agent_config.json or default
    load_user_config()   — load per-user config from users.json (raw dict)
    save_user_config()   — atomically update a user's config in users.json
"""

import json
import logging
import os
import re
import tempfile
from pathlib import Path

from dotenv import load_dotenv

# Load .env from current working directory (project root)
_env_path = Path.cwd() / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

# ── Data directory ────────────────────────────────────────────────────────────

_DEFAULT_DATA_DIR = Path(os.getenv("HALOANT_DATA_DIR", str(Path.home() / ".haloant")))

DATA_DIR: Path = _DEFAULT_DATA_DIR  # public export, no I/O side effects

# ── User identity ─────────────────────────────────────────────────────────────

_USER_ID_RE = re.compile(r'^[a-z][a-z0-9_-]{0,31}$')


def _validate_user_id(user_id: str) -> str:
    """Validate user_id format. Raises ValueError on bad input."""
    if not _USER_ID_RE.match(user_id):
        raise ValueError(
            f"Invalid user_id '{user_id}': must be 1-32 chars, "
            f"start with a-z, contain only a-z 0-9 _ -"
        )
    return user_id


def resolve_user_id(explicit: str | None = None) -> str:
    """Resolve user identity: explicit arg > agent_config.json in cwd.

    Priority:
        1. Explicit --user arg (if provided)
        2. agent_config.json in current working directory
        3. Raise error
    """
    if explicit:
        return _validate_user_id(explicit)

    config_path = Path.cwd() / "agent_config.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"agent_config.json in {Path.cwd()} is malformed: {e}"
            ) from e
        user_id = data.get("user_id")
        if user_id:
            return _validate_user_id(user_id)

    raise RuntimeError(
        "Cannot determine user identity. "
        "Provide --user or ensure agent_config.json exists in working directory."
    )


def resolve_data_dir() -> Path:
    """Resolve data directory from agent_config.json or default.

    If agent_config.json in CWD contains a "data_dir" key, use that path.
    Otherwise fall back to HALOANT_DATA_DIR / ~/.haloant.
    """
    try:
        config_path = Path.cwd() / "agent_config.json"
        if config_path.exists():
            with open(config_path) as f:
                data = json.load(f)
            if "data_dir" in data:
                p = Path(data["data_dir"])
                p.mkdir(parents=True, exist_ok=True)
                return p
    except Exception:
        pass
    return _DEFAULT_DATA_DIR


# ── Users config (users.json) ────────────────────────────────────────────────

_users_cache: dict | None = None

# Users.json location: DATA_DIR / "config" / "users.json"
# Projects can also place it at project_root / "config" / "users.json"


def _get_users_json_path() -> Path:
    """Return path to users.json. Checks CWD/config first, then DATA_DIR/config."""
    cwd_path = Path.cwd() / "config" / "users.json"
    if cwd_path.exists():
        return cwd_path
    return DATA_DIR / "config" / "users.json"


def _load_users_cache() -> dict:
    """Load and cache all users from users.json."""
    global _users_cache
    if _users_cache is None:
        users_json = _get_users_json_path()
        if users_json.exists():
            try:
                with open(users_json) as f:
                    _users_cache = json.load(f).get("users", {})
            except (json.JSONDecodeError, OSError) as e:
                logging.getLogger(__name__).error(
                    "Failed to load %s: %s — using empty config", users_json, e)
                _users_cache = {}
        else:
            _users_cache = {}
    return _users_cache


def load_user_config(user_id: str) -> dict:
    """Load per-user config from users.json.

    Returns the raw user config dict without any business-specific interpretation.
    Projects should interpret domain-specific fields themselves.

    Returns empty dict if user not found.
    """
    users = _load_users_cache()
    return dict(users.get(user_id, {}))


def save_user_config(user_id: str, config: dict) -> None:
    """Atomically update a user's config in users.json.

    Merges `config` into the existing user entry (or creates a new one).
    Uses atomic write (write-to-tmp + rename) to prevent corruption.
    Invalidates the in-memory cache.
    """
    _validate_user_id(user_id)
    global _users_cache

    users_json = _get_users_json_path()

    # Read current file
    if users_json.exists():
        with open(users_json) as f:
            data = json.load(f)
    else:
        data = {"users": {}}

    if "users" not in data:
        data["users"] = {}

    # Merge config
    if user_id in data["users"]:
        data["users"][user_id].update(config)
    else:
        data["users"][user_id] = config

    # Atomic write
    users_json.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(users_json.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, str(users_json))
    except Exception:
        os.unlink(tmp_path)
        raise

    # Invalidate cache
    _users_cache = None


def get_all_user_ids() -> list[str]:
    """Return all configured user IDs."""
    return list(_load_users_cache().keys())
