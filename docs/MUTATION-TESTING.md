# Mutation testing

Line coverage shows which code the tests run, but not whether a test would
notice if that code were wrong. Mutation testing checks that. A tool makes one
small change at a time (a *mutant*): it turns `!=` into `==`, `or` into `and`,
removes an argument or returns `True` instead of `False`. Then it runs the
tests. If a test fails, the mutant is *killed*. If every test still passes, the
mutant *survived*: the tests would not have caught that mistake.

## What is mutated, and why only that

Only the read-only checks in
`deploy/installer/app_installer/replication.py` that guard promotion and the
deletion of database data:

- `reseed_check`, `require_reseed_confirmations`, `require_quarantined_group`
  and `require_stopped_service`, which run before a standby's data is replaced;
- `rebuild_primary_check`, which checks that the current primary can accept a
  rebuilt standby;
- `require_promoted_group`, which checks that the whole group was promoted
  before the application tier or backups start;
- `status`, `require_primary`, `require_standby`, `streaming_status` and
  `archive_health`, which read the database role, replay position and WAL
  archiving;
- `lsn` and `unreplayed_bytes`, which turn two WAL positions into the bytes
  still to replay (never negative);
- `identifier` and `address`, which validate names and addresses.

A mistake in these checks can delete data or promote a database that is behind,
and their tests run in under a second. Orchestration code tested with fake
hosts, Jinja2 templates and shell scripts are left out. There,
surviving mutants mostly show how loose a fake is, not real bugs.

## Running it

The tool is [mutmut](https://github.com/boxed/mutmut), a test-only tool that
is never installed on a target host. Its settings are in
`deploy/installer/pyproject.toml`.

```bash
python3 -m venv /tmp/mutmut-venv
/tmp/mutmut-venv/bin/python -m pip install mutmut==3.8.0 pytest==9.1.1 jinja2 PyYAML
PATH=/tmp/mutmut-venv/bin:$PATH deploy/scripts/run-mutation-tests.sh
```

It takes well under a minute. It prints each surviving mutant as a diff and
the total. mutmut works in `deploy/installer/mutants/`, which Git ignores.

In CI, the **Mutation testing** workflow runs the same script once a week and
on demand (Actions > Mutation testing > Run workflow). It only reports: the
list is in the job summary, and survivors never fail the run.

## Accepted survivors

After the first round and the tests it added
(`deploy/installer/tests/test_replication_checks.py`, and exact command lists
in `test_destructive_gates.py`), 642 of 696 mutants are killed. The 54 that
survive are all in these groups:

| Group | Count | Why it is accepted |
|---|---|---|
| Error message wording | 18 | Tests match a key phrase of each message, not its exact capitalisation or wording. A person still gets a clear message. |
| SQL text | 32 | The tests answer SQL with fixed strings, so a broken query cannot be seen there. SQL keywords are also case-insensitive, so the upper- and lower-case mutants are equivalent. A real PostgreSQL checks these queries: the two-VM acceptance run and the backend CI jobs. |
| `split('=', 1)` variants in `require_stopped_service` | 3 | `systemctl show` values for these four properties never contain `=`, so the variants behave the same. |
| Publish address in `reseed_check`'s template render | 1 | The render only checks that the template renders; its output is discarded. |

## When a new mutant survives

Look at the diff and decide which it is:

1. A mistake the tests should catch: add a test that fails on the mutant,
   and run the script again to see it killed.
2. Harmless or equivalent: add it to the table above, with the reason.

Never change the checked code only to make a mutant die.
