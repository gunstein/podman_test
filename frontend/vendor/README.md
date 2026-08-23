# Vendored Keycloak adapter

`keycloak.js` is the browser adapter from Keycloak 26.7.1, matching the
Keycloak container version used by this project.

Source: the `keycloak-js` package distributed by the Keycloak project.

SHA-256:

```text
12ea5e286a90308c2cce9768c41a8d0d2724ff00c0800a614fdf28cd4a43d43f  keycloak.js
```

To update it, review and update the Keycloak container pin, copy the matching
adapter from the official distribution, update this checksum, and rerun backend
and E2E tests. Its Apache-2.0 license is retained in `LICENSE.txt`.
