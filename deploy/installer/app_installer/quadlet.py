"""Write Quadlet units and their files atomically; render the unit templates (build host only).

render() needs Jinja2 and imports it when it is called, so write() and
systemctl() also serve an offline host and the DR hosts without it: they
install units the build host rendered (target_render).
"""
import os
import stat
import tempfile
from pathlib import Path

from .commands import run


def render(project_root, name, variables):
    """Render deploy/quadlet/<name>.j2 with the Jinja2 whitespace settings it was written for.

    Returns the bytes; undefined variables are an error.
    """
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
    environment = Environment(
        loader=FileSystemLoader(Path(project_root) / "deploy/quadlet"),
        undefined=StrictUndefined, trim_blocks=True, keep_trailing_newline=True,
        autoescape=False,
    )
    return environment.get_template(name + ".j2").render(**variables).encode()


def write(path, content, mode):
    """Compare content and permissions, then atomically replace changed files."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"Refusing symlink destination: {path}")
    if path.exists() and path.read_bytes() == content:
        if stat.S_IMODE(path.stat().st_mode) == mode:
            return False
        path.chmod(mode)
        return True
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".app-installer-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def systemctl(*args):
    """Run systemctl --user with the given arguments."""
    return run("systemctl", "--user", *args)
