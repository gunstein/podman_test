const form=document.querySelector("#todo-form");
const input=document.querySelector("#todo-title");
const list=document.querySelector("#todo-list");
const message=document.querySelector("#message");

async function api(url, options={}) {
  const response=await fetch(url,{headers:{"Content-Type":"application/json"},...options});
  if (!response.ok) throw new Error("HTTP "+response.status);
  return response.status===204 ? null : response.json();
}

async function load() {
  try {
    message.textContent="";
    const todos=await api("/api/todos");
    list.replaceChildren();
    if (!todos.length) list.innerHTML="<li>No todos yet.</li>";
    for (const todo of todos) {
      const item=document.createElement("li");
      item.className="todo";
      const label=document.createElement("label");
      const checkbox=document.createElement("input");
      checkbox.type="checkbox";
      checkbox.checked=todo.completed;
      checkbox.onchange=async()=> {
        try {
          await api("/api/todos/"+todo.id,{method:"PUT",body:JSON.stringify({title:todo.title,completed:checkbox.checked})});
          await load();
        } catch(error) { showError(error); }
      };
      const title=document.createElement("span");
      title.textContent=todo.title;
      if(todo.completed) title.className="completed";
      const remove=document.createElement("button");
      remove.textContent="Delete";
      remove.onclick=async()=> {
        try { await api("/api/todos/"+todo.id,{method:"DELETE"}); await load(); }
        catch(error) { showError(error); }
      };
      label.append(checkbox,title);
      item.append(label,remove);
      list.append(item);
    }
  } catch(error) { showError(error); }
}

function showError(error) {
  console.error(error);
  message.textContent="Something went wrong.";
}

form.onsubmit=async event=> {
  event.preventDefault();
  try {
    await api("/api/todos",{method:"POST",body:JSON.stringify({title:input.value.trim()})});
    form.reset();
    await load();
  } catch(error) { showError(error); }
};
load();
