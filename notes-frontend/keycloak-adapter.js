import Keycloak from "/vendor/keycloak.js";

const applicationUrl = new URL("/", window.location.origin).href;
let client;


export default {
  async init() {
    const response = await fetch("/auth/realms/todo/.well-known/openid-configuration");
    if (!response.ok) throw new Error("Identity discovery failed");
    const discovery = await response.json();
    // Both apps must authorize at the canonical identity origin to share its SSO cookie.
    client = new Keycloak({
      url: new URL("/auth", discovery.issuer).href,
      realm: "todo",
      clientId: "notes-frontend",
    });
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
