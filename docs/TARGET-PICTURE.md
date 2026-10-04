# Target picture: Oslo and Trondheim after the backlog

One page showing how the solution works once [BACKLOG.md](BACKLOG.md) is done.
Codes in brackets are backlog items. Two machines on separate hardware at
separate sites, and no third machine.

## Every day

```mermaid
flowchart LR
  users(["Users"]) --> dns{{"todo / notes names<br>DNS, short TTL"}}
  op(["Operator<br>runbooks"])

  subgraph OSLO["Oslo: primary"]
    o_proxy["nginx, TLS, shared CA (T4)<br>security headers"]
    o_apps["Todo, Notes<br>Keycloak with lockout"]
    o_db[("PostgreSQL primary<br>todo, notes, keycloak")]
    o_bk[("Own backups: full every night<br>+ WAL archive, 7 days")]
    o_time["Timers: DR check, nightly backup<br>journald logs (L1, L4)"]
  end

  subgraph TRD["Trondheim: standby"]
    t_proxy["nginx, same CA<br>started by failover"]
    t_apps["Todo, Notes, Keycloak<br>started by failover"]
    t_db[("PostgreSQL standby<br>read-only")]
    t_bk[("Own backups from the stream:<br>full every night + WAL (D2)")]
    t_time["Timers: ready to<br>take over (G2)"]
  end

  dns -->|normal| o_proxy
  dns -.->|after failover| t_proxy
  o_proxy ~~~ t_proxy
  o_apps -.- t_apps
  o_db ==>|"WAL stream, TLS"| t_db
  o_bk ~~~ t_bk
  o_time <-->|"journal copy (L6)<br>secrets, CA (G2, T4)"| t_time
  dns ~~~ o_apps & o_db & o_bk
  op -->|app-ops over SSH| o_time
  op -.->|"app-ops failover"| t_time
```

## When Oslo burns: running in Trondheim within 30 minutes

```mermaid
flowchart LR
  a["Oslo lost<br>alert from the<br>DR check"] --> b["Operator decides<br>Oslo is lost and<br>fenced (T3)"]
  b --> c["One command in<br>Trondheim, app-ops failover:<br>promote, apps,<br>backups, login check"]
  c --> d["Names point to<br>Trondheim"]
  d --> e["Users log in,<br>same CA: no<br>certificate errors (T4)"]
```

Failover never starts by itself: with only two sites, Trondheim cannot tell a
fire from a broken link, and promoting on a broken link gives two primaries
(split-brain). So there is one human decision, then one command. The disaster
drill in the Proxmox lab times the whole chain and requires under 30 minutes
(G3, G4). The last seconds of writes before the fire can be lost (asynchronous
replication, C4, T2).

Afterwards: a new Oslo machine becomes Trondheim's standby (G5), and a planned
switchover with no data loss moves operation back (T6).

## What the solution is made of

| Area | After the backlog |
|---|---|
| On the hosts | Only Python's standard library, systemd, journald, Podman and PostgreSQL. No new services. |
| Tools | `install.sh` for one host, app-ops over plain SSH for the pair: `app-ops failover`, updates (U1), switchover (T6). Ansible is retired. |
| Between the sites | Replication encrypted with TLS and verify-full; one CA (T4); DR secrets synchronised (G2). |
| Backups | Each host backs up its own copy: a full backup every night and the WAL archive, 7 days, so PITR works even if one site is lost (D2; the primary's half is done). |
| Security | Keycloak lockout and password policy, HTTP security headers, fapolicyd trusts only root-owned files (F), tool-owned firewall rules (W). |
| Logs | Every tool command in journald with time, host and result (L1, L2), kept across reboots (L4), copied to the other site (L6), one page on where to look (L5). |
| Warnings | Timers turn replication, slot, archive, disk and certificate problems into failed units and optional mail (certificates U2; the rest is done). |
| Proof | Acceptance with content fingerprints for all three databases (C1-C3), plus the timed disaster drill (G3, G4). |
| Size | Less code than today, new features included. |
