# Identity: what the platform asks of a login provider

The platform runs one Keycloak for every app that has login. This page says
what the apps and the platform need from it, which part of that is plain
OpenID Connect (OIDC) and which is Keycloak's own administration, and where
each piece is in the code. It is the page to read before changing anything
about login, and the list to work through if Keycloak is ever replaced
(for example by Duende IdentityServer, or by an identity service outside the
platform). There is no provider layer: a switch changes the places named
here.

Today's limits are stated as limits, with the phase that changes them
([plan](PLATFORM-PLAN.md), section 7): all apps share one realm, `todo`
(phase 6).

An app asks for login in its `app.yaml`:

```yaml
login:
  client: todo-frontend   # the OAuth client its frontend logs in with
  audience: todo-api      # the audience its backend accepts in an access token
```

The two must differ, and no app's audience may be another app's client: an
ID token names its client in `aud`, so an audience equal to a client would
let an ID token pass as an access token (the loader and `apps.Platform`
refuse it).

## 1. What a login provider must deliver

| What | Today (Keycloak) | Plain OIDC? |
|---|---|---|
| An issuer: one HTTPS URL that every token names in `iss` | `https://<identity hostname>:<port>/auth/realms/todo` (`keycloak.issuer`) | Yes |
| A discovery document at `<issuer>/.well-known/openid-configuration` | `keycloak.DISCOVERY`, relative to an origin | Yes |
| Signing keys (JWKS) the backends can fetch inside the app network | `keycloak.JWKS_URL`, `http://keycloak:8080/...` | Yes; the address is the platform's choice |
| Access tokens: JWT signed RS256 with `iss`, `aud` (the app's API audience), `exp` and `sub` | Keycloak's access tokens; the API audience from one audience mapper per client, in access tokens only | Yes, but how `aud` is filled is the provider's configuration |
| One public client per app: authorization code with PKCE (S256) only, the app's origin as redirect URI and web origin | Made from the platform's defaults and corrected by `keycloak.configure` | The flow is OIDC; creating the client is Keycloak administration |
| The login pages under `/auth/` on every app's hostname and its own | nginx sends `/auth/` to Keycloak (`deploy/manifests/shared-proxy.yaml.j2`); Keycloak serves under `/auth` (`KC_HTTP_RELATIVE_PATH`) | The path is the platform's choice |
| Readiness: when the provider can serve | Its discovery document answers (`keycloak.wait(keycloak.DISCOVERY, ...)`) | Yes |
| Its state in the DR group, so a failover keeps users and keys | Keycloak's own PostgreSQL, `keycloak-postgres`, in `Platform.replicated_databases` | No: each provider stores its state its own way |

## 2. The token contract of a backend

Each backend (`todo-backend/main.py`, `notes-backend/main.py`,
`validate_access_token`) checks every write request's bearer token itself;
nginx only routes. It requires:

- a signature by a key from `OIDC_JWKS_URL`, algorithm RS256 only;
- `iss` exactly `OIDC_ISSUER`;
- `aud` containing `OIDC_AUDIENCE`;
- `exp` in the future, and `sub` present.

The three values come from the app's ConfigMap
(`deploy/manifests/app-config.yaml.j2`), which the platform renders from
`keycloak.issuer`, `keycloak.JWKS_URL` and the app's login client.

`OIDC_AUDIENCE` is the app's `login.audience` (`todo-api`), never its
client ID: Keycloak's ID token for the frontend names the client
(`todo-frontend`) in `aud`, so the backend refuses it. A backend without
`OIDC_AUDIENCE` accepts nothing. The tests: each backend's
`unit_tests` (no database), and the browser test `e2e/test_multi_app.py`,
which takes the real ID tokens from Keycloak's token endpoint and sends
them to the API.

Access model: reading is public; any user with a valid token for the app may
create, change and delete. No row records who wrote it, so the apps' data
does not depend on a user's `sub`. An app that records ownership must store
`sub` (with the issuer), never a username or e-mail, and a provider switch
then needs a mapping from old to new identities.

## 3. Who uses which address

| Who | Asks | Through |
|---|---|---|
| The browser | the discovery document, then the login page and the token | first its own app's origin (`/auth/...`), then the identity hostname, which every app logs in at so they share one login cookie |
| A backend | the signing keys | `keycloak.JWKS_URL`, inside the app network; it never does discovery |
| The installer, DR and backup | the discovery document and, for Keycloak, its admin API | the local nginx on `127.0.0.1` with the identity hostname as Host (`keycloak.request`) |
| Failover's login check | the authorization endpoint named in discovery | the promoted host's HTTPS, as a browser would |

Keycloak is configured after nginx starts (`install.finish`), because the
installer reaches it through nginx. Moving that wait before nginx would
block the install.

## 4. Plain OIDC and Keycloak administration

Plain OIDC, which another provider also offers: the issuer, discovery, the
JWKS, the authorization code flow with PKCE, access tokens and their
claims, and failover's check that the authorization endpoint accepts each
app's redirect.

Keycloak's own, which another provider replaces with its own mechanism:

- `deploy/installer/app_installer/keycloak.py`: the admin token from the
  `master` realm with `admin-cli`, the realm's admin API
  (`ADMIN_TOKEN`, `ADMIN_REALM`), the login protection (`REALM_SECURITY`,
  `secure_realm`), and the clients (`configure`): a missing client is made
  from the platform's defaults (`new_client`); an existing one gets the
  fields the platform owns corrected (`owned_client_fields`, PKCE S256:
  public, code flow only, the app's redirect URI and web origin) and keeps
  every other field, and its audience mappers are made exactly one, the
  app's API audience in access tokens only (`audience_mapper`), because
  another audience could let one app's token pass another app's backend;
- `keycloak/Containerfile` and `keycloak/todo-realm.json`: the image and the
  realm import, which holds the realm's settings and no client;
- `deploy/manifests/keycloak.yaml.j2` and `deploy/quadlet/keycloak.kube.j2`:
  the pod (`KC_*` settings, health check) and its unit;
- `apps.py`: `KEYCLOAK_IMAGE`, `KEYCLOAK_DATABASE`, `KEYCLOAK_WORKLOAD`,
  the admin secrets; `secrets.py` makes them;
- failover's `login_page` looks for Keycloak's login form
  (`id="username"`): the page, not the endpoint, is Keycloak's;
- the frontends' `keycloak-adapter.js` and `vendor/keycloak.js`;
- the lab and e2e helpers that make the test user through the admin API
  (`e2e/provision_user.py`, `deploy/scripts/lab/provision-user.sh`).

## 5. What `has_login` and `has_identity` mean

`App.has_login`: its `app.yaml` names a login client, so its users log in.
`Platform.has_identity`: some app has login, so **the platform runs its own
Keycloak** with its own database in the DR group, its image, its secrets
and its place in the start order. The model cannot yet say "this app logs
in at an identity service outside the platform": that is a field and a few
plain conditions to add when it is needed, not before.

## 6. The frontend adapter

Each frontend imports `auth.js`, which re-exports one adapter with six
functions: `init`, `isAuthenticated`, `login`, `logout`, `getAccessToken`
and `getUsername`. The app uses nothing else. The Keycloak adapter:

- **settings**: `init` first reads `/config/login.json`: where discovery
  is, the realm and the app's client ID. The platform makes it from the
  app's `login.client` (`keycloak.frontend_login`), puts it in the app's
  ConfigMap as the one key `FRONTEND_LOGIN`, and the pod template mounts
  that key alone into the frontend (the database settings beside it are
  not served; the frontend's nginx answers `/config/` with a file or 404).
  The adapter's code names no realm, client or path of its own;
- **start** (`init`): reads the issuer from discovery through the app's own
  origin, then lets keycloak.js check, without showing a login page,
  whether the user is logged in (`check-sso`, PKCE S256, no session
  iframe);
- **callback**: keycloak.js reads the authorization code from the URL when
  the browser comes back, during `init`;
- **expired session or failed refresh**: `getAccessToken` refreshes a token
  that expires within 30 seconds; if that fails, the error reaches the app,
  which shows it (`showError`), and no stale token is used;
- **logout**: Keycloak ends the session and sends the browser back to the
  app's origin.

A new app's frontend copies the adapter and the two pod-template lines
that mount `FRONTEND_LOGIN`; its client ID is only in its `app.yaml`.

## 7. If the provider is replaced

Decide first: the provider runs in the platform (like Keycloak today) or
outside it. Then, in this order, with a test at each step:

1. The client and token contract: one public client per app (code flow,
   PKCE S256, exact redirect and origin), each app's API audience
   (`login.audience`), the claims in section 2. With Duende IdentityServer,
   `aud` comes from an API resource named like the audience; API scopes
   alone give no `aud`.
2. One app against a test instance: a new adapter behind `auth.js`, the
   issuer, JWKS and audience in its ConfigMap; a real access token accepted,
   an ID token and another app's token refused.
3. The platform: the addresses in `keycloak.py` (section 1), the client
   registration in place of `keycloak.configure`, and for a provider in the
   platform its image, pod, unit, secrets and readiness.
4. State and DR. For a provider in the platform, list where each of these
   lives and how it comes back after a restore and a failover: users,
   clients, grants and sessions, signing keys, and any key that encrypts
   them (for Duende, the ASP.NET Data Protection keys). Keep them in the
   replicated PostgreSQL where possible.
5. Existing users: passwords, second factors and sessions cannot be assumed
   to move; plan new logins or password resets, and old tokens ending.
   Install refuses a platform that drops Keycloak's workloads
   (`install.require_installed_workloads`): a switch on an installed host
   is a planned transition, not a deleted record.

## 8. Tests that guard login

- `todo-backend/unit_tests/test_auth.py`, `notes-backend/unit_tests/test_notes_auth.py`:
  the token checks without a database (wrong issuer, audience, expiry,
  signature, algorithm, a missing claim, another app's token, an ID token,
  no configured audience).
- `e2e/test_auth_adapter.py`: the adapter contract against a fake SDK.
- `e2e/test_todo_flow.py`, `e2e/test_notes_flow.py`, `e2e/test_multi_app.py`:
  real browser login, SSO between the apps, each access token's API
  audience, a Todo token refused by Notes and the real ID tokens refused.
- `deploy/installer/tests/test_install.py`, `test_keycloak_http.py`:
  a client made from the defaults, an existing one corrected (owned fields,
  PKCE, audience mappers) keeping the rest, one already right left alone,
  and the admin API calls.
- `tests/test_app_ops_failover.py`, `deploy/dr/tests/test_promoted.py`:
  failover's login check and the promoted host's issuer.
- The lab: `check browser` before and after a failover.
