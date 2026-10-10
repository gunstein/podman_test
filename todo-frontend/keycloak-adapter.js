import Keycloak from "/vendor/keycloak.js";

const applicationUrl = new URL("/", window.location.origin).href;
let client;

export default {
  async init() {
    // Keycloak has its own hostname: read the issuer through this app's own
    // origin, then log in there, where every app shares the SSO cookie.
    const response = await fetch("/auth/realms/todo/.well-known/openid-configuration");
    if (!response.ok) throw new Error("Identity discovery failed");
    const discovery = await response.json();
    client = new Keycloak({
      url: new URL("/auth", discovery.issuer).href,
      realm: "todo",
      clientId: "todo-frontend",
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
