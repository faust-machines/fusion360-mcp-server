"""Security helpers for the Fusion360MCP add-in.

Stdlib only: no ``adsk`` import and no relative imports, so tests can
load this file by path.
"""

import hmac
import os
import secrets

TOKEN_ENV = "FUSION_MCP_TOKEN"
TOKEN_PATH = os.path.join(os.path.expanduser("~"), ".fusion360mcp", "token")
EXEC_ENV = "FUSION_MCP_ENABLE_EXEC"
DEV_ENV = "FUSION_MCP_DEV"
ALLOWED_DIRS_ENV = "FUSION_MCP_ALLOWED_DIRS"  # os.pathsep-separated
DEFAULT_ALLOWED_DIRS = (
    os.path.join(os.path.expanduser("~"), "Desktop"),
    os.path.join(os.path.expanduser("~"), "Documents"),
)


class SecurityError(RuntimeError):
    """A request violates the add-in's security policy."""


def _env_flag(name: str) -> bool:
    """Truthy rule shared with the client: 1/true/yes, case-insensitive, stripped."""
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def load_or_create_token(path: str = TOKEN_PATH) -> str:
    """Return the shared secret, creating it on first run."""
    env_token = os.environ.get(TOKEN_ENV, "").strip()
    if env_token:
        return env_token
    try:
        with open(path, encoding="utf-8") as f:
            token = f.read().strip()
    except FileNotFoundError:
        token = ""
    if token:
        return token
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, mode=0o700, exist_ok=True)
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(path, 0o600)  # O_CREAT keeps the mode of an existing file
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token)
    return token


def check_token(expected: str, provided) -> bool:
    """Constant-time comparison; False for any non-string *provided*."""
    if not isinstance(provided, str) or not expected:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), provided.encode("utf-8"))


def exec_enabled() -> bool:
    """True when EXEC_ENV opts in to execute_code."""
    return _env_flag(EXEC_ENV)


def dev_enabled() -> bool:
    """True when DEV_ENV opts in to developer commands (reload_handler)."""
    return _env_flag(DEV_ENV)


def allowed_dirs() -> list[str]:
    """Resolved directories that file parameters may point into."""
    raw = os.environ.get(ALLOWED_DIRS_ENV, "")
    if not raw:
        dirs = DEFAULT_ALLOWED_DIRS
    else:
        dirs = [p for p in raw.split(os.pathsep) if p]
    return [os.path.realpath(os.path.expanduser(d)) for d in dirs]


def resolve_allowed_path(path: str) -> str:
    """Return the resolved *path*, or raise SecurityError if outside allowed_dirs()."""
    if not isinstance(path, str) or not path:
        raise SecurityError("path must be a non-empty string")
    resolved = os.path.realpath(os.path.expanduser(path))
    for allowed in allowed_dirs():
        try:
            if os.path.commonpath([resolved, allowed]) == allowed:
                return resolved
        except ValueError:
            # Different drives (Windows): cannot be under this directory.
            continue
    raise SecurityError(
        f"path {path!r} is outside the allowed directories; "
        f"set {ALLOWED_DIRS_ENV} to permit it"
    )
