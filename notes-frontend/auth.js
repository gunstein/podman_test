// Todo depends only on init, isAuthenticated, login, logout, getAccessToken
// and getUsername. Replace the adapter here for a different provider.
// Only Keycloak is implemented and validated by this demo.
export {default} from "./keycloak-adapter.js";
