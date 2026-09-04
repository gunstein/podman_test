import pathlib
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


class ProxyConfigurationTests(unittest.TestCase):
    def test_frontend_uses_pinned_nginx_image_as_non_root(self):
        containerfile = read("frontend/Containerfile")

        self.assertIn("nginx:1.30.4-alpine", containerfile)
        self.assertIn("USER nginx", containerfile)
        self.assertIn('LABEL io.todo.proxy="nginx"', containerfile)
        self.assertNotIn("caddy:", containerfile.lower())

    def test_nginx_routes_and_forwarded_headers_are_explicit(self):
        configuration = read("frontend/nginx.conf")
        headers = read("frontend/todo-proxy-headers.conf")

        for route in ("/auth/", "/api/", "/health", "/ready"):
            self.assertIn(route, configuration)
        for header in (
            "Host",
            "X-Forwarded-Host",
            "X-Forwarded-Proto",
            "X-Forwarded-Port",
            "X-Forwarded-For",
        ):
            self.assertIn(f"proxy_set_header {header} ", headers)

    def test_tls_private_state_uses_dedicated_kube_volume(self):
        app = read("kube/runtime/app.yaml")
        config = read("kube/runtime/config.yaml")

        self.assertIn("claimName: todo-nginx-data", app)
        self.assertIn("mountPath: /var/lib/todo-tls", app)
        self.assertIn('TODO_TLS_HOSTNAME: "todo.test"', config)

    def test_promoted_proxy_uses_stable_hostname_and_kube_publish(self):
        template = read(
            "ansible/roles/application_kube_runtime/templates/"
            "todo-app.kube.j2"
        )
        config = read("kube/runtime/config.yaml")

        self.assertIn(
            "PublishPort={{ todo_publish_address }}:"
            "{{ todo_service_port }}:8443",
            template,
        )
        self.assertIn("server_name todo.test;", config)
        self.assertIn('KC_HOSTNAME: "https://todo.test:8443/auth"', config)


if __name__ == "__main__":
    unittest.main()
