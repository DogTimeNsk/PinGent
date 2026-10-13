let csrfToken = null;
const $ = (selector) => document.querySelector(selector);

async function request(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (csrfToken && !["GET", "HEAD"].includes(options.method || "GET")) headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "Request failed");
  }
  return response.status === 204 ? null : response.json();
}

function showNotice(message = "") { $("#notice").textContent = message; }
function escape(text) { const node = document.createElement("span"); node.textContent = text || ""; return node.innerHTML; }

function taskCard(task, active) {
  const stopping = task.status === "cancelling";
  const cancel = active && !stopping ? `<button class="secondary" data-cancel="${task.id}">Cancel</button>` : "";
  const result = task.result_html ? `<div class="markdown">${task.result_html}</div>` : "";
  const status = stopping ? "Stopping…" : task.status;
  const className = stopping ? "task task-stopping" : "task";
  return `<article class="${className}"><div class="task-meta"><span>${escape(status)}</span>${cancel}</div><p>${escape(task.prompt)}</p>${result}</article>`;
}

async function refresh() {
  const [workspaces, state, history] = await Promise.all([request("/api/workspaces"), request("/api/state"), request("/api/history")]);
  const select = $("#workspace");
  select.innerHTML = workspaces.workspaces.map((workspace, index) => `<option value="${index + 1}" ${workspace === workspaces.selected ? "selected" : ""}>${escape(workspace)}</option>`).join("");
  $("#session-state").textContent = `Codex session: ${state.session_state.replace("_", " ")}`;
  const stoppingTasks = state.tasks.filter((task) => task.status === "cancelling");
  const activeTasks = state.tasks.filter((task) => task.status !== "cancelling");
  const activeMarkup = activeTasks.length ? activeTasks.map((task) => taskCard(task, true)).join("") : '<p class="empty">No running or queued tasks.</p>';
  const stoppingMarkup = stoppingTasks.length ? `<section class="task-group stopping-group"><h3>Stopping</h3>${stoppingTasks.map((task) => taskCard(task, true)).join("")}</section>` : "";
  $("#tasks").innerHTML = `${activeMarkup}${stoppingMarkup}`;
  $("#history").innerHTML = history.tasks.length ? history.tasks.map((task) => taskCard(task, false)).join("") : '<p class="empty">No completed tasks yet.</p>';
}

async function enterApp(user) {
  csrfToken = user.csrf_token;
  $("#identity").textContent = `Signed in as ${user.username}`;
  $("#login-panel").hidden = true;
  $("#app-panel").hidden = false;
  await refresh();
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try { await enterApp(await request("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: $("#username").value, password: $("#password").value }) })); } catch (error) { showNotice(error.message); }
});
$("#task-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try { await request("/api/tasks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt: $("#prompt").value }) }); $("#prompt").value = ""; showNotice("Task queued."); await refresh(); } catch (error) { showNotice(error.message); }
});
$("#workspace").addEventListener("change", async (event) => {
  try { await request("/api/workspaces", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ number: Number(event.target.value) }) }); await refresh(); } catch (error) { showNotice(error.message); }
});
$("#reset-session").addEventListener("click", async () => {
  if (!confirm("Start a new Codex session for this workspace?")) return;
  try { await request("/api/session/reset", { method: "POST" }); showNotice("Session reset."); await refresh(); } catch (error) { showNotice(error.message); }
});
$("#tasks").addEventListener("click", async (event) => {
  const taskId = event.target.dataset.cancel;
  if (!taskId) return;
  try {
    const result = await request(`/api/tasks/${encodeURIComponent(taskId)}`, { method: "DELETE" });
    showNotice(result.status === "cancelling" ? "Cancellation requested. Waiting for Codex to stop." : "Task cancelled.");
    await refresh();
  } catch (error) { showNotice(error.message); }
});
$("#logout").addEventListener("click", async () => { await request("/api/auth/logout", { method: "POST" }); location.reload(); });
request("/api/auth/me").then(enterApp).catch(() => {});
setInterval(() => { if (!$("#app-panel").hidden) refresh().catch((error) => showNotice(error.message)); }, 3000);
