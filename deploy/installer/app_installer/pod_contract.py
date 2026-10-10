"""The platform's rules for an app's rendered pod (docs/PLATFORM-PLAN.md, section 5.9).

An app brings its own pod template; after it is rendered, before it is
bundled or installed, check() holds the pod to what the platform can run
and back up: it is named after the app, runs only images the app declares,
mounts only its own secrets and volumes that hold no data of their own, and
asks for no host access or extra privileges. This checks trusted app
packages against the platform's contract; it is not a sandbox and does not
make an arbitrary template or image safe. Build host only (PyYAML).
"""
from . import secrets

# Volumes that hold no data of their own: version 1 keeps an app's data in
# PostgreSQL only (section 5.6), so a pod may mount nothing else.
VOLUME_KINDS = ('secret', 'configMap', 'emptyDir')
HOST_ACCESS = ('hostNetwork', 'hostPID', 'hostIPC')


def check(app, content):
    """Raise ValueError naming every way content, app's rendered pod, breaks the rules; return the pod."""
    import yaml

    documents = [document for document in yaml.safe_load_all(content) if document is not None]
    where = f'{app.pod_template}'
    if len(documents) != 1 or not isinstance(documents[0], dict) or documents[0].get('kind') != 'Pod':
        raise ValueError(f'{where}: must render exactly one Pod')
    pod = documents[0]
    spec = pod.get('spec') or {}
    problems = []
    name = (pod.get('metadata') or {}).get('name')
    if name != app.pod:
        problems.append(f'the pod is named {name!r}, not {app.pod}')
    problems += [f'it sets {key}' for key in HOST_ACCESS if spec.get(key)]
    containers = (spec.get('initContainers') or []) + (spec.get('containers') or [])
    images = {app.image(image.name) for image in app.images}
    config = app.names.resource('backend-config')
    ports = set()
    for container in containers:
        name = container.get('name', '')
        if not name.startswith(app.name + '-'):
            problems.append(f'container {name!r} is not named {app.name}-<part>')
        if container.get('image') not in images:
            problems.append(f'container {name} runs {container.get("image")!r}, an image app.yaml does not declare')
        context = container.get('securityContext') or {}
        if context.get('privileged'):
            problems.append(f'container {name} is privileged')
        if (context.get('capabilities') or {}).get('add'):
            problems.append(f'container {name} adds capabilities')
        if not ((container.get('resources') or {}).get('limits') or {}).get('memory'):
            problems.append(f'container {name} has no memory limit (resources.limits.memory)')
        for source in container.get('envFrom') or []:
            reference = (source.get('configMapRef') or {}).get('name')
            if reference is not None and reference != config:
                problems.append(f'container {name} reads ConfigMap {reference}, not its own {config}')
        ports.update(port.get('containerPort') for port in container.get('ports') or [])
    own_secrets = set(secrets.application_secret_mapping(app)) if app.has_database else set()
    for volume in spec.get('volumes') or []:
        kinds = [kind for kind in volume if kind != 'name']
        if kinds not in ([kind] for kind in VOLUME_KINDS):
            problems.append(f'volume {volume.get("name")!r} is {", ".join(kinds) or "empty"}, '
                            f'not one of {", ".join(VOLUME_KINDS)}')
        elif kinds == ['secret'] and (volume['secret'] or {}).get('secretName') not in own_secrets:
            problems.append(f'volume {volume.get("name")} mounts secret {(volume["secret"] or {}).get("secretName")}, '
                            f'not one of its own ({", ".join(sorted(own_secrets)) or "it has none"})')
        elif kinds == ['configMap'] and (volume['configMap'] or {}).get('name') != config:
            problems.append(f'volume {volume.get("name")} mounts ConfigMap {(volume["configMap"] or {}).get("name")}, '
                            f'not its own {config}')
    names = tuple(container.get('name') for container in spec.get('containers') or [])
    if app.containers and names != app.containers:
        problems.append(f'it runs the containers {", ".join(map(str, names))}, not {", ".join(app.containers)} '
                        '(App.containers, read from the same template when the platform was loaded)')
    problems += [f'no container port {endpoint.port} for endpoint {endpoint.name}'
                 for endpoint in app.endpoints if endpoint.port not in ports]
    if problems:
        raise ValueError(f'{where}: ' + '; '.join(problems))
    return pod
