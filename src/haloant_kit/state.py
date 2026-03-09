"""原子 JSON 状态文件：save_state_atomic / load_state_safe。

替换点：push_engine.py、circuit_breaker.py、session_context.py、health.py、monitor/context.py
"""
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def save_state_atomic(path: Path, data: dict) -> None:
    """原子 JSON 写入：tempfile + os.replace。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_state_safe(path: Path, default: dict | None = None) -> dict:
    """安全 JSON 读取：文件不存在/损坏返回 default。"""
    path = Path(path)
    if default is None:
        default = {}
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        logger.debug("load_state_safe(%s): %s, returning default", path, e)
        return dict(default)
