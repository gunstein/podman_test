# New-agent acceptance handoff

Paste the prompt below into the agent with this repository open. It applies to
Antigravity or another coding agent; it assumes no particular editor integration.
This document is a source-repository onboarding aid, not another runbook.

## Prompt

```text
Help me conduct a NEW full two-VM acceptance test. Start with a read-only
assessment and plan. Do not reset, stop, deploy, promote or reseed anything yet.
Earlier approvals in another conversation do not authorize this run.

Read AGENTS.md, then:
1. docs/ARCHITECTURE.md for system boundaries.
2. docs/MANUAL-DR-QUICKSTART.md as the canonical operator index.
3. docs/LAB-ACCEPTANCE.md for all 11 phase cards and pass/stop criteria.
4. docs/ACCEPTANCE-688a0f6.md as historical evidence, NOT current machine state.
Read linked security and operational runbooks before their relevant phase.

Use the direct playbook/tool route in LAB-ACCEPTANCE.md. Do not mix it with
the runner route for destructive phases. The full reset controller is not
required: the operator uses the Proxmox node Shell, not the guest console.

First verify repository branch, clean commit and CI status for that exact
revision. Check available terminal, SSH, build and Playwright/Chromium tools.
Report missing tools or permissions; do not silently weaken the test.
Explicitly read AGENTS.md even if your agent does not auto-load that filename.

Ask the operator for fresh Proxmox identity/snapshot/NIC/firewall/HA evidence.
Verify guest identities, current database roles and observed client source IP.
Treat lab-dr.example.toml as an example, not an executable inventory of reality.
Do not overwrite an existing local topology file without inspecting it.

Use one clean revision for both packages and verify checksums and VERSION.
Keep a private phase record outside the checkout, without secrets. Update it
after every phase so another agent can resume without this conversation.
Do not edit source during acceptance; if a repair is necessary, stop and record
the deviation. A changed/repaired run cannot be declared unchanged-revision PASS.

Run safe checks yourself where access permits. Label each operator command
with its location: client/build host, guest via SSH, or Proxmox node Shell.
The operator enters sudo passwords locally; never request passwords in chat.
Use ssh -t and --ask-become-pass where required, especially rebuild.
Require separate explicit approvals for snapshot reset, security opt-ins,
fencing/promotion, destructive reseed and disposable restore cleanup.
Never batch across these gates or retry a partially failed destructive phase.
For Guest Agent commands, verify JSON exited/exitcode and STOPPED; a PID alone
requires exec-status polling. Never infer success from the outer qm exit code.

Require real authenticated Chromium tests with TLS verification, persistent
markers, backup/PITR evidence and sequential reboots. No skipped login tests.
Use bounded readiness waits; preserve transient failures in the phase record.
Keep old primary fenced after promotion and retain rebuild quarantine rules.
Do not reset, retire files, commit or push automatically after the final verdict.

Your first response should summarize the plan, read-only findings, missing
information and the exact next approval needed. Do not change the VMs yet.
```

## Lab reference — verify before use

The previous run used VM107 / todo-primary / 192.168.0.102 and VM108 /
todo-standby / 192.168.0.108. Actual snapshot names were `clean-ol9-primary`
and `clean-ol9-standby`, unlike the illustrative TOML defaults. The Proxmox
node was `proxmox`; the observed client source was 192.168.0.100 (NAT may apply).
The run ended with VM108 as writable primary and VM107 as quarantined standby.
None of these historical observations proves current state or authorizes reset.
