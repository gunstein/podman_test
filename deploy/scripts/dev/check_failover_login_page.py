"""CI: failover's login-page check against the real Keycloak and nginx of a build-mode install.

The full-stack job runs it after the stack is up, as
PYTHONPATH=deploy/installer:deploy/dr python deploy/scripts/dev/check_failover_login_page.py.
It lives in a .py file, not in the workflow, so ruff and pyright check its calls.
"""
from app_installer import platform_file
from app_ops import failover, inventory
from app_ops.transport import Host


def main():
    host = Host(inventory.HostSpec(name="ci", role="current_primary", address="127.0.0.1",
                                   user="runner", home="/home/runner", local=True))
    # The build-mode install serves platform.yaml's hostnames and Keycloak's.
    platform = platform_file.checkout()
    failover.login_page(host, platform, {app.name: app.hostname for app in platform.apps}, platform.identity_hostname)
    # A redirect Keycloak does not know must be refused, or the check proves nothing.
    query = failover.urlencode({"client_id": "todo-frontend", "redirect_uri": "https://evil.test/",
                                "response_type": "code", "scope": "openid",
                                "code_challenge": failover.PKCE_CHALLENGE, "code_challenge_method": "S256"})
    try:
        page = failover.https(host, platform.identity_hostname, "/auth/realms/todo/protocol/openid-connect/auth?" + query)
    except RuntimeError:
        page = ""
    if 'id="username"' in page:
        raise SystemExit("Keycloak showed its login form for an unknown redirect")
    print("login-page check passed against the real stack")


if __name__ == "__main__":
    main()
