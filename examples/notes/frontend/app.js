import auth from "./auth.js";

const form = document.querySelector("#note-form");
const title = document.querySelector("#note-title");
const body = document.querySelector("#note-body");
const list = document.querySelector("#note-list");
const message = document.querySelector("#message");
const login = document.querySelector("#login");
const logout = document.querySelector("#logout");
const userStatus = document.querySelector("#user-status");
const save = document.querySelector("#save");
const cancel = document.querySelector("#cancel");
let editing = null;

async function api(url, options = {}) {
  const headers = {};
  if (options.body) headers["Content-Type"] = "application/json";
  const token = await auth.getAccessToken();
  if (token) headers.Authorization = "Bearer " + token;
  const response = await fetch(url, {...options, headers});
  if (!response.ok) throw new Error("HTTP " + response.status);
  return response.status === 204 ? null : response.json();
}

function updateAuthentication() {
  login.hidden = auth.isAuthenticated();
  logout.hidden = !auth.isAuthenticated();
  form.hidden = !auth.isAuthenticated();
  userStatus.textContent = auth.isAuthenticated()
    ? "Logged in as " + auth.getUsername()
    : "Reading publicly";
}

function resetForm() {
  editing = null;
  form.reset();
  save.textContent = "Add note";
  cancel.hidden = true;
}

function showError(error) {
  console.error(error);
  message.textContent = "Something went wrong.";
}

async function load() {
  try {
    message.textContent = "";
    const notes = await api("/api/notes");
    list.replaceChildren();
    if (!notes.length) list.innerHTML = "<li>No notes yet.</li>";
    for (const note of notes) {
      const item = document.createElement("li");
      item.className = "note";
      const heading = document.createElement("h2");
      heading.textContent = note.title;
      const text = document.createElement("p");
      text.className = "note-body";
      text.textContent = note.body;
      item.append(heading, text);
      if (auth.isAuthenticated()) {
        const actions = document.createElement("div");
        actions.className = "actions";
        const edit = document.createElement("button");
        edit.textContent = "Edit";
        edit.onclick = () => {
          editing = note.id;
          title.value = note.title;
          body.value = note.body;
          save.textContent = "Save note";
          cancel.hidden = false;
          title.focus();
        };
        const remove = document.createElement("button");
        remove.textContent = "Delete";
        remove.onclick = async () => {
          try {
            await api("/api/notes/" + note.id, {method: "DELETE"});
            if (editing === note.id) resetForm();
            await load();
          } catch (error) { showError(error); }
        };
        actions.append(edit, remove);
        item.append(actions);
      }
      list.append(item);
    }
  } catch (error) { showError(error); }
}

login.onclick = () => auth.login().catch(showError);
logout.onclick = () => auth.logout().catch(showError);
cancel.onclick = resetForm;
form.onsubmit = async (event) => {
  event.preventDefault();
  try {
    await api(editing === null ? "/api/notes" : "/api/notes/" + editing, {
      method: editing === null ? "POST" : "PUT",
      body: JSON.stringify({title: title.value.trim(), body: body.value}),
    });
    resetForm();
    await load();
  } catch (error) { showError(error); }
};

try {
  await auth.init();
  updateAuthentication();
  await load();
} catch (error) { showError(error); }
