import subprocess
import tempfile
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.TemporaryDirectory()
RUNTIME = Path(_tmp.name)

helm_bin = os.environ.get("HELM", "/tmp/linux-amd64/helm")
if not Path(helm_bin).exists():
    helm_bin = "helm" # assume in PATH

subprocess.run(
    [str(ROOT / "scripts" / "render-kube-runtime.sh"),
     str(ROOT / "helm" / "todo" / "values-prod.yaml"),
     str(RUNTIME)],
    check=True,
    env={**os.environ, "HELM": helm_bin}
)

for j2 in (ROOT / "ansible" / "roles").glob("*/templates/*.kube.j2"):
    dest = RUNTIME / j2.name.replace(".j2", "")
    lines = [line for line in j2.read_text().splitlines() if not line.strip().startswith("{%")]
    dest.write_text("\n".join(lines) + "\n")
import shutil

if (ROOT / "kube" / "runtime" / "README.md").exists():
    shutil.copy(ROOT / "kube" / "runtime" / "README.md", RUNTIME)
if (ROOT / "kube" / "runtime" / "RESULTS.md").exists():
    shutil.copy(ROOT / "kube" / "runtime" / "RESULTS.md", RUNTIME)

