import Keycloak from "/vendor/keycloak.js";

const applicationUrl = new URL("/", window.location.origin).href;
const client = new Keycloak({
  url: window.location.origin + "/auth",
  realm: "todo",
  clientId: "todo-frontend",
});

export default {
  init() {
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
    await client.updateToken(30);
    return client.token;
  },
  getUsername() {
    return client.tokenParsed?.preferred_username ?? client.tokenParsed?.sub ?? "";
  },
};
