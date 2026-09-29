"""Send the commands a DR tool runs to a fake host, for one test."""
from unittest import mock


def route_commands(test, runner):
    """For the rest of test, run every command through runner(arguments, timeout).

    commands.run starts programs with subprocess.run, so that is the boundary
    replaced here. Standard input (the SQL replication.sql() sends to psql) is
    passed on as a last argument, so a fake finds a statement at the end of
    the command, where psql's --command used to put it.
    """
    def fake(argv, input=None, timeout=None, **_):
        return runner(list(argv) + ([input.rstrip("\n")] if input is not None else []), timeout)

    patcher = mock.patch("app_installer.commands.subprocess.run", side_effect=fake)
    patcher.start()
    test.addCleanup(patcher.stop)
