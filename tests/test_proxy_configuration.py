from __future__ import annotations

import pathlib
import sys
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

        self.assertIn("/var/lib/platform-tls", script)
        self.assertIn("openssl req", script)
        self.assertIn("tls_hostname=${PLATFORM_TLS_HOSTNAME:-localhost}", script)
        self.assertIn("PLATFORM_TLS_HOSTNAME: $tls_hostname", script)
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
        self.assertIn("include /etc/platform-nginx/security-headers.conf;", app)
        self.assertNotIn("Referrer-Policy", shared)
        self.assertIn("Referrer-Policy strict-origin-when-cross-origin always", app)
        csp = re.search(r'Content-Security-Policy "([^"]+)" always', app).group(1)
        for directive in ("default-src 'self'", "script-src 'self'", "frame-ancestors 'none'",
                          "object-src 'none'"):
            self.assertIn(directive, csp)
        # Every app logs in at Keycloak's own origin; nothing else may be reached.
        connect = re.search(r"connect-src ([^;]+);", csp).group(1).split()
        self.assertEqual(connect[0], "'self'")
        self.assertEqual(len(connect), 2)
        self.assertRegex(connect[1], r"^https://[a-z0-9.-]+:\d+$")
        self.assertNotIn("unsafe-inline", csp)
        self.assertNotIn("unsafe-eval", csp)
        identity, *servers = config["nginx.conf"].split("server {")[1:]
        self.assertEqual(len(servers), 3)
        # Keycloak's own server: shared headers, /auth/ to Keycloak, nothing else.
        self.assertIn("include /etc/platform-nginx/security-headers.conf;", identity.split("location")[0])
        self.assertEqual(set(dict(re.findall(r"location ([^{]+)\{([^}]*)\}", identity))), {"/auth/ ", "/ "})
        self.assertNotIn("app-headers.conf", identity)
        # todo and notes log in at Keycloak; Help is static and has no login.
        expected = {"todo.test": {"/auth/ ", "/api/ ", "= /health ", "= /ready ", "/ "},
                    "notes.test": {"/auth/ ", "/api/ ", "= /health ", "= /ready ", "/ "},
                    "help.test": {"/ "}}
        self.assertEqual([re.search(r"server_name ([^;]+);", server).group(1) for server in servers], list(expected))
        for server in servers:
            self.assertIn("include /etc/platform-nginx/security-headers.conf;", server.split("location")[0])
            locations = dict(re.findall(r"location ([^{]+)\{([^}]*)\}", server))
            self.assertEqual(set(locations), expected[re.search(r"server_name ([^;]+);", server).group(1)])
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

        self.assertIn("ssl_certificate /var/lib/platform-tls/server.crt;", nginx)
        self.assertIn("ssl_certificate_key /var/lib/platform-tls/server.key;", nginx)
        self.assertIn("name: nginx-config", app)
        self.assertIn("readOnly: true", app)
        self.assertIn("mountPath: /etc/platform-nginx", app)

    def test_nginx_executes_as_unprivileged_workload(self):
        containerfile = read("proxy/Containerfile")
        app = (RUNTIME / "shared-proxy.yaml").read_text(encoding="utf-8")

        self.assertIn("USER nginx", containerfile)
        self.assertIn("runAsUser: 101", app)
        self.assertIn("runAsGroup: 101", app)
        self.assertIn("allowPrivilegeEscalation: false", app)
        self.assertIn("drop: [ALL]", app)
        self.assertIn('args: [nginx, -c, /etc/platform-nginx/nginx.conf, -g, "daemon off;"]', app)
        
    def test_nginx_serves_its_tls_files_read_only_from_a_kube_secret(self):
        import yaml
        from app_installer import apps, tls_secrets
        docs = list(yaml.safe_load_all((RUNTIME / "shared-proxy.yaml").read_text(encoding="utf-8")))
        self.assertEqual([doc["kind"] for doc in docs], ["ConfigMap", "ConfigMap", "Pod"])
        pod = next(doc for doc in docs if doc["kind"] == "Pod")
        # Nothing in the pod writes TLS files: no init container, nginx only serves.
        self.assertNotIn("initContainers", pod["spec"])
        nginx = pod["spec"]["containers"][0]
        self.assertEqual(nginx["env"], [{"name": "PLATFORM_TLS_ROLE", "value": "serve"}])
        mount = next(m for m in nginx["volumeMounts"] if m["name"] == "tls-data")
        self.assertEqual(mount, {"name": "tls-data", "mountPath": "/var/lib/platform-tls", "readOnly": True})
        volume = next(v for v in pod["spec"]["volumes"] if v["name"] == "tls-data")
        self.assertEqual(volume, {"name": "tls-data", "secret": {
            "secretName": apps.PROXY_KUBE_TLS_SECRET, "optional": False, "defaultMode": 0o444}})
        # The Kube secret's keys are the files nginx's entrypoint checks; no CA or waiting key.
        self.assertEqual(tls_secrets.SERVED, ("tls-mode", "ca.crt", "server.crt", "server.key"))

    def test_going_back_to_the_tls_volume_gives_the_volume_manifest_again(self):
        import subprocess

        import yaml
        sys.path.insert(0, str(ROOT / "deploy/installer/tests"))
        from volume_mode import volume_manifest
        template = read("deploy/manifests/shared-proxy.yaml.j2")
        # The volume's three parts are kept, commented out with "#~ ".
        for line in ("#~ kind: PersistentVolumeClaim", "#~   initContainers:",
                     "#~         claimName: platform-nginx-data"):
            self.assertIn(line, template)
        # Following the steps at the top gives the template before the secrets, apart from its comments,
        # in what the TLS storage decides: the volume claim (first) and the pod (last). The ConfigMaps
        # between them, nginx.conf among them, have changed since for other reasons.
        before = subprocess.run(["git", "show", "8d0e699:deploy/manifests/shared-proxy.yaml.j2"], cwd=ROOT,
                                capture_output=True, text=True, check=False)
        if before.returncode == 0:
            def storage_parts(text):
                documents = text.split("\n---\n")
                return [[line for line in document.splitlines() if not line.lstrip().startswith("#")]
                        for document in (documents[0], documents[-1])]
            # The shared names have carried the platform- prefix since (docs/PLATFORM-PLAN.md, phase 1).
            renamed = before.stdout
            for old, new in (("todo-nginx", "platform-nginx"), ("todo-tls", "platform-tls"),
                             ("todo-proxy", "platform-proxy"), ("TODO_TLS_", "PLATFORM_TLS_")):
                renamed = renamed.replace(old, new)
            self.assertEqual(storage_parts(volume_manifest(template)), storage_parts(renamed))
        docs = list(yaml.safe_load_all(volume_manifest((RUNTIME / "shared-proxy.yaml").read_text())))
        self.assertEqual([doc["kind"] for doc in docs], ["PersistentVolumeClaim", "ConfigMap", "ConfigMap", "Pod"])
        self.assertEqual(docs[-1]["spec"]["initContainers"][0]["name"], "nginx-tls")

    def test_promoted_proxy_uses_stable_hostname_and_kube_publish(self):
        template = read(
            "deploy/quadlet/"
            "shared-proxy.kube.j2"
        )
        config = (RUNTIME / "todo-config.yaml").read_text(encoding="utf-8")
        proxy_config = (RUNTIME / "shared-proxy.yaml").read_text(encoding="utf-8")

        from app_installer import platform_file
        self.assertEqual(platform_file.checkout().identity_hostname, 'auth.test')
        # The promoted host publishes the port the bundle was built for (platform.yaml's publicPort).
        self.assertIn("'--service-port', str(steps.public_port(project_root))", read("deploy/dr/app_ops/recovery.py"))
        self.assertIn(
            "PublishPort={{ publish_address }}:"
            "{{ service_port }}:8443",
            template,
        )
        self.assertIn('OIDC_ISSUER: "https://auth.test:8443/auth/realms/todo"', config)
        self.assertIn('KC_HOSTNAME: "https://auth.test:8443/auth"',
                      read(RUNTIME / 'keycloak.yaml'))
        self.assertIn("name: shared-nginx-env", proxy_config)


if __name__ == "__main__":
    unittest.main()
