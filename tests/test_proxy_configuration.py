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

    def test_tls_private_state_uses_dedicated_volume(self):
        quadlet = read("quadlet/todo-frontend.container")

        self.assertIn(
            "todo-nginx-data.volume:/var/lib/todo-tls:U",
            quadlet,
        )
        self.assertIn("Environment=TODO_TLS_HOSTNAME=localhost", quadlet)

    def test_promoted_proxy_uses_stable_hostname(self):
        template = read(
            "ansible/roles/promoted_application/templates/"
            "todo-frontend.container.j2"
        )
        nginx = read(
            "ansible/roles/promoted_application/templates/nginx.conf.j2"
        )

        self.assertIn(
            "Environment=TODO_TLS_HOSTNAME={{ m14_service_hostname }}",
            template,
        )
        self.assertIn("server_name {{ m14_service_hostname }};", nginx)
        self.assertIn("listen {{ m14_service_port }} ssl;", nginx)


if __name__ == "__main__":
    unittest.main()
