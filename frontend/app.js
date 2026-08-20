const todoList = document.querySelector("#todo-list");

async function loadTodos() {
  try {
    const response = await fetch("/api/todos");

    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }

    const todos = await response.json();
    todoList.replaceChildren();

    for (const todo of todos) {
      const item = document.createElement("li");
      item.textContent = `${todo.completed ? "✓" : "○"} ${todo.title}`;
      todoList.append(item);
    }
  } catch (error) {
    console.error(error);
    todoList.innerHTML = "<li>Could not load todos.</li>";
  }
}

loadTodos();
