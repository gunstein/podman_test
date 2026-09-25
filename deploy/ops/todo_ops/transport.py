"""Run one command on one host: locally, or over ssh with every argument quoted."""
import shlex
import subprocess

SSH_OPTIONS = ('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15',
               '-o', 'ServerAliveCountMax=4', '-o', 'StrictHostKeyChecking=yes')


class CommandError(RuntimeError):
    """Names the host, command and stderr tail; never stdin, which can hold secrets."""


class Host:
    def __init__(self, spec, sudo_password=None, runner=subprocess.run):
        self.spec, self.sudo_password, self.runner = spec, sudo_password, runner
        self._passwordless = None

    @property
    def name(self):
        return self.spec.name

    def _argv(self, argv):
        argv = [str(argument) for argument in argv]
        if self.spec.local:
            return argv
        return ['ssh', *SSH_OPTIONS, self.spec.destination, shlex.join(argv)]

    def _sudo(self, argv, input):
        if self._passwordless is None:
            probe = self.runner(self._argv(['sudo', '-k', '-n', 'true']), capture_output=True, text=True)
            self._passwordless = probe.returncode == 0
        if self._passwordless:
            return ['sudo', '-n', '--', *argv], input
        if self.sudo_password is None:
            raise CommandError(f'{self.name}: sudo needs a password; run with --ask-become-pass')
        # -k always prompts, so the password line is never left for the command's stdin.
        return ['sudo', '-k', '-S', '-p', '', '--', *argv], self.sudo_password + '\n' + (input or '')

    def run(self, argv, *, sudo=False, input=None, allowed=(0,)):
        if sudo:
            argv, input = self._sudo(argv, input)
        result = self.runner(self._argv(argv), input=input, capture_output=True, text=True)
        if result.returncode not in allowed:
            detail = ' '.join(argv[argv.index('--') + 1:] if sudo else argv)[:120]
            raise CommandError(f'{self.name}: {detail} failed (exit {result.returncode}): '
                               f'{result.stderr.strip()[-400:]}')
        return result
