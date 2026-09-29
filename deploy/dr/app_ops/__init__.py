"""Controller-side DR orchestration over plain SSH; the hosts run app_dr_host, app_installer and the tools."""
import sys
from pathlib import Path

# app-ops reuses the installer's registry and settings. On the controller it
# runs from a checkout or the operations package, where the installer is
# deploy/installer next to deploy/dr; see deploy/dr/README.md ("Where DR finds
# the installer"). Done here, so every module of the package can import it.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'installer'))
