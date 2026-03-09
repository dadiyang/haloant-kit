from haloant_kit.state import save_state_atomic, load_state_safe


def test_save_and_load(tmp_path):
    path = tmp_path / "test.json"
    save_state_atomic(path, {"key": "value"})
    assert load_state_safe(path) == {"key": "value"}


def test_load_missing_returns_default(tmp_path):
    path = tmp_path / "missing.json"
    assert load_state_safe(path, {"default": True}) == {"default": True}


def test_load_corrupt_returns_default(tmp_path):
    path = tmp_path / "corrupt.json"
    path.write_text("not json{{{")
    assert load_state_safe(path, {}) == {}


def test_load_missing_returns_empty_dict_when_no_default(tmp_path):
    """default=None 时返回空 dict。"""
    path = tmp_path / "missing.json"
    assert load_state_safe(path) == {}


def test_save_creates_parent_dirs(tmp_path):
    """父目录不存在时自动创建。"""
    path = tmp_path / "a" / "b" / "state.json"
    save_state_atomic(path, {"nested": True})
    assert load_state_safe(path) == {"nested": True}


def test_save_overwrites_existing(tmp_path):
    """覆盖已存在的状态文件。"""
    path = tmp_path / "test.json"
    save_state_atomic(path, {"v": 1})
    save_state_atomic(path, {"v": 2})
    assert load_state_safe(path) == {"v": 2}


def test_default_is_not_mutated(tmp_path):
    """确保返回的 default 是副本，不会被调用方意外修改原始 dict。"""
    path = tmp_path / "missing.json"
    default = {"key": "original"}
    result = load_state_safe(path, default)
    result["key"] = "mutated"
    assert default["key"] == "original"
