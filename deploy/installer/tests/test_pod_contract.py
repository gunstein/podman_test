"""pod_contract.py: an app's rendered pod held to the platform's rules (docs/PLATFORM-PLAN.md, section 5.9)."""
import copy
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_installer import manifests, platform_file, pod_contract  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


class PodContractTests(unittest.TestCase):
    def setUp(self):
        self.app = platform_file.checkout().app('todo')
        self.pod = yaml.safe_load(manifests.render_app(ROOT, self.app))

    def refused(self, message, pod):
        with self.assertRaisesRegex(ValueError, message):
            pod_contract.check(self.app, yaml.safe_dump(pod).encode())

    def changed(self, change):
        pod = copy.deepcopy(self.pod)
        change(pod)
        return pod

    def test_the_example_apps_pods_keep_the_rules(self):
        for app in platform_file.checkout().apps:
            with self.subTest(app=app.name):
                self.assertEqual(pod_contract.check(app, manifests.render_app(ROOT, app))['metadata']['name'],
                                 app.pod)

    def test_each_broken_rule_is_named(self):
        backend = lambda pod: pod['spec']['containers'][0]  # noqa: E731
        for message, change in (
            (r"the pod is named 'other-app', not todo-app", lambda pod: pod['metadata'].update(name='other-app')),
            (r'it sets hostNetwork', lambda pod: pod['spec'].update(hostNetwork=True)),
            (r'it sets hostPID', lambda pod: pod['spec'].update(hostPID=True)),
            (r"container 'notes-backend' is not named todo-<part>", lambda pod: backend(pod).update(name='notes-backend')),
            (r"runs 'docker.io/library/busybox', an image app.yaml does not declare",
             lambda pod: backend(pod).update(image='docker.io/library/busybox')),
            (r'container todo-backend is privileged', lambda pod: backend(pod)['securityContext'].update(privileged=True)),
            (r'container todo-backend adds capabilities',
             lambda pod: backend(pod)['securityContext']['capabilities'].update(add=['NET_ADMIN'])),
            (r'container todo-backend has no memory limit', lambda pod: backend(pod).pop('resources')),
            (r'reads ConfigMap notes-backend-config, not its own todo-backend-config',
             lambda pod: backend(pod)['envFrom'][0]['configMapRef'].update(name='notes-backend-config')),
            (r"volume 'data' is hostPath, not one of secret, configMap, emptyDir",
             lambda pod: pod['spec']['volumes'].append({'name': 'data', 'hostPath': {'path': '/var'}})),
            (r"volume 'uploads' is persistentVolumeClaim",
             lambda pod: pod['spec']['volumes'].append({'name': 'uploads',
                                                        'persistentVolumeClaim': {'claimName': 'x'}})),
            (r'mounts secret notes-kube-backend-secret, not one of its own',
             lambda pod: pod['spec']['volumes'][0]['secret'].update(secretName='notes-kube-backend-secret')),
            (r'mounts ConfigMap keycloak-config, not its own todo-backend-config',
             lambda pod: pod['spec']['volumes'].append({'name': 'other', 'configMap': {'name': 'keycloak-config'}})),
            (r'no container port 8000 for endpoint backend', lambda pod: backend(pod).pop('ports')),
        ):
            with self.subTest(message=message):
                self.refused(message, self.changed(change))
        # An emptyDir holds no data of its own, so a pod may mount one.
        pod = self.changed(lambda pod: pod['spec']['volumes'].append({'name': 'tmp', 'emptyDir': {}}))
        pod_contract.check(self.app, yaml.safe_dump(pod).encode())

    def test_a_template_renders_exactly_one_pod(self):
        for content in (b'', b'kind: ConfigMap\n', yaml.safe_dump_all([self.pod, self.pod]).encode()):
            with self.subTest(content=content[:20]), \
                    self.assertRaisesRegex(ValueError, r'examples/todo/pod.yaml.j2: must render exactly one Pod'):
                pod_contract.check(self.app, content)


if __name__ == '__main__':
    unittest.main()
