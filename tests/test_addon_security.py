"""Unit tests for the add-in's security helpers (addon/server/security.py).

The module is loaded by path: the add-in package cannot be imported
outside Fusion. Every test uses tmp_path and monkeypatch, so the result
does not depend on the real HOME or on the developer's environment.
"""

from __future__ import annotations

import importlib.util
import os
import stat
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module_by_path(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sec = _load_module_by_path(
    REPO_ROOT / "addon" / "server" / "security.py", "_addon_security"
)


def _clear_env(monkeypatch):
    for name in (
        sec.TOKEN_ENV,
        sec.EXEC_ENV,
        sec.DEV_ENV,
        sec.ALLOWED_DIRS_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


# --- load_or_create_token -------------------------------------------------


def test_token_env_wins_and_writes_no_file(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv(sec.TOKEN_ENV, "from-env")
    path = tmp_path / ".fusion360mcp" / "token"

    assert sec.load_or_create_token(str(path)) == "from-env"
    assert not path.exists()
    assert not path.parent.exists()


def test_token_read_from_existing_file_stripped(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    path = tmp_path / "token"
    path.write_text("  existing-secret\n")

    assert sec.load_or_create_token(str(path)) == "existing-secret"
    assert path.read_text() == "  existing-secret\n"


def test_empty_token_file_is_regenerated(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    path = tmp_path / "token"
    path.write_text("   \n")

    token = sec.load_or_create_token(str(path))
    assert token
    assert path.read_text() == token


def test_token_created_with_restrictive_modes(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    parent = tmp_path / ".fusion360mcp"
    path = parent / "token"

    token = sec.load_or_create_token(str(path))

    assert parent.is_dir()
    assert stat.S_IMODE(os.stat(parent).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert path.read_text() == token
    # token_urlsafe(32) yields 43 URL-safe characters.
    assert len(token) >= 43


def test_created_token_is_stable_across_calls(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    path = tmp_path / "token"

    first = sec.load_or_create_token(str(path))
    second = sec.load_or_create_token(str(path))

    assert first == second


# --- check_token ----------------------------------------------------------


def test_check_token_accepts_match():
    assert sec.check_token("s3cret", "s3cret") is True


def test_check_token_rejects_mismatch():
    assert sec.check_token("s3cret", "s3cret!") is False
    assert sec.check_token("s3cret", "") is False


@pytest.mark.parametrize("provided", [None, 123, b"s3cret", ["s3cret"]])
def test_check_token_rejects_non_string(provided):
    assert sec.check_token("s3cret", provided) is False


def test_check_token_rejects_empty_expected():
    assert sec.check_token("", "") is False
    assert sec.check_token("", "anything") is False


def test_check_token_compares_utf8_bytes():
    assert sec.check_token("pässwörd", "pässwörd") is True
    assert sec.check_token("pässwörd", "passwörd") is False


# --- exec_enabled / dev_enabled ------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes", "  yes  "])
def test_exec_enabled_truthy(monkeypatch, value):
    _clear_env(monkeypatch)
    monkeypatch.setenv(sec.EXEC_ENV, value)
    assert sec.exec_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "on", "2"])
def test_exec_enabled_falsy(monkeypatch, value):
    _clear_env(monkeypatch)
    monkeypatch.setenv(sec.EXEC_ENV, value)
    assert sec.exec_enabled() is False


def test_exec_enabled_unset(monkeypatch):
    _clear_env(monkeypatch)
    assert sec.exec_enabled() is False


@pytest.mark.parametrize("value", ["1", "yes", "TRUE"])
def test_dev_enabled_truthy(monkeypatch, value):
    _clear_env(monkeypatch)
    monkeypatch.setenv(sec.DEV_ENV, value)
    assert sec.dev_enabled() is True


def test_dev_enabled_falsy_and_unset(monkeypatch):
    _clear_env(monkeypatch)
    assert sec.dev_enabled() is False
    monkeypatch.setenv(sec.DEV_ENV, "off")
    assert sec.dev_enabled() is False


def test_exec_and_dev_flags_are_independent(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv(sec.DEV_ENV, "1")
    assert sec.exec_enabled() is False
    assert sec.dev_enabled() is True


# --- allowed_dirs ---------------------------------------------------------


def test_allowed_dirs_defaults_when_unset(monkeypatch):
    _clear_env(monkeypatch)
    assert sec.allowed_dirs() == [
        os.path.realpath(d) for d in sec.DEFAULT_ALLOWED_DIRS
    ]


def test_allowed_dirs_defaults_when_empty(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv(sec.ALLOWED_DIRS_ENV, "")
    assert sec.allowed_dirs() == [
        os.path.realpath(d) for d in sec.DEFAULT_ALLOWED_DIRS
    ]


def test_allowed_dirs_split_skip_empty_and_resolved(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv(
        sec.ALLOWED_DIRS_ENV, os.pathsep.join([str(a), "", str(b)])
    )

    assert sec.allowed_dirs() == [os.path.realpath(a), os.path.realpath(b)]


def test_allowed_dirs_expands_user(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(sec.ALLOWED_DIRS_ENV, "~/work")

    assert sec.allowed_dirs() == [os.path.realpath(tmp_path / "work")]


# --- resolve_allowed_path -------------------------------------------------


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """allowed/ is the only allowed dir; outside/ is a sibling directory."""
    _clear_env(monkeypatch)
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    monkeypatch.setenv(sec.ALLOWED_DIRS_ENV, str(allowed))
    return allowed, outside


def test_resolve_accepts_dir_itself_and_children(sandbox):
    allowed, _ = sandbox
    assert sec.resolve_allowed_path(str(allowed)) == os.path.realpath(allowed)
    child = allowed / "sub" / "part.f3d"
    assert sec.resolve_allowed_path(str(child)) == os.path.realpath(child)


def test_resolve_rejects_sibling_with_common_prefix(sandbox):
    allowed, _ = sandbox
    sibling = allowed.parent / (allowed.name + "_evil")
    sibling.mkdir()
    with pytest.raises(sec.SecurityError):
        sec.resolve_allowed_path(str(sibling / "x"))


def test_resolve_rejects_parent_traversal(sandbox):
    allowed, outside = sandbox
    target = allowed / ".." / "outside" / "secret.txt"
    with pytest.raises(sec.SecurityError):
        sec.resolve_allowed_path(str(target))


def test_resolve_rejects_symlink_escaping_allowed(sandbox):
    allowed, outside = sandbox
    link = allowed / "link"
    os.symlink(outside, link, target_is_directory=True)
    with pytest.raises(sec.SecurityError):
        sec.resolve_allowed_path(str(link / "secret.txt"))


def test_resolve_rejects_outside_path(sandbox, tmp_path):
    with pytest.raises(sec.SecurityError):
        sec.resolve_allowed_path(str(tmp_path / "elsewhere.txt"))


@pytest.mark.parametrize("bad", ["", None, 42, b"/tmp/x"])
def test_resolve_rejects_empty_or_non_string(sandbox, bad):
    with pytest.raises(sec.SecurityError):
        sec.resolve_allowed_path(bad)


def test_resolve_error_names_env_var(sandbox, tmp_path):
    with pytest.raises(sec.SecurityError) as exc:
        sec.resolve_allowed_path(str(tmp_path / "nope.txt"))
    assert sec.ALLOWED_DIRS_ENV in str(exc.value)


def test_resolve_expands_user(sandbox, monkeypatch, tmp_path):
    allowed, _ = sandbox
    monkeypatch.setenv("HOME", str(allowed.parent))
    resolved = sec.resolve_allowed_path("~/allowed/file.f3d")
    assert resolved == os.path.realpath(allowed / "file.f3d")
