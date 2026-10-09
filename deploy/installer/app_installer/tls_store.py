"""Where nginx's TLS files live, as settings.NGINX_TLS_STORAGE says.

  secret  Podman secrets (tls_secrets.py), the default: nginx mounts a Kube
          secret, and the installer makes and renews its files.
  volume  the TLS volume todo-nginx-data (tls.py): the shared-proxy pod's
          init container makes them, nginx mounts the volume read-only.

Both modules offer the same commands (request, install, status, check and
recorded_hostnames); the installer's CLI, its nightly check and the DR tools
call the one module() returns. Going back to the volume also needs the
manifest's volume parts (deploy/manifests/shared-proxy.yaml.j2, docs/TLS.md).
"""
from . import settings, tls, tls_secrets


def secret_storage():
    """True if nginx's TLS files are Podman secrets."""
    if settings.NGINX_TLS_STORAGE not in ('secret', 'volume'):
        raise ValueError(f'NGINX_TLS_STORAGE must be "secret" or "volume", not {settings.NGINX_TLS_STORAGE!r}')
    return settings.NGINX_TLS_STORAGE == 'secret'


def module():
    """tls_secrets or tls: the module that keeps nginx's TLS files on this host."""
    return tls_secrets if secret_storage() else tls
