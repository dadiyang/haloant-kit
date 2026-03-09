"""Tests for haloant_kit.config module."""
import json

import pytest

import haloant_kit.config as config_mod
from haloant_kit.config import (
    _validate_user_id,
    resolve_user_id,
    resolve_data_dir,
    load_user_config,
    save_user_config,
    get_all_user_ids,
    DATA_DIR,
)


class TestValidateUserId:
    def test_valid_ids(self):
        assert _validate_user_id("irons") == "irons"
        assert _validate_user_id("user-1") == "user-1"
        assert _validate_user_id("a123") == "a123"
        assert _validate_user_id("my_user") == "my_user"

    def test_invalid_start_digit(self):
        with pytest.raises(ValueError, match="Invalid user_id"):
            _validate_user_id("1user")

    def test_invalid_start_uppercase(self):
        with pytest.raises(ValueError, match="Invalid user_id"):
            _validate_user_id("User")

    def test_invalid_empty(self):
        with pytest.raises(ValueError, match="Invalid user_id"):
            _validate_user_id("")

    def test_invalid_special_chars(self):
        with pytest.raises(ValueError, match="Invalid user_id"):
            _validate_user_id("user@name")

    def test_too_long(self):
        with pytest.raises(ValueError, match="Invalid user_id"):
            _validate_user_id("a" * 33)


class TestResolveUserId:
    def test_explicit_user(self):
        assert resolve_user_id("testuser") == "testuser"

    def test_explicit_invalid_raises(self):
        with pytest.raises(ValueError):
            resolve_user_id("123bad")

    def test_from_agent_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config_file = tmp_path / "agent_config.json"
        config_file.write_text(json.dumps({"user_id": "fromconfig"}))
        assert resolve_user_id() == "fromconfig"

    def test_no_source_raises(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(RuntimeError, match="Cannot determine user identity"):
            resolve_user_id()


class TestResolveDataDir:
    def test_default(self, monkeypatch):
        # When no agent_config.json, returns default DATA_DIR
        monkeypatch.chdir("/tmp")
        result = resolve_data_dir()
        assert isinstance(result, type(DATA_DIR))

    def test_from_agent_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        custom_dir = tmp_path / "custom_data"
        config_file = tmp_path / "agent_config.json"
        config_file.write_text(json.dumps({"data_dir": str(custom_dir)}))
        result = resolve_data_dir()
        assert result == custom_dir
        assert custom_dir.exists()


class TestLoadUserConfig:
    def test_existing_user(self, tmp_path, monkeypatch):
        # Set up users.json
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        users_json = config_dir / "users.json"
        users_json.write_text(json.dumps({
            "users": {
                "alice": {"telegram_chat_id": "123", "channels": {"tg": True}}
            }
        }))
        monkeypatch.chdir(tmp_path)
        # Reset cache
        config_mod._users_cache = None

        cfg = load_user_config("alice")
        assert cfg["telegram_chat_id"] == "123"
        assert cfg["channels"]["tg"] is True

        # Cleanup
        config_mod._users_cache = None

    def test_missing_user_returns_empty(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        users_json = config_dir / "users.json"
        users_json.write_text(json.dumps({"users": {}}))
        monkeypatch.chdir(tmp_path)
        config_mod._users_cache = None

        cfg = load_user_config("nobody")
        assert cfg == {}

        config_mod._users_cache = None

    def test_no_users_json_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config_mod._users_cache = None

        cfg = load_user_config("anybody")
        assert cfg == {}

        config_mod._users_cache = None


class TestSaveUserConfig:
    def test_create_and_update(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        users_json = config_dir / "users.json"
        users_json.write_text(json.dumps({"users": {}}))
        monkeypatch.chdir(tmp_path)
        config_mod._users_cache = None

        # Create
        save_user_config("bob", {"key": "value"})
        config_mod._users_cache = None
        cfg = load_user_config("bob")
        assert cfg["key"] == "value"

        # Update (merge)
        save_user_config("bob", {"key2": "v2"})
        config_mod._users_cache = None
        cfg = load_user_config("bob")
        assert cfg["key"] == "value"  # Preserved
        assert cfg["key2"] == "v2"  # Added

        config_mod._users_cache = None

    def test_invalid_user_id_raises(self):
        with pytest.raises(ValueError):
            save_user_config("123Bad", {"x": 1})


class TestGetAllUserIds:
    def test_returns_ids(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        users_json = config_dir / "users.json"
        users_json.write_text(json.dumps({
            "users": {"alice": {}, "bob": {}}
        }))
        monkeypatch.chdir(tmp_path)
        config_mod._users_cache = None

        ids = get_all_user_ids()
        assert sorted(ids) == ["alice", "bob"]

        config_mod._users_cache = None
