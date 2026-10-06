# Acceptance without an agent

One command runs the whole two-VM acceptance: the same steps, in the same
order and with the same checks as an agent run under
[ACCEPTANCE-AGENT.md](ACCEPTANCE-AGENT.md), and the same `REPORT.md`. You
start it, type your sudo password twice, and read the verdict. It takes about
40 minutes; you are needed only at the two sudo prompts.

## Once, before the first run

Part A of [ACCEPTANCE-AGENT.md](ACCEPTANCE-AGENT.md): the Proxmox API token
and its file on this machine, passwordless sudo in the `clean-agent`
snapshots, and the client tools. The run's own readiness check names
anything that is missing.

## Each run

On the client, in the checkout, at the revision to test (CI green on it):

```bash
git switch feature/podman-kube && git pull && git status --short   # must print nothing
python3 deploy/scripts/lab/acceptance.py --run 2026-10-07-run-46 run
```

Use a new run ID each time: the date and a number. You do not roll the VMs
back yourself: the first steps do (`01-3`, `01-4`), and the readiness check
runs before them.

What you see: one line per step, `STEP 03-5: PASS`. Long steps (the build,
bootstrap, failover, rebuild, reseed) print `STARTED in the background` and
then `waiting for ...` until they end.

**Twice, it asks for your sudo password**, before `03-4a` and before `07-5`:
the client must reach `todo.test` and `notes.test` at the serving VM (`.102`,
then `.108` after the failover) and trust that VM's CA. It runs the lines of
C9.4 in the agent guide, then checks that both names answer over trusted
HTTPS. Type the password and wait. The time to the second prompt counts in
the failover time, so answer it when it comes.

At the end it prints the verdict (`ALL STEPS PASS` or `NOT CLEAN`) and the
failover time, and writes `REPORT.md` and `EVIDENCE.md` in
`~/todo-acceptance-runs/<run ID>/`. Send `EVIDENCE.md` for review; the
verdict is recorded in `docs/history/` from it.

## When it stops

It stops at the first step that does not pass, prints that step's output and
`STOP at <step>`, and runs nothing more. You do not judge whether it was "good
enough": a stop is a stop.

- Send `EVIDENCE.md` (`python3 deploy/scripts/lab/acceptance.py --run <run ID> evidence`)
  for a diagnosis. Do not repair the VMs by hand.
- If the stop was outside the product (the network, the Proxmox host, a typo
  in the sudo password) and nothing was changed, the same command goes on:
  a step that ran is never run again, and a failed step stays failed.
- To start over, use a new run ID; the first steps roll both VMs back.

If you press Ctrl-C, run the same command again: it goes on where it was, and
a background step goes on by itself meanwhile.

The testuser password lives in `$XDG_RUNTIME_DIR/todo-acceptance/e2e-password`
(tmpfs, 0600, never printed). The run makes it and removes it at the end; after
a stop it stays for the run that goes on. Remove it with
`rm -f "$XDG_RUNTIME_DIR/todo-acceptance/e2e-password"` when you give up a run.

## What it does not do

Every change gets this full run: the one-VM quick acceptance is retired,
since the full run needs no agent and takes about 35 minutes.

It does not record the verdict in the repository, and it does not decide
anything a person must decide: a stop is reported, never worked around. The
agent guide stays for runs by an agent; both run the same steps.
