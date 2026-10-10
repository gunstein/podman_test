"""Each app's readiness and checks (its app.yaml's ready and checks), asked of the local nginx.

ready is an HTTP path on the app's own hostname that answers 200 once the
app can serve; the platform waits for it after a start. checks are GET
requests with the status each must answer, run once the app is ready:
after an install and after a DR promotion, when Keycloak is already set up.
Both go through nginx on 127.0.0.1 with the app's hostname as the Host
header, so they also prove the route. Standard library only.
"""
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import settings

BASE = f'http://127.0.0.1:{settings.LOCAL_HTTP_PORT}'


def status(path, hostname):
    """The HTTP status of GET path on hostname's virtual host, or None if nothing answered."""
    try:
        with urlopen(Request(BASE + path, headers={'Host': hostname}), timeout=30) as response:
            return response.status
    except HTTPError as error:
        return error.code
    except (URLError, TimeoutError, ConnectionError):
        return None


def wait_ready(app, hostname, attempts=30, delay=1):
    """Wait until app's ready path answers 200 on hostname; an app without one is ready when it runs."""
    if not app.ready:
        return
    for attempt in range(attempts):
        if status(app.ready, hostname) == 200:
            return
        if attempt + 1 < attempts:
            time.sleep(delay)
    raise RuntimeError(f'{app.name}: {app.ready} on {hostname} did not answer 200 after {attempts} attempts')


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
    """What wait-ready.sh asks for each app with a ready path: HOSTNAME/PATH, e.g. todo.test/ready."""
    return [hostnames[app.name] + app.ready for app in platform.apps if app.ready]
