# Oslo is lost: run the service in Trondheim

**You notice it** when users cannot reach the apps, and on Trondheim the DR
check fails: the standby no longer receives WAL. Nothing fails over by
itself: with two sites, Trondheim cannot tell a fire from a broken link.

## 1. Decide, and fence Oslo

One person decides that Oslo is lost. Then make certain the Oslo machine
cannot come back on its own: power it off or cut its network at the
hypervisor or the switch, and turn off any automatic start. An address that
does not answer is **not** proof: a broken link looks the same, and two
primaries lose data. If you cannot fence Oslo for certain, stop here.
Without access to Oslo's hypervisor, [fence-without-oslo.md](fence-without-oslo.md)
says what counts as fenced, and what to do when Oslo comes back.

## 2. Check on Trondheim that promotion is safe

```bash
python3 /opt/platform/bin/app_dr.py preflight --confirm-primary-fenced 'todo-primary is fenced'
```

It refuses if any database is unhealthy, has unreplayed WAL, or if the Oslo
database still answers. Do not work around a refusal.

## 3. Fail over, on Trondheim

Write `~/platform-operations/recovery.yaml`, with Trondheim as `local: true`:

```yaml
user: gunstein
hosts:
  todo-standby: {role: current_primary, address: 192.168.0.108, local: true}
  todo-primary: {role: rebuild_standby, address: 192.168.0.102}
```

Let clients reach HTTPS on Trondheim if the firewall does not already, then
run the one command:

```bash
sudo firewall-cmd --permanent --zone=public --add-port=8443/tcp && sudo firewall-cmd --reload
cd ~/platform-operations && export PYTHONPATH="$PWD/deploy/dr" PYTHONDONTWRITEBYTECODE=1
python3 -m app_ops --inventory recovery.yaml failover \
  --confirm-primary-fenced 'todo-primary is fenced' --confirm-promotion todo-standby
```

(Use a narrower rich rule for 8443 if your site requires it.) It promotes all
three databases, starts the apps, Keycloak and nginx, turns on WAL archiving
and the nightly backup, and checks that each app answers and its login page
works. It prints each step; at the end, its JSON names the hostnames, this
address and the CA fingerprint.

If a step fails, it says which. Fix the cause and run the same command again:
a completed promotion is skipped. If it says the promotion record is
`failed` or `promoting`, **stop**: some databases may be promoted and others
not. Inspect `python3 /opt/platform/bin/app_dr.py status` and
`~/.config/platform/promotion.json` before anything else.

## 4. Send users to Trondheim

Have the DNS owner point the public names at Trondheim's address. If the
JSON says `"client_trust": "unchanged"`, the pair uses certificates from your
CA and clients need nothing new. If it says `"required"` (the demo CA),
clients must trust Trondheim's CA (fingerprint in the JSON; the file is
`~/.config/platform/platform-nginx-root.crt`). Log in to each app in a browser:
`failover` checks the login page, not a real login.

## Afterwards

- The DR check on Trondheim now fails with "no standby streams": correct,
  there is no standby. It passes again after
  [standby-rebuild.md](standby-rebuild.md) makes the old Oslo machine (or a
  new one) the standby. Until then there is no second copy.
- Writes from the last seconds before the loss may be missing (asynchronous
  replication).

**Never** start the old Oslo machine with its network connected: it still
believes it is the primary. Bring it back only as described in
[PROXMOX-QUARANTINE.md](../PROXMOX-QUARANTINE.md) and phase 9 of
ACCEPTANCE.md.
