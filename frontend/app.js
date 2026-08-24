import Keycloak from "/vendor/keycloak.js";

const form = document.querySelector("#todo-form");
const input = document.querySelector("#todo-title");
const list = document.querySelector("#todo-list");
const message = document.querySelector("#message");
const login = document.querySelector("#login");
const logout = document.querySelector("#logout");
const userStatus = document.querySelector("#user-status");
const applicationUrl = new URL("/", window.location.origin).href;

const keycloak = new Keycloak({
  url: window.location.origin + "/auth",
  realm: "todo",
  clientId: "todo-frontend",
});

async function api(url, options = {}) {
  const headers = {};
  if (options.body) headers["Content-Type"] = "application/json";
  if (keycloak.authenticated) {
    await keycloak.updateToken(30);
    headers.Authorization = "Bearer " + keycloak.token;
  }
  const response = await fetch(url, {...options, headers});
  if (!response.ok) throw new Error("HTTP " + response.status);
  return response.status === 204 ? null : response.json();
}

function updateAuthentication() {
  login.hidden = keycloak.authenticated;
  logout.hidden = !keycloak.authenticated;
  form.hidden = !keycloak.authenticated;
  userStatus.textContent = keycloak.authenticated
    ? "Logged in as " + keycloak.tokenParsed.preferred_username
    : "Reading publicly";
}

async function load() {
  try {
    message.textContent = "";
    const todos = await api("/api/todos");
    list.replaceChildren();
    if (!todos.length) list.innerHTML = "<li>No todos yet.</li>";
    for (const todo of todos) {
      const item = document.createElement("li");
      item.className = "todo";
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = todo.completed;
      checkbox.disabled = !keycloak.authenticated;
      checkbox.onchange = async () => {
        try {
          await api("/api/todos/" + todo.id, {
            method: "PUT",
            body: JSON.stringify({title: todo.title, completed: checkbox.checked}),
          });
          await load();
        } catch (error) { showError(error); }
      };
      const title = document.createElement("span");
      title.textContent = todo.title;
      if (todo.completed) title.className = "completed";
      label.append(checkbox, title);
      item.append(label);
      if (keycloak.authenticated) {
        const remove = document.createElement("button");
        remove.textContent = "Delete";
        remove.onclick = async () => {
          try {
            await api("/api/todos/" + todo.id, {method: "DELETE"});
            await load();
          } catch (error) { showError(error); }
        };
        item.append(remove);
      }
      list.append(item);
    }
  } catch (error) { showError(error); }
}

function showError(error) {
  console.error(error);
  message.textContent = "Something went wrong.";
}

login.onclick = () => keycloak.login({redirectUri: applicationUrl});
logout.onclick = () => keycloak.logout({redirectUri: applicationUrl});

form.onsubmit = async (event) => {
  event.preventDefault();
  try {
    await api("/api/todos", {
      method: "POST",
      body: JSON.stringify({title: input.value.trim()}),
    });
    form.reset();
    await load();
  } catch (error) { showError(error); }
};

try {
  await keycloak.init({
    onLoad: "check-sso",
    pkceMethod: "S256",
    checkLoginIframe: false,
  });
  updateAuthentication();
  await load();
} catch (error) {
  showError(error);
}
