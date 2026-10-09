"""One journald line for each operations command that ran: what, how it ended and how long it took.

Every tool's entry point (`python3 -m app_installer`, `-m app_dr_host`,
`-m app_ops`, app_dr.py and app_backup.py) runs its main() through run().
main() names its command with describe() as soon as it has parsed it, so the
line holds the command and the database it chose, never an argument's value
that could be a secret. `logger --tag TAG` hands the line to journald, which
adds the time and the host:

    journalctl -t app-ops -t app-dr-host -t app-installer -t app-dr -t app-backup

Tests call main() directly, not run(), so they log nothing. app-ops, on the
controller, also keeps everything it printed in one file per run
(run(..., keep=True)), in ~/.local/state/todo/app-ops/.
"""
import io
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

KEPT = Path.home() / '.local/state/todo/app-ops'
_command = []


def describe(*words):
    """Name the command that runs: its subcommand and choices such as the database, nothing secret."""
    _command[:] = [str(word) for word in words if word]


class _Tee:
    """A stream that also keeps what it was given."""

    def __init__(self, stream, kept):
        self.stream, self.kept = stream, kept

    def write(self, text):
        self.kept.write(text)
        return self.stream.write(text)

    def flush(self):
        self.stream.flush()


def run(tag, main, keep=False):
    """Run main() and return its exit code; then log one line about it with tag.

    Only a command main() named with describe() is logged: --help and a
    usage error log nothing. With keep, what it printed on stdout and stderr
    also goes to a new file under KEPT, named by the time it started, and
    the journal line names that file.
    """
    started, start = datetime.now(timezone.utc), time.monotonic()
    code = 'interrupted'
    streams = sys.stdout, sys.stderr
    kept = io.StringIO()
    if keep:
        sys.stdout, sys.stderr = _Tee(sys.stdout, kept), _Tee(sys.stderr, kept)
    try:
        code = main()
        return code
    except SystemExit as error:
        code = error.code if isinstance(error.code, int) else 1
        raise
    finally:
        sys.stdout, sys.stderr = streams
        if _command:
            text = ' '.join([*_command, f'exit={code}', f'seconds={time.monotonic() - start:.1f}'])
            if keep:
                KEPT.mkdir(parents=True, exist_ok=True, mode=0o700)
                path = KEPT / f"{started.strftime('%Y%m%dT%H%M%SZ')}-{_command[0]}.log"
                path.write_text(kept.getvalue() + f'# {text}\n', encoding='utf-8')
                text += f' log={path}'
            if shutil.which('logger'):
                subprocess.run(['logger', '--tag', tag, '--', text], check=False, capture_output=True,
                               timeout=10)
