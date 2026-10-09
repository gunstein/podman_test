# Fence Oslo when you cannot reach its hypervisor

[primary-lost.md](primary-lost.md) step 1 asks you to make certain the Oslo
machine cannot accept writes before Trondheim is promoted. Acceptance does
that through the Proxmox API at Oslo (power off, links down, ports checked).
After a fire or a cut-off site, that API may not answer. This page is how
you know Oslo is fenced without it, and what to do when Oslo comes back.

## Why an unreachable Oslo is not a fenced Oslo

Trondheim sees the same thing in both cases: no replication, no answer on
the database port, no SSH. But a fire stops Oslo; a broken link between the
sites does not. If the link broke and Oslo still reaches its users, and you
promote Trondheim, both sites accept writes: some users write to Oslo,
others to Trondheim, and one side's writes are lost when the pair is joined
again (split-brain). `app_dr.py preflight` checks that Oslo's database does
not answer **from Trondheim**; that is necessary, not sufficient.

Closing the network on the Trondheim side (its firewall, its router) does
not fence Oslo either: it only stops replication, while Oslo can still serve
users who reach it from elsewhere.

## What counts as fenced

Promote only on one of these, and write down which, who, and when. The text
you give `--confirm-primary-fenced` is part of that record.

1. **Oslo is off, confirmed by a person.** Someone at Oslo, or the hosting
   provider, confirms that the machine (or the hypervisor under it) is
   powered off or destroyed, and that it will not start by itself (automatic
   start after power loss off, or the power stays cut).
2. **Oslo is off, confirmed out of band.** You switch it off through a path
   that does not depend on the broken link: the hypervisor or BMC (iLO,
   iDRAC, IPMI) over a separate management network, or a switched PDU, and
   you see its state as off there.
3. **Oslo cannot reach any user, confirmed by whoever owns its network.** The
   Oslo uplink is down or blocked at the site's router or provider, for all
   users, not only towards Trondheim, and stays so until the procedure below.
   Users then cannot write to Oslo even if it runs.

None of these is "it does not answer", "it is probably burnt", or "the
monitoring is red".

## If you cannot get any of them

Do not promote. Users stay without the service until one of the three is
confirmed. That is the price of not losing data: two primaries cost more,
and later, than an hour of downtime. Write down the time you started
waiting; the 30-minute goal (G3) assumes fencing is possible.

If the decision must be taken without proof anyway, it belongs to the owner
of the data, in writing, knowing that writes to Oslo after that moment may
be lost.

## When Oslo comes back

The old Oslo machine still believes it is the primary. It must never join a
network where users or Trondheim can reach it while its stack runs.

1. **Before its network is connected,** at its console (or through a
   management path that does not give it the LAN), stop the whole stack as
   root:

   ```bash
   sudo /opt/todo/bin/app-quarantine.sh stop todo-primary gunstein
   sudo /opt/todo/bin/app-quarantine.sh check todo-primary gunstein
   ```

   (host name and service user as installed). Stopping does not disable the
   units at boot: keep the network away until the rebuild below has run.
   Where Oslo runs on Proxmox and its API answers again, use the quarantine
   profile instead ([PROXMOX-QUARANTINE.md](../PROXMOX-QUARANTINE.md)).
2. **Did Oslo take writes after the promotion?** Only under case 3 above, or
   when the fence was not certain. If it may have, keep a copy before
   anything is erased, still without network, and hand it to whoever owns
   the data to compare by hand:

   ```bash
   systemctl --user start todo-postgres.service notes-postgres.service keycloak-postgres.service
   for db in todo notes keycloak; do
     podman exec "$db-postgres" pg_dump -U "$db" -d "$db" > ~/oslo-after-split-$db.sql
   done
   systemctl --user stop todo-postgres.service notes-postgres.service keycloak-postgres.service
   ```

   (as the service user; the database units only, never the apps or nginx).

   The rebuild in the next step erases Oslo's databases.
3. **Make Oslo the standby of Trondheim** with
   [standby-rebuild.md](standby-rebuild.md): connect only what the rebuild
   needs (SSH and replication from Trondheim), never the users' path, until
   it is done and Oslo streams as a standby.
4. **Move back to Oslo later**, if you want to, only as a planned switchover
   (backlog T6), never by promoting Oslo again from this state.
