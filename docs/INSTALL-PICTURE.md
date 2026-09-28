# Install picture: from source to running pods

How the application gets from source code to running pods, in development and
in production. [TARGET-PICTURE.md](TARGET-PICTURE.md) is the matching page
for failover between two sites.

**The same application, the same YAML templates and the same installer in
both. Only where the YAML and images come from, and how the pods are started
and kept running, differ. The production install needs no internet.**

Wide boxes are the same in both; split boxes show the difference, DEV on the
left and PROD on the right.

```text
          DEV (developer machine)            PROD (build machine -> production host)
   +----------------------------------+   +----------------------------------+
   | dev-up.sh                        |   | build-bundle.sh (build machine)  |
   |                                  |   |  - render YAML, prod values      |
   |                                  |   |  - podman build/pull/save images |
   |                                  |   |  - pack bundle + SHA256SUMS      |
   |                                  |   |             |                    |
   |                                  |   |      copy, no internet           |
   |                                  |   |             v                    |
   |                                  |   | sh install.sh (production host)  |
   +-----------------+----------------+   +-----------------+----------------+
                     |                                      |
                     +------------------+-------------------+
                                        |
                                        v
                     python3 -m app_installer install
                                        |
                     +------------------+-------------------+
                     |                                      |
                     v                                      v
   +----------------------------------+   +----------------------------------+
   | 1. CHECK                         |   | 1. CHECK                         |
   |    Podman: kube play             |   |    SHA256SUMS + preflight.sh     |
   |    --no-pod-prefix               |   |    + Podman: kube play           |
   |                                  |   |    --no-pod-prefix               |
   +-----------------+----------------+   +-----------------+----------------+
                     |                                      |
                     v                                      v
   +----------------------------------+   +----------------------------------+
   | 2. YAML                          |   | 2. YAML                          |
   |    render now, local values      |   |    use the bundle's, as built    |
   +-----------------+----------------+   +-----------------+----------------+
                     |                                      |
                     +------------------+-------------------+
                                        |
                                        v
   +-------------------------------------------------------------------------+
   | 3. PASSWORDS   keep existing Podman secrets, generate missing ones      |
   +------------------------------------+------------------------------------+
                                        |
                     +------------------+-------------------+
                     |                                      |
                     v                                      v
   +----------------------------------+   +----------------------------------+
   | 4. IMAGES (only missing ones)    |   | 4. IMAGES (only missing ones)    |
   |    podman build / pull           |   |    podman load from the bundle   |
   +-----------------+----------------+   +-----------------+----------------+
                     |                                      |
                     v                                      v
   +----------------------------------+   +----------------------------------+
   | 5. CONFIGURATION                 |   | 5. CONFIGURATION                 |
   |    Kube secrets only;            |   |    Kube secrets + YAML + 7       |
   |    no files installed            |   |    Quadlet .kube units;          |
   |                                  |   |    systemctl daemon-reload       |
   +-----------------+----------------+   +-----------------+----------------+
                     |                                      |
                     v                                      v
   +----------------------------------+   +----------------------------------+
   | 6. WHAT RESTARTS                 |   | 6. WHAT RESTARTS                 |
   |    nothing if unchanged and      |   |    stop only the services whose  |
   |    running; else take all down   |   |    file or image changed         |
   +-----------------+----------------+   +-----------------+----------------+
                     |                                      |
                     +------------------+-------------------+
                                        |
                                        v
   +-------------------------------------------------------------------------+
   | 7. START IN THE SAME ORDER                                              |
   |    DEV: podman kube play          PROD: systemctl --user start (Quadlet)|
   |                                                                         |
   |    todo-postgres, notes-postgres  wait healthy, create database roles   |
   |              |                                                          |
   |    keycloak-postgres              wait healthy                          |
   |              |                                                          |
   |    keycloak                                                             |
   |              |                                                          |
   |    todo-app, notes-app            migration, backend, frontend;         |
   |              |                    database roles again                  |
   |    shared-proxy (nginx)           DEV: 127.0.0.1:8443  PROD: IP:8443    |
   +------------------------------------+------------------------------------+
                                        |
                                        v
   +-------------------------------------------------------------------------+
   | 8. CONFIGURE KEYCLOAK   login protection, one client per app            |
   +------------------------------------+------------------------------------+
                                        |
                     +------------------+-------------------+
                     |                                      |
                     v                                      v
   +----------------------------------+   +----------------------------------+
   | 9. dev-down.sh knows what to     |   | 9. verify each service runs from |
   |    stop (saved at start)         |   |    its expected .kube file       |
   +-----------------+----------------+   +-----------------+----------------+
                     |                                      |
                     +------------------+-------------------+
                                        |
                                        v
                          print {"changed": true|false}

   After a reboot:   DEV: nothing starts       PROD: systemd starts every pod
```

## What is the same

Steps 3, 7 and 8 are the same code in the same order: passwords, the seven
pods with their database roles and migrations, and the Keycloak setup. Running
the install again with nothing changed changes nothing.

## What differs

| | Development | Production |
|---|---|---|
| Start with | `dev-up.sh` | `build-bundle.sh`, then `install.sh` on the host |
| YAML | Rendered on the machine, `local` values | Rendered on the build machine, `prod` values |
| Images | Built and pulled on the machine | Loaded from the offline bundle |
| Checks first | Podman only | Checksums, `preflight.sh` and Podman |
| Pods run by | `podman kube play` directly | Quadlet `.kube` units under user systemd |
| On a change | Everything is taken down and started again | Only the changed services restart |
| After a reboot | Nothing starts | systemd starts every pod |

The build machine does not have to be a particular server or CI system. It
needs access to the source code, the container images and the other build
dependencies, over the internet, from internal mirrors or from local storage.

**Prerequisite:** the production host must already be prepared with the host
tools it needs, among them Podman, systemd and Python with Jinja2.

## More detail

- [Offline bundle](../deploy/offline/README.md): building, checking and
  installing the bundle, and the host prerequisites.
- [Python installer](../deploy/installer/README.md): what `install.sh` and
  `dev-up.sh` do, step by step.
- [Architecture](ARCHITECTURE.md): the seven pods and why they are grouped
  as they are.
- [Secrets](SECRETS.md) and [TLS](TLS.md): passwords and certificates.
- [app-ops](../deploy/dr/README.md): the second site, replication, failover
  and backup.
