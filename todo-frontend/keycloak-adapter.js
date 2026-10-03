import Keycloak from "/vendor/keycloak.js";

const applicationUrl = new URL("/", window.location.origin).href;
// Todo is served on the identity hostname, so its own origin is Keycloak's.
// Notes, on another hostname, reads the issuer first (notes-frontend).
const client = new Keycloak({
  url: window.location.origin + "/auth",
  realm: "todo",
  clientId: "todo-frontend",
});

export default {
  init() {
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
