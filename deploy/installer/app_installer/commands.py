"""Checked commands; never include potentially secret output in exceptions."""
import subprocess


def run(*argv, input=None, allowed=(0,)):
    """Run a command, capture its output, and raise unless its exit code is in allowed.

    The error names only the command and its first argument, never stdout or
    stderr: those can hold secret values.
    """
    result = subprocess.run([str(arg) for arg in argv], input=input,
                            text=True, capture_output=True, check=False)
    if result.returncode not in allowed:
        raise RuntimeError(f"{argv[0]} {argv[1]} failed (exit {result.returncode})")
    return result


def exists(kind, name):
    """True if the Podman object exists, e.g. exists("secret", "todo-db-password")."""
    return run("podman", kind, "exists", name, allowed=(0, 1)).returncode == 0
