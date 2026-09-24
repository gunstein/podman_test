"""Unit, safety and error-path tests for the Jinja2 manifest templates.

Complements the end-to-end coverage in tests/test_kube_runtime.py and friends
(which exercise manifests.py only transitively through render-kube-runtime.sh)
with direct, isolated coverage of manifests.py and render.py themselves.
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import jinja2
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from todo_installer import apps, manifests, render, stack  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
VALUES = ROOT / "deploy/environments/local/values.yaml"


def _database(name="widget"):
    return stack.Database(name=name, chart=name, replication_port=5432)


def _app(name="widget"):
    return apps.App(name=name, chart=name, hostname=name + ".test", keycloak_client=name + "-frontend")


class ManifestFunctionTests(unittest.TestCase):
    """Direct, isolated coverage of each manifests.py render_* function."""

    def test_render_postgres_produces_the_expected_claims_and_pod(self):
        docs = list(yaml.safe_load_all(
            manifests.render_postgres(ROOT, _database(), "docker.io/library/postgres:17.11")))
        self.assertEqual([doc["kind"] for doc in docs], ["PersistentVolumeClaim", "PersistentVolumeClaim", "Pod"])
        pod = docs[2]
        self.assertEqual(pod["metadata"]["name"], "widget-postgres")
        self.assertEqual(pod["spec"]["containers"][0]["image"], "docker.io/library/postgres:17.11")

    def test_render_postgres_config_carries_the_database_name(self):
        doc = next(yaml.safe_load_all(manifests.render_postgres_config(ROOT, _database())))
        self.assertEqual(doc["data"], {"POSTGRES_DB": "widget", "POSTGRES_USER": "widget"})

    def test_render_app_groups_migrate_backend_and_frontend(self):
        docs = list(yaml.safe_load_all(manifests.render_app(
            ROOT, _app(), "localhost/widget-backend:m12", "localhost/widget-frontend:m12")))
        pod = docs[0]
        self.assertEqual(pod["metadata"]["name"], "widget-app")
        self.assertEqual([c["name"] for c in pod["spec"]["initContainers"]], ["widget-migrate"])
        self.assertEqual([c["name"] for c in pod["spec"]["containers"]], ["widget-backend", "widget-frontend"])

    def test_render_app_config_carries_oidc_settings(self):
        doc = next(yaml.safe_load_all(manifests.render_app_config(ROOT, _app(), "widget.test", 8443, "debug")))
        self.assertEqual(doc["data"]["OIDC_AUDIENCE"], "widget-frontend")
        self.assertEqual(doc["data"]["LOG_LEVEL"], "debug")
        self.assertEqual(doc["data"]["OIDC_ISSUER"], "https://widget.test:8443/auth/realms/todo")

    def test_render_keycloak_wires_the_database_and_admin_secret(self):
        pod, config = list(yaml.safe_load_all(manifests.render_keycloak(
            ROOT, apps.KEYCLOAK_DATABASE, apps.KEYCLOAK_KUBE_ADMIN_SECRET, "todo.test", 8443, apps.KEYCLOAK_IMAGE)))
        self.assertEqual(config["data"]["KC_DB_URL_HOST"], "keycloak-postgres")
        secret_names = {e["valueFrom"]["secretKeyRef"]["name"] for e in pod["spec"]["containers"][0]["env"]}
        self.assertEqual(secret_names, {"keycloak-kube-postgres-secret", "keycloak-kube-admin-secret"})

    def test_render_shared_proxy_resolves_the_identity_hostname(self):
        widget, gadget = _app("widget"), _app("gadget")
        docs = list(yaml.safe_load_all(manifests.render_shared_proxy(
            ROOT, [widget, gadget], widget, "shared.test", "localhost/todo-proxy:m12")))
        config = next(d["data"]["nginx.conf"] for d in docs if d["metadata"]["name"] == "shared-nginx-config")
        self.assertIn("server_name shared.test;", config)
        self.assertIn("server_name gadget.test;", config)


class TemplateSafetyTests(unittest.TestCase):
    """The obligatory tojson property: no value can break the surrounding YAML.

    Deliberately excludes embedded newlines: shared-proxy.yaml.j2 interpolates
    hostnames raw (not tojson'd) inside the nginx.conf literal block scalar, same
    as the Helm template it replaced, relying on the app registry's hostname regex
    rather than template-level escaping. A literal newline there can break the
    surrounding YAML - a known, narrow gap (operator-authored values.yaml, not
    attacker input), out of scope for what tojson was asked to guarantee.
    """

    ADVERSARIAL = [
        "evil.test: injected",
        "evil.test # comment",
        "*anchor",
        "[not, a, list]",
        "{not: a, map: here}",
        "quote's and \"quotes\"",
    ]

    def test_adversarial_hostnames_survive_app_config_as_literal_strings(self):
        for value in self.ADVERSARIAL:
            with self.subTest(value=value):
                docs = list(yaml.safe_load_all(manifests.render_app_config(ROOT, _app(), value, 8443, "info")))
                self.assertEqual(len(docs), 1)
                self.assertIn(value, docs[0]["data"]["OIDC_ISSUER"])

    def test_adversarial_hostnames_survive_shared_proxy_env_as_literal_strings(self):
        app = _app()
        for value in self.ADVERSARIAL:
            with self.subTest(value=value):
                docs = list(yaml.safe_load_all(manifests.render_shared_proxy(
                    ROOT, [app], app, value, "localhost/todo-proxy:m12")))
                env = next(d["data"] for d in docs if d["metadata"]["name"] == "shared-nginx-env")
                self.assertEqual(env["TODO_TLS_HOSTNAME"], value)

    def test_adversarial_image_references_survive_postgres_as_literal_strings(self):
        for value in self.ADVERSARIAL:
            with self.subTest(value=value):
                docs = list(yaml.safe_load_all(manifests.render_postgres(ROOT, _database(), value)))
                self.assertEqual(docs[2]["spec"]["containers"][0]["image"], value)


class StrictUndefinedTests(unittest.TestCase):
    def test_missing_template_variable_raises_immediately(self):
        # Every production value is piped through | tojson, so a missing variable
        # surfaces as TypeError (json.dumps rejects the StrictUndefined sentinel)
        # rather than Jinja2's usual UndefinedError - still an immediate, loud
        # failure, just not the exception type StrictUndefined normally raises.
        template = manifests._environment(ROOT).get_template("postgres.yaml.j2")
        with self.assertRaisesRegex(TypeError, "StrictUndefined"):
            template.render(database=_database())


class RenderErrorTests(unittest.TestCase):
    def test_unknown_application_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Unknown or empty application selection"):
                render.render(ROOT, VALUES, directory, ("bogus",))

    def test_mixed_known_and_unknown_application_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Unknown or empty application selection"):
                render.render(ROOT, VALUES, directory, ("todo", "bogus"))

    def test_broken_template_syntax_fails_before_any_output(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory) / "project"
            shutil.copytree(ROOT / "deploy/manifests", project_root / "deploy/manifests")
            (project_root / "deploy/manifests/postgres.yaml.j2").write_text("{% broken\n")
            output = Path(directory) / "output"
            with self.assertRaises(jinja2.TemplateSyntaxError):
                render.render(project_root, VALUES, output)
            self.assertFalse(output.exists())

    def test_valid_jinja_producing_invalid_yaml_raises_a_clear_error(self):
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory) / "project"
            shutil.copytree(ROOT / "deploy/manifests", project_root / "deploy/manifests")
            (project_root / "deploy/manifests/postgres.yaml.j2").write_text("foo: [1, 2\n")
            output = Path(directory) / "output"
            with self.assertRaisesRegex(RuntimeError, "is not valid YAML"):
                render.render(project_root, VALUES, output)
            self.assertFalse(output.exists())
