import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
from tests.runtime_fixture import RUNTIME


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

    def test_proxy_headers_include_oauth2_proxy_standards(self):
        headers = read("proxy/proxy-headers.conf")

        self.assertIn("proxy_set_header Host $http_host;", headers)
        self.assertIn("proxy_set_header X-Real-IP $remote_addr;", headers)
        self.assertIn("proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;", headers)
        self.assertIn("proxy_set_header X-Forwarded-Proto $scheme;", headers)
        self.assertIn("proxy_set_header X-Forwarded-Host $http_host;", headers)
        self.assertIn("proxy_set_header X-Forwarded-Port $server_port;", headers)

    def test_nginx_configuration_reads_from_readonly_system_volume(self):
        nginx = read("helm/shared-proxy/templates/shared-proxy.yaml")
        app = (RUNTIME / "shared-proxy.yaml").read_text(encoding="utf-8")

        pass # Asserts are now below
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
        ansible = read("ansible/deploy-promoted-application.yml")
        template = read(
            "ansible/roles/shared_proxy_runtime/templates/"
            "shared-proxy.kube.j2"
        )
        config = (RUNTIME / "config.yaml").read_text(encoding="utf-8")
        proxy_config = (RUNTIME / "shared-proxy.yaml").read_text(encoding="utf-8")

        self.assertIn('m14_service_hostname: todo.test', ansible)
        self.assertIn('m14_service_port: 8443', ansible)
        self.assertIn(
            'todo_service_port: "{{ m14_service_port }}"',
            read("ansible/roles/promoted_application/tasks/main.yml"),
        )
        self.assertIn(
            "PublishPort={{ todo_publish_address }}:"
            "{{ todo_service_port }}:8443",
            template,
        )
        self.assertIn('KC_HOSTNAME: "https://todo.test:8443/auth"', config)
        self.assertIn("name: shared-nginx-env", proxy_config)


if __name__ == "__main__":
    unittest.main()
