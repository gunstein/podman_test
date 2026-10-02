# Oracle Linux acceptance with app-ops — 2026-10-02 (run 28)

**Blocked, build host environment:** `aa18b3a3e83480c6f650e612f4178513ee40f9dc`.
Run `2026-10-02-app-ops-28`, a new agent session under `docs/ACCEPTANCE-AGENT.md`
Part C, meant to accept the pre-rendered offline install (`179aeb2`). No
verdict on that code: the run stopped in phase 2, before any install.

Phase 1 passed (both VMs rolled back to `clean-agent`, prerequisites
installed). `02-1-build-offline` (`deploy/offline/build-bundle.sh`) failed in
its first rendering step, `render-kube-runtime.sh`, which the change did not
touch:

    File ".../.local/share/uv/python/cpython-3.9.25-linux-x86_64-gnu/lib/python3.9/runpy.py" ...
    File ".../deploy/installer/app_installer/render.py", line 7, in <module>
        import yaml
    ModuleNotFoundError: No module named 'yaml'
    exit=1

`python3` in the agent's environment for that step was a uv-managed Python
3.9 without PyYAML, not the system `/usr/bin/python3`, which has Jinja2 and
PyYAML. The client had been upgraded to Ubuntu 26.04 after run 27. The same
diagnostics in the agent's environment afterwards found `/usr/bin/python3`
(3.14.4) and no activated environment, so the cause was the state of that
agent session. The agent stopped at once and changed nothing on the client.

Not a product defect. The readiness check now asks the `python3` the build
scripts find on `PATH` for Jinja2 and PyYAML and names it, and guide A4 lists
both as build host prerequisites, so this stops before phase 1.
