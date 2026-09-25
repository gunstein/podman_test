"""Checked commands; never include potentially secret output in exceptions."""
import subprocess


def run(*argv, input=None, allowed=(0,)):
    result = subprocess.run([str(arg) for arg in argv], input=input,
                            text=True, capture_output=True, check=False)
    if result.returncode not in allowed:
        raise RuntimeError(f"{argv[0]} {argv[1]} failed (exit {result.returncode})")
    return result


def exists(kind, name):
    return run("podman", kind, "exists", name, allowed=(0, 1)).returncode == 0
