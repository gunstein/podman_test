"""Browser-level adapter tests: no VM, IdP or Node.js required."""
from pathlib import Path

from playwright.sync_api import expect

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
FAKE_SDK = """
export default class Keycloak {
  constructor(config) {
    window.sdkConfig = config;
    this.authenticated = window.testAuthenticated ?? false;
    this.token = "old-test-token";
    this.tokenParsed = {preferred_username: "adapter-user"};
    window.sdk = this;
  }
  async init(options) { window.initOptions = options; }
  async login(options) { window.loginOptions = options; }
  async logout(options) { window.logoutOptions = options; }
  async updateToken(seconds) {
    window.refreshSeconds = seconds;
    if (window.failRefresh) throw new Error("refresh failed");
    this.token = "new-test-token";
  }
}
"""


def serve(page):
    requests = []

    def handle(route):
        path = route.request.url.split("https://adapter.test", 1)[1].split("?")[0]
        if path == "/vendor/keycloak.js":
            route.fulfill(content_type="text/javascript", body=FAKE_SDK)
        elif path == "/api/todos":
            requests.append(route.request.headers)
            route.fulfill(content_type="application/json", body="[]")
        else:
            file = FRONTEND / (path.lstrip("/") or "index.html")
            if not file.is_file():
                route.fulfill(status=404)
                return
            mime = {".js": "text/javascript", ".css": "text/css"}.get(
                file.suffix, "text/html"
            )
            route.fulfill(content_type=mime, body=file.read_text())

    page.route("https://adapter.test/**", handle)
    return requests


def test_public_adapter_and_redirects(page):
    requests = serve(page)
    page.goto("https://adapter.test/")
    expect(page.get_by_text("Reading publicly")).to_be_visible()
    assert requests and "authorization" not in requests[0]
    assert page.evaluate("window.initOptions") == {
        "onLoad": "check-sso", "pkceMethod": "S256", "checkLoginIframe": False
    }
    page.get_by_role("button", name="Log in").click()
    assert page.evaluate("window.loginOptions.redirectUri") == "https://adapter.test/"
    assert page.evaluate("window.refreshSeconds") is None


def test_authenticated_adapter_refresh_and_failure(page):
    requests = serve(page)
    page.add_init_script("window.testAuthenticated = true")
    page.goto("https://adapter.test/")
    expect(page.get_by_text("Logged in as adapter-user")).to_be_visible()
    assert requests[0]["authorization"] == "Bearer new-test-token"
    assert page.evaluate("window.refreshSeconds") == 30
    page.get_by_role("button", name="Log out").click()
    assert page.evaluate("window.logoutOptions.redirectUri") == "https://adapter.test/"
    # A refresh failure must propagate, never return a stale bearer token.
    assert page.evaluate("""async () => {
      window.failRefresh = true;
      const {default: auth} = await import('/auth.js');
      try { await auth.getAccessToken(); return 'unexpected success'; }
      catch { return 'rejected'; }
    }""") == "rejected"
