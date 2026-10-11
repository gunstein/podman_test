import auth from "./auth.js";

const element = (id) => document.getElementById(id);

function showError(error) {
  element("error").textContent = error.message || String(error);
  element("error").hidden = false;
}

async function request(path, options = {}) {
  const token = await auth.getAccessToken();
  if (!token) throw new Error("Please log in to use the guestbook.");
  const response = await fetch(path, {
    ...options,
    headers: {"Content-Type": "application/json", Authorization: `Bearer ${token}`},
  });
  if (!response.ok) {
    if (response.status === 401) {
      element("entries").replaceChildren();
      element("book").hidden = true;
      element("login").hidden = false;
      throw new Error("Your session has expired. Please log in again.");
    }
    throw new Error(`Request failed (${response.status}). Please try again.`);
  }
  return response.status === 204 ? null : response.json();
}

async function refresh() {
  const entries = await request("/api/entries");
  element("entries").replaceChildren();
  element("empty").hidden = entries.length !== 0;
  for (const entry of entries) {
    const item = document.createElement("li");
    const message = document.createElement("p");
    // Messages are text, including anything that looks like HTML.
    message.textContent = entry.message;
    const time = document.createElement("time");
    time.dateTime = entry.created_at;
    time.textContent = new Date(entry.created_at).toLocaleString();
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Delete";
    remove.setAttribute("aria-label", `Delete entry ${entry.id}`);
    remove.addEventListener("click", () => perform(remove, async () => {
      await request(`/api/entries/${entry.id}`, {method: "DELETE"});
      await refresh();
    }));
    item.append(message, time, remove);
    element("entries").append(item);
  }
}

async function perform(button, action) {
  button.disabled = true;
  element("error").hidden = true;
  try {
    await action();
  } catch (error) {
    showError(error);
  } finally {
    button.disabled = false;
  }
}

element("login").addEventListener("click", () => perform(element("login"), () => auth.login()));
element("logout").addEventListener("click", () => perform(element("logout"), () => auth.logout()));
element("refresh").addEventListener("click", () => perform(element("refresh"), refresh));
element("entry-form").addEventListener("submit", (event) => {
  event.preventDefault();
  perform(element("submit"), async () => {
    const message = element("message").value.trim();
    if (!message) throw new Error("Write a message before signing.");
    await request("/api/entries", {method: "POST", body: JSON.stringify({message})});
    element("message").value = "";
    await refresh();
  });
});

try {
  await auth.init();
  const loggedIn = auth.isAuthenticated();
  element("login").hidden = loggedIn;
  element("logout").hidden = !loggedIn;
  element("book").hidden = !loggedIn;
  element("session").textContent = loggedIn
    ? `Signed in as ${auth.getUsername()}`
    : "Log in to read, write and delete messages.";
  if (loggedIn) await refresh();
} catch (error) {
  element("session").textContent = "Could not load the guestbook. Reload to try again.";
  showError(error);
}
