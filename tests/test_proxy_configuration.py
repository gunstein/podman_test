import pathlib
import unittest

import jinja2
import yaml

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


class ProxyConfigurationTests(unittest.TestCase):
    def test_clean_deploy_redirect_update_preserves_client_security_and_is_idempotent(self):
        tasks = yaml.safe_load(read("ansible/roles/todo_kube_runtime/tasks/main.yml"))
        update = next(t for t in tasks if t["name"] ==
                      "Update the Todo frontend stable redirect and origin")
        environment = jinja2.Environment()
        environment.filters["combine"] = lambda value, changes: {**value, **changes}
        original = {
            "redirectUris": ["https://localhost:8443/"],
            "webOrigins": ["https://localhost:8443"],
            "attributes": {"pkce.code.challenge.method": "S256"},
            "protocolMappers": [{"name": "todo audience"}],
            "publicClient": True,
            "directAccessGrantsEnabled": False,
        }
        context = {"todo_kube_client": {"json": original},
                   "todo_kube_public_origin": "https://todo.test:8443"}
        condition = environment.compile_expression(update["when"])
        self.assertTrue(condition(**context))
        expression = update["ansible.builtin.uri"]["body"].strip()[2:-2].strip()
        updated = environment.compile_expression(expression)(**context)
        self.assertEqual(updated["redirectUris"], ["https://todo.test:8443/"])
        self.assertEqual(updated["webOrigins"], ["https://todo.test:8443"])
        for key in ("attributes", "protocolMappers", "publicClient", "directAccessGrantsEnabled"):
            self.assertEqual(updated[key], original[key])
        self.assertFalse(condition(**{**context, "todo_kube_client": {"json": updated}}))
        self.assertTrue(update["changed_when"])

    def test_clean_deploy_suppresses_credentials_and_token_bearing_requests(self):
        tasks = yaml.safe_load(read("ansible/roles/todo_kube_runtime/tasks/main.yml"))
        protected = []
        for task in tasks:
            uri = task.get("ansible.builtin.uri", {})
            if "Authorization" in uri.get("headers", {}) or "password" in uri.get("body", {}):
                protected.append(task)
                self.assertTrue(task.get("no_log"), task["name"])
        self.assertEqual(len(protected), 4)

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

        playbook = read("ansible/deploy-promoted-application.yml")
        inventory = read("ansible/inventory-recovery.example.ini")
        self.assertIn("m14_service_hostname: todo.test", playbook)
        self.assertIn("m14_service_port: 8443", playbook)
        self.assertNotIn("todo_service_hostname=", inventory)
        self.assertNotIn("todo_service_port=", inventory)


if __name__ == "__main__":
    unittest.main()
