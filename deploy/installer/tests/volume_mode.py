"""Going back to the TLS volume, as the top of deploy/manifests/shared-proxy.yaml.j2 tells a person to.

The tests that keep the volume storage working (test_tls.py, the DR's
test_nginx_tls.py, tests/test_proxy_configuration.py) use this on the
template or on a rendered shared-proxy.yaml: every line that starts with
"#~ " (or is just "#~") loses that prefix, and the lines from "# BEGIN
secret" to "# END secret" go.
"""
import re
import shutil
import tempfile
from pathlib import Path

BLOCK = re.compile(r'^[ \t]*# BEGIN secret\n.*?^[ \t]*# END secret\n', re.M | re.S)


def volume_manifest(text):
    """shared-proxy.yaml (or its template) as it is with the TLS volume instead of the Kube secret."""
    text = BLOCK.sub('', text)
    return re.sub(r'^#~ ?', '', text, flags=re.M)


def volume_bundle(bundle):
    """A copy of an offline bundle directory whose shared-proxy.yaml uses the TLS volume; the caller removes it."""
    copy = Path(tempfile.mkdtemp()) / 'bundle'
    shutil.copytree(bundle, copy)
    for path in copy.rglob('shared-proxy.yaml'):
        path.write_text(volume_manifest(path.read_text()))
    return copy
