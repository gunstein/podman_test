"""Checked commands, the one way the installer and the DR tools run a program."""
import subprocess

from . import settings

# How much of a failed command's own output its error shows.
ERROR_LINES = 3


class CommandError(RuntimeError):
    """A command failed, ran out of time, or could not start (not installed, no permission)."""


def run(*argv, input=None, allowed=(0,), timeout: float = settings.COMMAND_TIMEOUT, description=None,
        secret_output=False):
    """Run a command, capture its output, and raise CommandError unless its exit code is in allowed.

    The error names the step (description, or the program and its first
    argument) and shows the last lines of what the command printed, so the
    reason is visible without running it again. It never shows the output of
    a command that can print a secret: every `podman secret ...` command, and
    any call that passes secret_output=True (such as SQL that sets a
    password, which psql echoes back when it fails). Every command has a time
    limit, so a hang stops the step with an error instead of waiting forever.
    """
    argv = [str(arg) for arg in argv]
    step = description or " ".join(argv[:2])
    try:
        result = subprocess.run(argv, input=input, text=True, capture_output=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise CommandError(f"{step} timed out after {timeout:g} seconds") from None
    except FileNotFoundError:
        raise CommandError(f"{step} failed: {argv[0]} is not installed or not on PATH") from None
    except OSError as error:
        raise CommandError(f"{step} could not start: {error}") from None
    if result.returncode not in allowed:
        message = f"{step} failed (exit {result.returncode})"
        if not (secret_output or argv[:2] == ["podman", "secret"]):
            lines = (result.stderr or result.stdout or "").strip().splitlines()[-ERROR_LINES:]
            if lines:
                message += ": " + " / ".join(line.strip() for line in lines)
        raise CommandError(message)
    return result


def exists(kind, name):
    """True if the Podman object exists, e.g. exists("secret", "todo-db-password")."""
    return run("podman", kind, "exists", name, allowed=(0, 1)).returncode == 0
