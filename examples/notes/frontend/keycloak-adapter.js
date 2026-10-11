import Keycloak from "/vendor/keycloak.js";

const applicationUrl = new URL("/", window.location.origin).href;
let client;

async function fetchJson(path, what) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(what + " failed: " + response.status);
  return response.json();
}


export default {
  async init() {
    // This app's login settings, which the platform mounts from its ConfigMap
    // (keycloak.frontend_login, docs/IDENTITY.md): where discovery is, the
    // realm and this app's client ID. Nothing about login is written here.
    const settings = await fetchJson("/config/login.json", "Login settings");
    // Keycloak has its own hostname: read the issuer through this app's own
    // origin, then log in there, where every app shares the SSO cookie.
    const discovery = await fetchJson(settings.discovery, "Identity discovery");
    const realmPath = "/realms/" + settings.realm;
    if (!discovery.issuer.endsWith(realmPath)) {
      throw new Error("Unexpected issuer " + discovery.issuer);
    }
    client = new Keycloak({
      url: discovery.issuer.slice(0, -realmPath.length),
      realm: settings.realm,
      clientId: settings.clientId,
    });
    // check-sso: learn whether the user is already logged in, without
    // sending them to the login page. The session-status iframe is off; the
    // token refresh in getAccessToken notices an ended session instead.
    return client.init({
      onLoad: "check-sso",
      pkceMethod: "S256",
      checkLoginIframe: false,
    });
  },
  isAuthenticated() {
    return Boolean(client.authenticated);
  },
  async login() {
    await client.login({redirectUri: applicationUrl});
  },
  async logout() {
    await client.logout({redirectUri: applicationUrl});
  },
  async getAccessToken() {
    if (!client.authenticated) return null;
    // Refresh the token first if it expires within 30 seconds.
    await client.updateToken(30);
    return client.token;
  },
  getUsername() {
    return client.tokenParsed?.preferred_username ?? client.tokenParsed?.sub ?? "";
  },
};
