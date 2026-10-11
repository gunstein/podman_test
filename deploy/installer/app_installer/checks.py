"""Each app's readiness and checks (its app.yaml's ready and checks), asked of the local nginx.

ready is an HTTP path on the app's own hostname that answers 200 once the
app can serve; the platform waits for it after a start (for an app without
one, until its containers run). checks are GET
requests with the status each must answer, run once the app is ready:
after an install and after a DR promotion, when Keycloak is already set up.
Both go through nginx on 127.0.0.1 with the app's hostname as the Host
header, so they also prove the route. They send no token or cookie, and a
redirect is not followed: a check sees the status of its own path (a 302
to a login page is 302). Standard library only.
"""
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import settings
from .commands import run as command

BASE = f'http://127.0.0.1:{settings.LOCAL_HTTP_PORT}'


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None  # urllib then raises HTTPError with the 3xx status


_OPENER = build_opener(_NoRedirect)


def status(path, hostname):
    """The HTTP status of GET path on hostname's virtual host, or None if nothing answered."""
    try:
        with _OPENER.open(Request(BASE + path, headers={'Host': hostname}), timeout=30) as response:
            return response.status
    except HTTPError as error:
        return error.code
    except (URLError, TimeoutError, ConnectionError):
        return None


def running(container):
    """True if Podman runs container (podman container inspect; a missing one is not running)."""
    result = command('podman', 'container', 'inspect', '--format', '{{.State.Running}}', container, allowed=(0, 125))
    return result.returncode == 0 and result.stdout.strip() == 'true'


def wait_ready(app, hostname, attempts=30, delay=1):
    """Wait until app's ready path answers 200 on hostname or, without one, until its containers run."""
    for attempt in range(attempts):
        if (status(app.ready, hostname) == 200 if app.ready else all(map(running, app.containers))):
            return
        if attempt + 1 < attempts:
            time.sleep(delay)
    waited = f'{app.ready} on {hostname} did not answer 200' if app.ready else \
        f'its containers {", ".join(app.containers)} were not all running'
    raise RuntimeError(f'{app.name}: {waited} after {attempts} attempts')


def run(app, hostname):
    """Run app's checks on hostname once; raise naming the first that answers another status."""
    for check in app.checks:
        answered = status(check.path, hostname)
        if answered != check.status:
            raise RuntimeError(f'{app.name}: GET {check.path} on {hostname} answered '
                               f'{answered or "nothing"}, not {check.status}')


def verify(platform, hostnames):
    """Every app of platform ready, then its checks passed; hostnames is {app name: public hostname}."""
    for app in platform.apps:
        wait_ready(app, hostnames[app.name])
        run(app, hostnames[app.name])


def ready_urls(platform, hostnames):
    """What wait-ready.sh asks for each app with a ready path: HOSTNAME/PATH, e.g. shop.example.org/ready."""
    return [hostnames[app.name] + app.ready for app in platform.apps if app.ready]
