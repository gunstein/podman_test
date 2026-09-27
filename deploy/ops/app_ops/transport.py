"""Run one command on one host: locally, or over ssh with every argument quoted."""
import shlex
import subprocess

# A plain command on a host; app_installer steps pass their own, longer limit.
COMMAND_TIMEOUT = 600

SSH_OPTIONS = ('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15',
               '-o', 'ServerAliveCountMax=4', '-o', 'StrictHostKeyChecking=yes')


class CommandError(RuntimeError):
    """Names the host, command and stderr tail; never stdin, which can hold secrets."""


class Host:
    """Sudo is non-interactive only (sudo -n): no password is ever read, sent or stored."""

    def __init__(self, spec, runner=subprocess.run):
        self.spec, self.runner = spec, runner

    @property
    def name(self):
        """The inventory name of this host."""
        return self.spec.name

    def _argv(self, argv):
        """argv as it runs locally, or wrapped in one quoted ssh command for a remote host."""
        argv = [str(argument) for argument in argv]
        if self.spec.local:
            return argv
        return ['ssh', *SSH_OPTIONS, self.spec.destination, shlex.join(argv)]

    def run(self, argv, *, sudo=False, input=None, allowed=(0,), timeout=COMMAND_TIMEOUT):
        """Run argv on this host and return the result; raise CommandError unless the exit code is allowed.

        input goes to stdin; secrets must only travel that way. With sudo=True
        the command runs as root through sudo -n, which fails at once instead of
        asking for a password. A command still running after timeout seconds is
        stopped and raises CommandError naming it, so a hang never stops an
        operation silently.
        """
        command = ['sudo', '-n', '--', *argv] if sudo else list(argv)
        detail = ' '.join(str(argument) for argument in argv)[:120]
        try:
            result = self.runner(self._argv(command), input=input, capture_output=True, text=True,
                                 timeout=timeout)
        except subprocess.TimeoutExpired:
            raise CommandError(f'{self.name}: {detail} timed out after {timeout:g} seconds') from None
        if result.returncode not in allowed:
            if sudo and 'password is required' in result.stderr:
                raise CommandError(f'{self.name}: sudo requires a password; app-ops needs passwordless '
                                   f'sudo (NOPASSWD) for {self.spec.user or "this user"}')
            raise CommandError(f'{self.name}: {detail} failed (exit {result.returncode}): '
                               f'{result.stderr.strip()[-400:]}')
        return result
