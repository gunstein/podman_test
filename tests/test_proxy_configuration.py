from __future__ import annotations

import pathlib
import unittest

from tests.runtime_fixture import RUNTIME

ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(path: pathlib.Path | str) -> str:
    path = path if isinstance(path, pathlib.Path) else ROOT / path
    return path.read_text(encoding="utf-8")


class ProxyConfigurationTests(unittest.TestCase):
    def test_certificate_provisioning_script_binds_to_container(self):
        script = read("proxy/proxy-entrypoint.sh")
        containerfile = read("proxy/Containerfile")

        self.assertIn("/var/lib/todo-tls", script)
        self.assertIn("openssl req", script)
        self.assertIn("tls_hostname=${TODO_TLS_HOSTNAME:-localhost}", script)
        self.assertIn("TODO_TLS_HOSTNAME: $tls_hostname", script)
        self.assertIn("-subj", script)

        self.assertIn("COPY --chmod=0755 proxy/proxy-entrypoint.sh /usr/local/bin/", containerfile)

    def test_security_headers_cover_every_response_and_csp_only_the_apps(self):
        import re

        import yaml
        config = next(doc for doc in yaml.safe_load_all(read(RUNTIME / "shared-proxy.yaml"))
                      if doc["kind"] == "ConfigMap" and doc["metadata"]["name"] == "shared-nginx-config")["data"]
        shared, app = config["security-headers.conf"], config["app-headers.conf"]
        self.assertIn('Strict-Transport-Security "max-age=31536000" always', shared)
        self.assertIn("X-Content-Type-Options nosniff always", shared)
        self.assertIn("include /etc/todo-nginx/security-headers.conf;", app)
        self.assertNotIn("Referrer-Policy", shared)
        self.assertIn("Referrer-Policy strict-origin-when-cross-origin always", app)
        csp = re.search(r'Content-Security-Policy "([^"]+)" always', app).group(1)
        for directive in ("default-src 'self'", "script-src 'self'", "frame-ancestors 'none'",
                          "object-src 'none'"):
            self.assertIn(directive, csp)
        # Notes logs in at Keycloak's canonical origin; nothing else may be reached.
        connect = re.search(r"connect-src ([^;]+);", csp).group(1).split()
        self.assertEqual(connect[0], "'self'")
        self.assertEqual(len(connect), 2)
        self.assertRegex(connect[1], r"^https://[a-z0-9.-]+:\d+$")
        self.assertNotIn("unsafe-inline", csp)
        self.assertNotIn("unsafe-eval", csp)
        servers = config["nginx.conf"].split("server {")[1:]
        self.assertEqual(len(servers), 2)
        for server in servers:
            self.assertIn("include /etc/todo-nginx/security-headers.conf;", server.split("location")[0])
            locations = dict(re.findall(r"location ([^{]+)\{([^}]*)\}", server))
            self.assertEqual(set(locations), {"/auth/ ", "/api/ ", "= /health ", "= /ready ", "/ "})
            for name, body in locations.items():
                # Keycloak sends its own CSP for its login pages.
                self.assertEqual("app-headers.conf" in body, name != "/auth/ ", name)

    def test_proxy_headers_include_oauth2_proxy_standards(self):
        headers = read("proxy/proxy-headers.conf")

        self.assertIn("proxy_set_header Host $http_host;", headers)
        self.assertIn("proxy_set_header X-Real-IP $remote_addr;", headers)
        self.assertIn("proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;", headers)
        self.assertIn("proxy_set_header X-Forwarded-Proto $scheme;", headers)
        self.assertIn("proxy_set_header X-Forwarded-Host $http_host;", headers)
        self.assertIn("proxy_set_header X-Forwarded-Port $server_port;", headers)

    def test_nginx_configuration_reads_from_readonly_system_volume(self):
        nginx = read("deploy/manifests/shared-proxy.yaml.j2")
        app = (RUNTIME / "shared-proxy.yaml").read_text(encoding="utf-8")

        self.assertIn("ssl_certificate /var/lib/todo-tls/server.crt;", nginx)
        self.assertIn("ssl_certificate_key /var/lib/todo-tls/server.key;", nginx)
        self.assertIn("name: nginx-config", app)
        self.assertIn("readOnly: true", app)
        self.assertIn("mountPath: /etc/todo-nginx", app)

    def test_nginx_executes_as_unprivileged_workload(self):
        containerfile = read("proxy/Containerfile")
        app = (RUNTIME / "shared-proxy.yaml").read_text(encoding="utf-8")

        self.assertIn("USER nginx", containerfile)
        self.assertIn("runAsUser: 101", app)
        self.assertIn("runAsGroup: 101", app)
        self.assertIn("allowPrivilegeEscalation: false", app)
        self.assertIn("drop: [ALL]", app)
        self.assertIn('args: [nginx, -c, /etc/todo-nginx/nginx.conf, -g, "daemon off;"]', app)
        
    def test_tls_private_state_uses_dedicated_kube_volume(self):
        app = (RUNTIME / "shared-proxy.yaml").read_text(encoding="utf-8")

        self.assertIn("claimName: todo-nginx-data", app)
        self.assertIn("mountPath: /var/lib/todo-tls", app)

    def test_promoted_proxy_uses_stable_hostname_and_kube_publish(self):
        template = read(
            "deploy/quadlet/"
            "shared-proxy.kube.j2"
        )
        config = (RUNTIME / "config.yaml").read_text(encoding="utf-8")
        proxy_config = (RUNTIME / "shared-proxy.yaml").read_text(encoding="utf-8")

        from app_installer import apps
        self.assertEqual(apps.SHARED_RESOURCE_OWNER.hostname, 'todo.test')
        self.assertIn("'--service-port', '8443'", read("deploy/ops/app_ops/recovery.py"))
        self.assertIn(
            "PublishPort={{ todo_publish_address }}:"
            "{{ todo_service_port }}:8443",
            template,
        )
        self.assertIn('OIDC_ISSUER: "https://todo.test:8443/auth/realms/todo"', config)
        self.assertIn('KC_HOSTNAME: "https://todo.test:8443/auth"',
                      read(RUNTIME / 'keycloak.yaml'))
        self.assertIn("name: shared-nginx-env", proxy_config)


if __name__ == "__main__":
    unittest.main()
