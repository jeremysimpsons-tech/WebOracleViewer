const state = {
  csrf: document.querySelector('meta[name="csrf-token"]').content,
  connections: [],
  workspace: null,
  owners: [],
  currentUser: "",
  ownerNotice: "",
  objects: {},
  expanded: new Set(),
  editing: null,
  timer: null,
  libraryBucket: null,
  gridState: {},
  sessionReport: null,
  sessionFocus: null,
  tablespaceReport: null,
};

const TYPES = [
  "TABLE",
  "VIEW",
  "MATERIALIZED VIEW",
  "INDEX",
  "SEQUENCE",
  "PROCEDURE",
  "FUNCTION",
  "PACKAGE",
  "TRIGGER",
  "SYNONYM",
];

document.addEventListener("DOMContentLoaded", () => {
  bindChrome();
  window.addEventListener("pagehide", (event) => {
    if (event.persisted || !state.csrf) return;
    fetch("/api/connections/close-browser", {
      method: "POST",
      credentials: "same-origin",
      keepalive: true,
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": state.csrf,
      },
      body: "{}",
    });
  });
  refreshAll().catch((error) => toast(error.message, "error"));
});

function bindChrome() {
  document.getElementById("btn-logout").addEventListener("click", logout);
  document.getElementById("btn-settings").addEventListener("click", () => {
    document.getElementById("settings-dialog").showModal();
    loadClientPrefs();
  });
  document.getElementById("pref-save").addEventListener("click", saveClientPrefs);
  document.getElementById("settings-close").addEventListener("click", () => {
    document.getElementById("settings-dialog").close();
  });
  document.getElementById("btn-disconnect").addEventListener("click", () => disconnect(false));
  document.getElementById("btn-new-conn").addEventListener("click", () => openDialog(null));
  document.getElementById("conn-select").addEventListener("change", (event) => {
    if (event.target.value) activate(event.target.value);
  });
  document.getElementById("conn-close").addEventListener("click", closeDialog);
  document.getElementById("conn-form").addEventListener("submit", saveConnection);
  document.getElementById("conn-test").addEventListener("click", testConnection);
  document.querySelectorAll('input[name="mode"]').forEach((input) => {
    input.addEventListener("change", syncModePanels);
  });
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => showTab(button.dataset.tab));
  });
  document.getElementById("btn-run").addEventListener("click", () => runSql(false));
  document.getElementById("btn-cancel").addEventListener("click", cancelSql);
  document.getElementById("btn-library").addEventListener("click", openLibrary);
  document.getElementById("library-close").addEventListener("click", () => {
    document.getElementById("library-dialog").close();
  });
  document.getElementById("bucket-new").addEventListener("click", createBucket);
  document.getElementById("query-save").addEventListener("click", saveWorksheet);
  document.getElementById("btn-commit").addEventListener("click", () => transaction("commit"));
  document.getElementById("btn-rollback").addEventListener("click", () => transaction("rollback"));
  document.getElementById("btn-clear").addEventListener("click", () => {
    document.getElementById("sql").value = "";
    document.getElementById("sql-output").replaceChildren();
  });
  document.getElementById("btn-sessions").addEventListener("click", () => loadMonitor("sessions"));
  document.getElementById("session-filter").addEventListener("input", (event) => {
    gridPrefs("sessions").filter = event.target.value;
    if (state.sessionReport) paintSessions(state.sessionReport);
  });
  document.getElementById("session-status").addEventListener("change", () => {
    if (state.sessionReport) paintSessions(state.sessionReport);
  });
  document.getElementById("ts-filter").addEventListener("input", (event) => {
    gridPrefs("tablespaces").filter = event.target.value;
    if (state.tablespaceReport) paintTablespaces(state.tablespaceReport);
  });
  document.getElementById("btn-tablespaces").addEventListener("click", () => loadMonitor("tablespaces"));
  document.getElementById("btn-runtime").addEventListener("click", loadRuntime);
  document.getElementById("sessions-refresh").addEventListener("change", armTimer);
  document.getElementById("tablespaces-refresh").addEventListener("change", armTimer);
  document.getElementById("runtime-refresh").addEventListener("change", armTimer);
  document.getElementById("tree-filter").addEventListener("input", renderTree);
  const editor = document.getElementById("sql");
  editor.addEventListener("keydown", (event) => {
    if (event.key === "Tab") {
      event.preventDefault();
      const start = editor.selectionStart;
      const end = editor.selectionEnd;
      editor.value = editor.value.slice(0, start) + "  " + editor.value.slice(end);
      editor.selectionStart = editor.selectionEnd = start + 2;
    }
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      runSql();
    }
  });
}

async function refreshAll() {
  const previous = state.workspace && state.workspace.active_connection_id;
  await Promise.all([loadWorkspace(), loadConnections()]);
  const current = state.workspace && state.workspace.active_connection_id;
  if (previous && previous !== current) {
    state.objects = {};
    state.expanded = new Set();
    state.owners = [];
  }
  if (state.workspace && state.workspace.connected) {
    try {
      await loadOwners();
    } catch (error) {
      state.owners = [];
      state.ownerNotice = error.message;
    }
  } else {
    state.owners = [];
  }
  renderTree();
  paintStatus();
  if (state.workspace && state.workspace.idle_rollback_note) {
    toast(state.workspace.idle_rollback_note);
  }
}

async function api(method, url, body) {
  const options = {
    method,
    credentials: "same-origin",
    headers: { "X-CSRF-Token": state.csrf },
  };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  const text = await response.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch (_err) {
      data = { detail: text.slice(0, 300) };
    }
  }
  if (response.status === 401) {
    window.location.assign("/login");
    throw new Error("Sign in required.");
  }
  if (!response.ok) {
    const error = new Error(messageOf(data));
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

function messageOf(data) {
  if (!data) return "The request failed.";
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail) && data.detail[0]) {
    return data.detail[0].msg || "Check the form and try again.";
  }
  return "The request failed.";
}

async function loadWorkspace() {
  state.workspace = await api("GET", "/api/workspace");
}

async function loadConnections() {
  const data = await api("GET", "/api/connections");
  state.connections = data.connections || [];
  const select = document.getElementById("conn-select");
  const open = (state.workspace && state.workspace.open_connections) || [];
  select.replaceChildren();
  select.appendChild(el(
    "option",
    { value: "" },
    open.length ? "Choose a working connection" : "No open connection"
  ));
  open.forEach((item) => {
    select.appendChild(el("option", { value: item.connection_id }, item.name + " (" + item.db_user + ")"));
  });
  if (state.workspace && state.workspace.active_connection_id) {
    select.value = state.workspace.active_connection_id;
  }
}

async function loadOwners() {
  const data = await api("GET", "/api/schema/owners");
  state.owners = data.owners || [];
  state.currentUser = data.current_user || "";
  state.ownerNotice = data.notice || "";
  if (state.currentUser) state.expanded.add("owner:" + state.currentUser);
}

function renderTree() {
  const root = document.getElementById("tree");
  root.replaceChildren();
  if (!state.connections.length) {
    root.appendChild(el("p", { class: "empty" }, "No saved connections. Create one to begin."));
    return;
  }
  const filter = document.getElementById("tree-filter").value.trim().toUpperCase();
  const openIds = new Set(
    ((state.workspace && state.workspace.open_connections) || []).map((item) => item.connection_id)
  );
  state.connections.forEach((item) => {
    const working = state.workspace && state.workspace.active_connection_id === item.id && state.workspace.connected;
    const connected = openIds.has(item.id);
    const row = el("div", {});
    const button = el("button", { class: "row" + (working ? " active" : ""), type: "button" });
    button.appendChild(el("span", { class: "dot" + (connected ? " on" : "") }));
    button.appendChild(el("span", {}, item.name));
    button.appendChild(el("span", { class: "muted" }, item.username));
    button.addEventListener("click", () => (connected ? activate(item.id) : connect(item.id, false)));
    row.appendChild(button);
    const actions = el("div", { class: "conn-actions" });
    if (!connected) {
      const open = el("button", { type: "button" }, "Connect");
      open.addEventListener("click", () => connect(item.id, false));
      actions.appendChild(open);
    } else {
      if (!working) {
        const use = el("button", { type: "button" }, "Use");
        use.addEventListener("click", () => activate(item.id));
        actions.appendChild(use);
      } else {
        actions.appendChild(el("span", { class: "pill" }, "Working"));
      }
      const close = el("button", { type: "button", class: "ghost" }, "Disconnect");
      close.addEventListener("click", () => disconnectOne(item.id, false));
      actions.appendChild(close);
    }
    const edit = el("button", { type: "button", class: "ghost" }, "Edit");
    edit.addEventListener("click", () => openDialog(item));
    const remove = el("button", { type: "button", class: "ghost" }, "Delete");
    remove.addEventListener("click", () => removeConnection(item));
    actions.appendChild(edit);
    actions.appendChild(remove);
    row.appendChild(actions);
    if (working) {
      if (state.ownerNotice) row.appendChild(el("p", { class: "fine" }, state.ownerNotice));
      const owners = state.owners.slice().sort((a, b) => {
        if (a === state.currentUser) return -1;
        if (b === state.currentUser) return 1;
        return a.localeCompare(b);
      });
      owners.forEach((owner) => row.appendChild(renderOwner(owner, filter)));
    } else if (connected) {
      row.appendChild(el("p", { class: "fine" }, "Connected. Click Use to make this the working connection."));
    } else {
      row.appendChild(el("p", { class: "fine" }, "Not connected. Click Connect to open it."));
    }
    root.appendChild(row);
  });
}

function renderOwner(owner, filter) {
  const wrap = el("div", { class: "owner" });
  const key = "owner:" + owner;
  const open = state.expanded.has(key);
  const button = el("button", { class: "row", type: "button" }, (open ? "▾ " : "▸ ") + owner);
  button.addEventListener("click", () => toggle(key));
  wrap.appendChild(button);
  if (open) TYPES.forEach((type) => wrap.appendChild(renderType(owner, type, filter)));
  return wrap;
}

function renderType(owner, type, filter) {
  const wrap = el("div", { class: "folder" });
  const key = "type:" + owner + ":" + type;
  const open = state.expanded.has(key);
  const button = el("button", { class: "row", type: "button" }, (open ? "▾ " : "▸ ") + type);
  button.addEventListener("click", () => toggleType(owner, type));
  wrap.appendChild(button);
  if (open) {
    const cached = state.objects[key];
    if (!cached) {
      wrap.appendChild(el("p", { class: "fine" }, "Loading…"));
    } else if (!cached.length) {
      wrap.appendChild(el("p", { class: "fine" }, "None"));
    } else {
      cached.forEach((object) => {
        if (filter && !String(object.object_name).toUpperCase().includes(filter)) return;
        const item = el("button", { class: "row obj", type: "button" }, object.object_name);
        if (object.status && object.status !== "VALID") item.appendChild(el("span", { class: "muted" }, " " + object.status));
        item.addEventListener("click", () => openObject(owner, object.object_name, type));
        wrap.appendChild(item);
      });
    }
  }
  return wrap;
}

function toggle(key) {
  if (state.expanded.has(key)) state.expanded.delete(key);
  else state.expanded.add(key);
  renderTree();
}

async function toggleType(owner, type) {
  const key = "type:" + owner + ":" + type;
  if (state.expanded.has(key)) {
    state.expanded.delete(key);
    renderTree();
    return;
  }
  state.expanded.add(key);
  renderTree();
  try {
    const filter = document.getElementById("tree-filter").value.trim();
    const data = await api(
      "GET",
      "/api/schema/objects?owner=" + encodeURIComponent(owner) +
        "&type=" + encodeURIComponent(type) +
        "&q=" + encodeURIComponent(filter)
    );
    state.objects[key] = data.objects || [];
    if (data.truncated) toast("The object list was truncated at 5000 names.", "error");
  } catch (error) {
    state.expanded.delete(key);
    toast(error.message, "error");
  }
  renderTree();
}

async function openObject(owner, name, type) {
  showTab("object");
  const view = document.getElementById("object-view");
  view.replaceChildren(el("p", {}, "Loading " + owner + "." + name + "…"));
  try {
    const data = await api(
      "GET",
      "/api/schema/object?owner=" + encodeURIComponent(owner) +
        "&name=" + encodeURIComponent(name) +
        "&type=" + encodeURIComponent(type)
    );
    renderObject(data);
  } catch (error) {
    view.replaceChildren(problemBox(error.message));
  }
}

function renderObject(data) {
  const view = document.getElementById("object-view");
  view.replaceChildren();
  if (data.notice) view.appendChild(el("p", { class: "banner" }, data.notice));
  if (data.kind === "source") {
    view.appendChild(el("h3", {}, data.owner + "." + data.name));
    const pre = el("pre", { class: "source" });
    pre.textContent = data.source || "";
    view.appendChild(pre);
    return;
  }
  if (data.kind === "meta") {
    view.appendChild(el("h3", {}, data.object.object_name + " (" + data.object.object_type + ")"));
    view.appendChild(el("p", {}, "Status: " + (data.object.status || "")));
    return;
  }
  view.appendChild(el("h3", {}, data.owner + "." + data.name + "  " + data.object_type));
  if (data.comment) view.appendChild(el("p", {}, data.comment));
  if (data.stats) {
    const stats = data.stats;
    view.appendChild(el(
      "p",
      { class: "fine" },
      "Tablespace " + (stats.tablespace_name || "—") +
        ". Optimizer row estimate " + (stats.num_rows ?? "unknown") +
        (stats.last_analyzed ? " analyzed " + stats.last_analyzed : "") +
        ". This is statistics, not a live count."
    ));
  }
  view.appendChild(el("h4", {}, "Columns"));
  view.appendChild(renderTable(
    "object-columns",
    ["column_id", "column_name", "data_type", "data_length", "nullable"],
    data.columns || []
  ));
  if (data.constraints && data.constraints.length) {
    view.appendChild(el("h4", {}, "Constraints"));
    view.appendChild(renderTable(
      "object-constraints",
      ["constraint_type", "constraint_name", "column_name", "position"],
      data.constraints
    ));
  }
  if (data.indexes && data.indexes.length) {
    view.appendChild(el("h4", {}, "Indexes"));
    view.appendChild(renderTable("object-indexes", ["index_name", "uniqueness", "status"], data.indexes));
  }
  view.appendChild(el("h4", {}, "Data"));
  view.appendChild(renderDataFilter(data));
  const note = el("p", { id: "data-note", class: "form-note" });
  view.appendChild(note);
  const grid = el("div", { id: "object-data" });
  grid.appendChild(renderMatrix(data.preview_columns || [], data.preview_rows || [], "object-data"));
  view.appendChild(grid);
}

function renderDataFilter(data) {
  const bar = el("div", { class: "filter-bar" });
  const columnLabel = el("label", {}, "Column");
  const column = el("select", { id: "filt-col" });
  column.appendChild(el("option", { value: "" }, "Any"));
  (data.columns || []).forEach((item) => {
    column.appendChild(el("option", { value: item.column_name }, item.column_name));
  });
  columnLabel.appendChild(column);
  const opLabel = el("label", {}, "Filter");
  const op = el("select", { id: "filt-op" });
  [
    ["", "No column filter"],
    ["contains", "contains"],
    ["eq", "equals"],
    ["ne", "not equal"],
    ["starts", "starts with"],
    ["null", "is null"],
    ["notnull", "is not null"],
  ].forEach(([value, label]) => op.appendChild(el("option", { value }, label)));
  opLabel.appendChild(op);
  const valueLabel = el("label", {}, "Value");
  valueLabel.appendChild(el("input", { id: "filt-value", placeholder: "KING" }));
  const whereLabel = el("label", { class: "where" }, "WHERE");
  whereLabel.appendChild(el("input", {
    id: "filt-where",
    placeholder: "deptno = 10 AND sal > 1000",
  }));
  const apply = el("button", { type: "button" }, "Apply");
  apply.addEventListener("click", () => applyDataFilter(data.owner, data.name));
  bar.append(columnLabel, opLabel, valueLabel, whereLabel, apply);
  return bar;
}

async function applyDataFilter(owner, name) {
  const note = document.getElementById("data-note");
  const grid = document.getElementById("object-data");
  note.className = "form-note";
  note.textContent = "Reading " + owner + "." + name + "…";
  try {
    const data = await api("POST", "/api/schema/rows", {
      owner,
      name,
      where: document.getElementById("filt-where").value,
      column: document.getElementById("filt-col").value,
      op: document.getElementById("filt-op").value,
      value: document.getElementById("filt-value").value,
      limit: 200,
    });
    grid.replaceChildren(renderMatrix(data.columns || [], data.rows || [], "object-data"));
    note.className = "form-note ok";
    note.textContent = data.rows.length + " row(s) from " + owner + "." + name +
      (data.truncated ? ". Stopped at the row limit." : ".");
  } catch (error) {
    note.className = "form-note bad";
    note.textContent = error.message;
  }
}

async function connect(id, confirm) {
  try {
    const data = await api("POST", "/api/connections/" + encodeURIComponent(id) + "/connect", {
      confirm_rollback: confirm,
    });
    if (data.rolled_back) toast("The previous session was rolled back.");
    state.objects = {};
    state.expanded = new Set();
    await refreshAll();
    setStatus("Connected as " + data.db_user + (data.db_name ? " @ " + data.db_name : ""));
  } catch (error) {
    if (error.data && error.data.code === "open_transaction" && !confirm) {
      if (window.confirm("The current session has an open transaction. Roll it back and switch?")) {
        return connect(id, true);
      }
      return;
    }
    toast(error.message, "error");
    setStatus(error.message);
    await refreshAll();
  }
}

async function activate(id) {
  try {
    const data = await api("POST", "/api/connections/" + encodeURIComponent(id) + "/activate", {});
    state.objects = {};
    state.expanded = new Set();
    await refreshAll();
    setStatus("Working connection: " + data.connection_name + " as " + data.db_user);
    const current = document.querySelector(".tab.active");
    if (current && current.dataset.tab === "runtime") loadRuntime();
    else if (current && (current.dataset.tab === "sessions" || current.dataset.tab === "tablespaces")) {
      loadMonitor(current.dataset.tab);
    }
  } catch (error) {
    toast(error.message, "error");
    setStatus(error.message);
    await refreshAll();
  }
}

async function disconnect(confirm) {
  const id = state.workspace && state.workspace.active_connection_id;
  if (!id) {
    setStatus("No working connection to disconnect.");
    return;
  }
  return disconnectOne(id, confirm);
}

async function disconnectOne(id, confirm) {
  try {
    const data = await api("POST", "/api/connections/" + encodeURIComponent(id) + "/disconnect", {
      confirm_rollback: confirm,
    });
    if (data.rolled_back) toast("The open transaction was rolled back.");
    state.owners = [];
    state.objects = {};
    await refreshAll();
    setStatus("Disconnected");
  } catch (error) {
    if (error.data && error.data.code === "open_transaction" && !confirm) {
      if (window.confirm("Roll back the open transaction and disconnect " + (error.data.connection || "this connection") + "?")) {
        return disconnectOne(id, true);
      }
      return;
    }
    toast(error.message, "error");
    setStatus(error.message);
  }
}

async function removeConnection(item) {
  if (!window.confirm("Delete the saved connection " + item.name + "?")) return;
  try {
    await api("DELETE", "/api/connections/" + encodeURIComponent(item.id), { confirm_rollback: false });
    await refreshAll();
  } catch (error) {
    if (error.data && error.data.code === "open_transaction") {
      if (window.confirm("A session has an open transaction. Roll it back and delete the connection?")) {
        await api("DELETE", "/api/connections/" + encodeURIComponent(item.id), { confirm_rollback: true });
        await refreshAll();
      }
      return;
    }
    toast(error.message, "error");
  }
}

function openDialog(item) {
  state.editing = item;
  const form = document.getElementById("conn-form");
  form.reset();
  document.getElementById("conn-title").textContent = item ? "Edit connection" : "New connection";
  document.getElementById("f-password").placeholder = item ? "Leave blank to keep the saved password" : "";
  document.getElementById("f-ssl").checked = true;
  setFormNote("");
  if (item) {
    document.getElementById("f-name").value = item.name;
    document.getElementById("f-user").value = item.username;
    document.getElementById("f-role").value = item.role || "";
    document.getElementById("f-read-only").checked = !!item.read_only;
    document.querySelector('input[name="mode"][value="' + item.mode + '"]').checked = true;
    document.getElementById("f-host").value = item.host || "";
    document.getElementById("f-port").value = item.port || (item.protocol === "tcps" ? 1522 : 1521);
    document.getElementById("f-service").value = item.service_name || "";
    document.getElementById("f-sid").value = item.sid || "";
    document.getElementById("f-protocol").value = item.protocol || "tcp";
    document.getElementById("f-wallet").value = item.wallet_dir || "";
    document.getElementById("f-alias").value = item.tns_alias || "";
    document.getElementById("f-dsn").value = item.connect_string || "";
    document.getElementById("f-ssl").checked = item.ssl_server_dn_match !== false;
  }
  syncModePanels();
  const warning = document.getElementById("wallet-warning");
  if (state.workspace && !state.workspace.wallet_root) {
    warning.textContent = "WALLET_ROOT is not set, so any directory this service can read is accepted. Set WALLET_ROOT in .env to confine wallet paths.";
  }
  document.getElementById("conn-dialog").showModal();
}

function closeDialog() {
  document.getElementById("conn-dialog").close();
}

function syncModePanels() {
  const mode = document.querySelector('input[name="mode"]:checked').value;
  document.querySelectorAll(".mode-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === "panel-" + mode);
  });
}

function formPayload() {
  const portRaw = document.getElementById("f-port").value;
  return {
    name: document.getElementById("f-name").value,
    username: document.getElementById("f-user").value,
    password: document.getElementById("f-password").value,
    role: document.getElementById("f-role").value,
    read_only: document.getElementById("f-read-only").checked,
    mode: document.querySelector('input[name="mode"]:checked').value,
    host: document.getElementById("f-host").value,
    port: portRaw === "" ? null : Number(portRaw),
    service_name: document.getElementById("f-service").value,
    sid: document.getElementById("f-sid").value,
    protocol: document.getElementById("f-protocol").value,
    wallet_dir: document.getElementById("f-wallet").value,
    wallet_password: document.getElementById("f-wallet-password").value,
    tns_alias: document.getElementById("f-alias").value,
    connect_string: document.getElementById("f-dsn").value,
    ssl_server_dn_match: document.getElementById("f-ssl").checked,
    confirm_rollback: false,
  };
}

async function saveConnection(event) {
  event.preventDefault();
  setFormNote("");
  const payload = formPayload();
  try {
    if (state.editing) {
      await saveUpdate(state.editing.id, payload, false);
    } else {
      await api("POST", "/api/connections", payload);
    }
    closeDialog();
    await refreshAll();
    toast("Connection saved.", "ok");
  } catch (err) {
    setFormNote(err.message, "bad");
  }
}

async function saveUpdate(id, payload, confirm) {
  payload.confirm_rollback = confirm;
  try {
    const data = await api("PUT", "/api/connections/" + encodeURIComponent(id), payload);
    if (data.rolled_back) toast("The open transaction was rolled back because the connection changed.");
    if (data.reconnected) toast("Connect again. The saved target changed.");
  } catch (error) {
    if (error.data && error.data.code === "open_transaction" && !confirm) {
      if (window.confirm("This connection has an open transaction. Roll it back and save?")) {
        return saveUpdate(id, payload, true);
      }
      throw new Error("Save cancelled.");
    }
    throw error;
  }
}

async function testConnection() {
  setFormNote("");
  const payload = formPayload();
  if (state.editing) payload.id = state.editing.id;
  try {
    const data = await api("POST", "/api/connections/test", payload);
    const parts = ["Connected as " + data.session_user];
    if (data.db_name) parts.push("database " + data.db_name);
    if (data.service_name) parts.push("service " + data.service_name);
    if (data.banner) parts.push(data.banner);
    setFormNote(parts.join(". "), "ok");
  } catch (err) {
    setFormNote(err.message, "bad");
  }
}

function setFormNote(message, kind) {
  const note = document.getElementById("conn-form-note");
  note.textContent = message || "";
  note.className = "form-note" + (kind ? " " + kind : "");
}

async function cancelSql() {
  try {
    const data = await api("POST", "/api/sql/cancel", {});
    toast(data.message || "Cancel sent.");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function loadClientPrefs() {
  const note = document.getElementById("pref-note");
  note.textContent = "";
  note.className = "form-note";
  try {
    const prefs = await api("GET", "/api/client-prefs");
    document.getElementById("pref-locking").checked = !!prefs.block_locking_sql;
    document.getElementById("pref-rollback").checked = !!prefs.rollback_script_on_error;
    document.getElementById("pref-max-open").value = prefs.max_open_connections;
    document.getElementById("pref-tag").checked = !!prefs.tag_sessions;
  } catch (error) {
    note.className = "form-note bad";
    note.textContent = error.message;
  }
}

async function saveClientPrefs() {
  const note = document.getElementById("pref-note");
  try {
    await api("PUT", "/api/client-prefs", {
      block_locking_sql: document.getElementById("pref-locking").checked,
      rollback_script_on_error: document.getElementById("pref-rollback").checked,
      max_open_connections: Number(document.getElementById("pref-max-open").value),
      tag_sessions: document.getElementById("pref-tag").checked,
    });
    note.className = "form-note ok";
    note.textContent = "Saved.";
  } catch (error) {
    note.className = "form-note bad";
    note.textContent = error.message;
  }
}

async function runSql(confirmDdl) {
  const editor = document.getElementById("sql");
  const selected = editor.value.slice(editor.selectionStart, editor.selectionEnd);
  const sql = selected.trim() ? selected : editor.value;
  if (!sql.trim()) {
    toast("Enter a SQL statement or PL/SQL block.", "error");
    return;
  }
  let binds = null;
  const rawBinds = document.getElementById("binds").value.trim();
  if (rawBinds) {
    try {
      binds = JSON.parse(rawBinds);
    } catch (_err) {
      toast("Binds must be a JSON object, for example {\"id\": 10}.", "error");
      return;
    }
    if (!binds || typeof binds !== "object" || Array.isArray(binds)) {
      toast("Binds must be a JSON object.", "error");
      return;
    }
  }
  const button = document.getElementById("btn-run");
  const cancel = document.getElementById("btn-cancel");
  let handedOff = false;
  button.disabled = true;
  cancel.disabled = false;
  setStatus("Running…");
  try {
    const data = await api("POST", "/api/sql/execute", {
      sql,
      max_rows: Number(document.getElementById("max-rows").value || 50),
      autocommit: document.getElementById("autocommit").checked,
      binds,
      confirm_ddl: !!confirmDdl,
    });
    renderSql(data);
    if (state.workspace) state.workspace.open_transaction = !!data.open_transaction;
    paintStatus();
    setStatus(data.rolled_back ? "Rolled back after an error" : data.ok ? "Completed" : "Stopped with an error");
  } catch (error) {
    if (error.data && error.data.code === "confirm_ddl" && !confirmDdl) {
      if (window.confirm(error.message)) {
        handedOff = true;
        return runSql(true);
      }
      setStatus("Not run");
      return;
    }
    toast(error.message, "error");
    setStatus(error.message);
  } finally {
    if (!handedOff) {
      button.disabled = false;
      cancel.disabled = true;
    }
  }
}

function renderSql(data) {
  const output = document.getElementById("sql-output");
  output.replaceChildren();
  (data.results || []).forEach((result) => {
    const block = el("div", { class: "result" });
    const title = "Statement " + result.index + (result.summary ? " — " + result.summary : "");
    block.appendChild(el("h4", {}, title));
    if (result.kind === "message") {
      block.appendChild(el("p", {}, result.text || ""));
    } else if (result.kind === "query") {
      block.appendChild(renderPagedQuery(result));
    } else {
      const affected = result.row_count === null || result.row_count === undefined ? "" : " · " + result.row_count + " row(s) affected";
      block.appendChild(el("p", { class: "fine" }, (result.kind || "done") + affected + " · " + result.elapsed_ms + " ms"));
    }
    output.appendChild(block);
  });
  if (data.dbms_output && data.dbms_output.length) {
    const pre = el("pre", { class: "output-text" });
    pre.textContent = data.dbms_output.join("\n");
    const block = el("div", { class: "result" });
    block.appendChild(el("h4", {}, "DBMS_OUTPUT"));
    block.appendChild(pre);
    output.appendChild(block);
  }
  if (data.error && data.error.message) {
    output.appendChild(problemBox(data.error.message));
  }
  if (data.rolled_back) {
    output.appendChild(el("p", { class: "banner" }, "The script failed, so this session was rolled back."));
  } else if (data.open_transaction) {
    output.appendChild(el("p", { class: "banner" }, "A transaction is open. Commit or roll it back."));
  }
  if ((data.results || []).some((result) => result.paging_closed)) {
    output.appendChild(el("p", { class: "fine" }, "A locking statement was not kept open. Only this page was read, and its cursor was closed."));
  }
}

async function transaction(kind) {
  try {
    const data = await api("POST", "/api/sql/" + kind, {});
    if (state.workspace) state.workspace.open_transaction = !!data.open_transaction;
    paintStatus();
    toast(kind === "commit" ? "Committed." : "Rolled back.");
  } catch (error) {
    toast(error.message, "error");
  }
}

function showTab(name) {
  document.querySelectorAll(".tab").forEach((button) => {
    const on = button.dataset.tab === name;
    button.classList.toggle("active", on);
    button.setAttribute("aria-selected", on ? "true" : "false");
  });
  document.querySelectorAll(".panel").forEach((panel) => {
    const on = panel.id === "panel-" + name;
    panel.classList.toggle("active", on);
    panel.hidden = !on;
  });
  if (name === "sessions" || name === "tablespaces") loadMonitor(name);
  if (name === "runtime") loadRuntime();
  armTimer();
}

function armTimer() {
  if (state.timer) clearInterval(state.timer);
  state.timer = null;
  const current = document.querySelector(".tab.active");
  if (!current) return;
  const name = current.dataset.tab;
  if (name !== "sessions" && name !== "tablespaces" && name !== "runtime") return;
  const box = document.getElementById(name + "-refresh");
  if (box && box.checked) {
    state.timer = setInterval(() => (name === "runtime" ? loadRuntime() : loadMonitor(name)), 15000);
  }
}

async function loadMonitor(name) {
  const notice = document.getElementById(name === "sessions" ? "session-notice" : "ts-notice");
  const summary = document.getElementById(name === "sessions" ? "session-summary" : "ts-summary");
  const table = document.getElementById(name === "sessions" ? "session-table" : "ts-table");
  try {
    const data = await api("GET", "/api/monitor/" + name);
    if (data.notice) {
      notice.hidden = false;
      notice.className = "banner";
      notice.textContent = (data.connection_name ? data.connection_name + " — " : "") + data.notice;
    } else {
      notice.hidden = true;
      notice.textContent = "";
    }
    summary.replaceChildren();
    const cards = name === "sessions"
      ? [
          ["Total", data.summary.total],
          ["Active", data.summary.active],
          ["Inactive", data.summary.inactive],
          ["Blocked", data.summary.blocked],
        ]
      : [
          ["Tablespaces", data.summary.count],
          ["≥ 85% used", data.summary.warning],
          ["≥ 95% used", data.summary.critical],
          ["Used MB", data.summary.used_mb ?? "—"],
        ];
    cards.forEach(([label, value]) => {
      const card = el("div", { class: "card" });
      card.appendChild(el("b", {}, value));
      card.appendChild(el("span", {}, label));
      summary.appendChild(card);
    });
    if (name === "sessions") {
      state.sessionReport = data;
      paintSessions(data);
    } else {
      state.tablespaceReport = data;
      paintTablespaces(data);
    }
  } catch (error) {
    notice.hidden = false;
    notice.className = "problem";
    notice.textContent = error.message;
    summary.replaceChildren();
    table.replaceChildren();
    if (name === "sessions") document.getElementById("session-detail").replaceChildren();
  }
}

const SESSION_COLUMNS = [
  "sid", "serial", "username", "status", "osuser", "machine", "program", "module",
  "action", "sql_id", "sql_text", "event", "blocking_session", "last_call_et", "logon_time",
];

const SESSION_GROUPS = [
  ["Session", ["sid", "serial", "username", "status", "schema_name", "session_type", "server", "service_name", "consumer_group", "con_id", "logon_time", "last_call_et"]],
  ["Client", ["osuser", "machine", "terminal", "program", "port", "process", "module", "action", "client_info", "client_identifier"]],
  ["Current SQL", ["sql_id", "sql_child_number", "sql_exec_start", "sql_text"]],
  ["Previous SQL", ["prev_sql_id", "prev_child_number", "prev_exec_start", "prev_sql_text"]],
  ["Wait", ["event", "wait_class", "state", "seconds_in_wait", "blocking_session", "blocking_session_status", "final_blocking_session"]],
];

function paintSessions(data) {
  const table = document.getElementById("session-table");
  const status = document.getElementById("session-status").value;
  const rows = (data.rows || []).filter((row) => {
    if (!status) return true;
    if (status === "BLOCKED") return row.blocking_session !== null && row.blocking_session !== undefined && String(row.blocking_session) !== "";
    return String(row.status || "").toUpperCase() === status;
  });
  const columns = columnsPresent(rows, SESSION_COLUMNS);
  table.replaceChildren(renderGrid("sessions", columns, rows, {
    externalFilter: true,
    countId: "session-count",
    clip: ["sql_text", "prev_sql_text"],
    rowClass: (row) => {
      const classes = [];
      if (row.blocking_session !== null && row.blocking_session !== undefined && String(row.blocking_session) !== "") classes.push("bad");
      if (data.current_sid !== null && data.current_sid !== undefined && String(row.sid) === String(data.current_sid)) classes.push("self");
      if (sessionKey(row) === state.sessionFocus) classes.push("picked");
      return classes.join(" ");
    },
    onSelect: (row) => {
      state.sessionFocus = sessionKey(row);
      paintSessionDetail(data, row);
      table.querySelectorAll("tbody tr").forEach((tr) => tr.classList.remove("picked"));
      const picked = table.querySelector('tr[data-key="' + cssEscape(state.sessionFocus) + '"]');
      if (picked) picked.classList.add("picked");
    },
    action: (row) => sessionKillButton(data, row),
  }));
  const selected = rows.find((row) => sessionKey(row) === state.sessionFocus);
  paintSessionDetail(data, selected || null);
}

function paintSessionDetail(data, row) {
  const host = document.getElementById("session-detail");
  host.replaceChildren();
  if (!row) {
    host.appendChild(el("p", { class: "fine" }, "Select a session to see the client, the SQL it is running, and the wait."));
    return;
  }
  const mine = data.current_sid !== null && data.current_sid !== undefined && String(row.sid) === String(data.current_sid);
  if (mine) host.appendChild(el("p", { class: "banner wide" }, "This is the working connection."));
  SESSION_GROUPS.forEach(([title, keys]) => {
    const present = keys.filter((key) => Object.prototype.hasOwnProperty.call(row, key));
    const block = el("section", { class: title.indexOf("SQL") >= 0 ? "wide" : "" });
    block.appendChild(el("h4", {}, title));
    if (title === "Current SQL" && row.sql_notice) {
      block.appendChild(el("p", { class: "fine" }, row.sql_notice));
    }
    if (title === "Current SQL" && !Object.prototype.hasOwnProperty.call(row, "sql_text") && !row.sql_notice) {
      block.appendChild(el("p", { class: "fine" }, "Loading statement text…"));
    }
    if (!present.length) {
      block.appendChild(el("p", { class: "fine" }, "No columns in this group."));
      host.appendChild(block);
      return;
    }
    const textKey = present.find((key) => key === "sql_text" || key === "prev_sql_text");
    const facts = present.filter((key) => key !== "sql_text" && key !== "prev_sql_text");
    if (facts.length) {
      const list = document.createElement("dl");
      list.className = "kv";
      facts.forEach((key) => {
        list.appendChild(el("dt", {}, headerLabel(key)));
        list.appendChild(el("dd", {}, displayValue(row[key])));
      });
      block.appendChild(list);
    }
    if (textKey) {
      const pre = el("pre");
      pre.textContent = row[textKey] === null || row[textKey] === undefined || row[textKey] === "" ? "No statement text." : String(row[textKey]);
      block.appendChild(pre);
    }
    host.appendChild(block);
  });
  if (row.serial !== undefined && row.serial !== null && row.serial !== "" && !Object.prototype.hasOwnProperty.call(row, "sql_text")) {
    loadSessionSql(row);
  }
}

async function loadSessionSql(row) {
  const key = sessionKey(row);
  try {
    const extra = await api(
      "GET",
      "/api/monitor/sessions/sql?sid=" + encodeURIComponent(row.sid) + "&serial=" + encodeURIComponent(row.serial)
    );
    if (state.sessionFocus !== key || !state.sessionReport) return;
    const current = (state.sessionReport.rows || []).find((item) => sessionKey(item) === key);
    if (!current) return;
    current.sql_text = extra.sql_text === undefined ? null : extra.sql_text;
    current.prev_sql_text = extra.prev_sql_text === undefined ? null : extra.prev_sql_text;
    ["sql_id", "prev_sql_id", "sql_exec_start", "prev_exec_start", "sql_child_number", "prev_child_number"].forEach((field) => {
      if (extra[field] !== undefined && extra[field] !== null) current[field] = extra[field];
    });
    current.sql_notice = extra.notice || "";
    paintSessionDetail(state.sessionReport, current);
  } catch (error) {
    if (state.sessionFocus !== key || !state.sessionReport) return;
    const current = (state.sessionReport.rows || []).find((item) => sessionKey(item) === key);
    if (!current || Object.prototype.hasOwnProperty.call(current, "sql_text")) return;
    current.sql_text = null;
    current.sql_notice = error.message;
    paintSessionDetail(state.sessionReport, current);
  }
}

function sessionKillButton(data, row) {
  if (row.sid === null || row.sid === undefined || row.serial === null || row.serial === undefined || row.serial === "") {
    return null;
  }
  const button = el("button", { type: "button", class: "danger" }, "Kill");
  const readOnly = state.workspace && state.workspace.read_only;
  if (readOnly) {
    button.disabled = true;
    button.title = "This connection is read only.";
    return button;
  }
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    killSession(data, row);
  });
  return button;
}

async function killSession(data, row) {
  const who = (row.username || "session") + " " + row.sid + "," + row.serial;
  const where = [row.machine, row.program].filter(Boolean).join(" · ");
  const mine = data.current_sid !== null && String(row.sid) === String(data.current_sid);
  let message = "Kill " + who + (where ? " (" + where + ")" : "") + "? Oracle ends it immediately and rolls back its uncommitted work.";
  if (mine) message = "This is the working connection. " + message + " This viewer will lose that database session.";
  if (!window.confirm(message)) return;
  try {
    const result = await api("POST", "/api/monitor/sessions/kill", {
      sid: Number(row.sid),
      serial: Number(row.serial),
    });
    toast(result.message || "Kill requested.", "ok");
    await loadMonitor("sessions");
  } catch (error) {
    toast(error.message, "error");
  }
}

function paintTablespaces(data) {
  const columns = ["tablespace_name", "contents", "status", "used_mb", "free_mb", "total_mb", "pct_used", "max_mb", "files"];
  document.getElementById("ts-table").replaceChildren(renderGrid("tablespaces", columns, data.rows || [], {
    externalFilter: true,
    countId: "ts-count",
    rowClass: (row) => {
      const pct = Number(row.pct_used);
      if (Number.isNaN(pct)) return "";
      if (pct >= 95) return "bad";
      if (pct >= 85) return "warn";
      return "";
    },
    prefix: (row) => {
      const pct = row.pct_used === null || row.pct_used === undefined ? null : Number(row.pct_used);
      const cellNode = document.createElement("td");
      if (pct !== null && !Number.isNaN(pct)) {
        const meter = document.createElement("progress");
        meter.max = 100;
        meter.value = Math.max(0, Math.min(100, pct));
        if (pct >= 95) meter.className = "bad";
        else if (pct >= 85) meter.className = "warn";
        cellNode.appendChild(meter);
      }
      return cellNode;
    },
  }));
}

function sessionKey(row) {
  return String(row.sid) + ":" + String(row.serial);
}

function cssEscape(value) {
  if (window.CSS && CSS.escape) return CSS.escape(value);
  return String(value).replace(/"/g, "");
}

async function loadRuntime() {
  const body = document.getElementById("runtime-body");
  const label = document.getElementById("runtime-connection");
  try {
    const data = await api("GET", "/api/monitor/runtime");
    label.textContent = data.connection_name
      ? "Working connection: " + data.connection_name + " (" + data.db_user + ")"
      : "";
    body.replaceChildren();
    (data.sections || []).forEach((section) => {
      const block = el("section", { class: "section-block" });
      block.appendChild(el("h3", {}, section.title));
      if (section.notice) block.appendChild(el("p", { class: "banner" }, section.notice));
      const rows = section.rows || [];
      const columns = rows.length ? Object.keys(rows[0]) : [];
      block.appendChild(renderMatrix(
        columns,
        rows.map((row) => columns.map((column) => row[column])),
        "runtime-" + section.id
      ));
      body.appendChild(block);
    });
  } catch (error) {
    label.textContent = "";
    body.replaceChildren(problemBox(error.message));
  }
}

function problemBox(message) {
  const box = el("div", { class: "problem" });
  const split = message.indexOf(": ");
  if (split > 0 && split < 140) {
    box.appendChild(el("strong", {}, message.slice(0, split)));
    box.appendChild(el("p", {}, message.slice(split + 2)));
  } else {
    box.appendChild(el("p", {}, message));
  }
  return box;
}

function renderTable(id, columns, rows) {
  return renderGrid(id, columns, rows || [], {});
}

function renderPagedQuery(result) {
  const wrap = el("div");
  const gridHost = el("div");
  const note = el("p", { class: "fine" });
  const pager = el("div", { class: "pager" });
  const prev = el("button", { type: "button" }, "Previous");
  const next = el("button", { type: "button" }, "Next");
  pager.append(prev, next);
  const page = {
    columns: result.columns || [],
    pages: [result.rows || []],
    starts: [result.row_start || (result.rows && result.rows.length ? 1 : 0)],
    index: 0,
    hasMore: !!result.has_more,
    token: result.page_token || null,
    pageSize: result.page_size || Number(document.getElementById("max-rows").value || 50),
    elapsed: result.elapsed_ms,
  };
  function paint() {
    const rows = page.pages[page.index] || [];
    const start = page.starts[page.index] || 0;
    gridHost.replaceChildren(renderMatrix(page.columns, rows, "worksheet-" + result.index));
    const end = rows.length ? start + rows.length - 1 : start;
    let text = rows.length ? "Rows " + start + "–" + end : "No rows";
    text += " · page size " + page.pageSize + " · first fetch " + page.elapsed + " ms. Filter and sort apply to this page.";
    if (page.index === page.pages.length - 1 && page.hasMore) {
      text += ". More rows remain. Next reads only the following page. The table is not counted.";
    } else if (page.index === page.pages.length - 1) {
      text += ". End of this result.";
    }
    note.textContent = text;
    prev.disabled = page.index === 0;
    next.disabled = page.index >= page.pages.length - 1 && !page.hasMore;
  }
  prev.addEventListener("click", () => {
    if (page.index > 0) {
      page.index -= 1;
      paint();
    }
  });
  next.addEventListener("click", async () => {
    if (page.index < page.pages.length - 1) {
      page.index += 1;
      paint();
      return;
    }
    if (!page.token) return;
    next.disabled = true;
    try {
      const data = await api("POST", "/api/sql/page", { token: page.token });
      page.pages.push(data.rows || []);
      page.starts.push(data.row_start || 1);
      page.hasMore = !!data.has_more;
      page.token = data.page_token || null;
      page.index += 1;
      paint();
    } catch (error) {
      toast(error.message, "error");
      paint();
    }
  });
  paint();
  wrap.append(gridHost, note, pager);
  return wrap;
}

async function openLibrary() {
  document.getElementById("library-dialog").showModal();
  try {
    await loadLibrary();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function loadLibrary() {
  const data = await api("GET", "/api/library");
  state.library = data.buckets || [];
  if (!state.library.some((bucket) => bucket.id === state.libraryBucket)) {
    state.libraryBucket = state.library.length ? state.library[0].id : null;
  }
  paintLibrary();
}

function paintLibrary() {
  const buckets = document.getElementById("bucket-list");
  const queries = document.getElementById("query-list");
  const title = document.getElementById("bucket-title");
  buckets.replaceChildren();
  queries.replaceChildren();
  const current = (state.library || []).find((bucket) => bucket.id === state.libraryBucket) || null;
  title.textContent = current ? current.name : "Queries";
  (state.library || []).forEach((bucket) => {
    const row = el("div", { class: "lib-item" });
    const open = el("button", { type: "button", class: "name" }, bucket.name);
    if (bucket.id === state.libraryBucket) open.classList.add("primary");
    open.addEventListener("click", () => {
      state.libraryBucket = bucket.id;
      paintLibrary();
    });
    const rename = el("button", { type: "button", class: "ghost" }, "Rename");
    rename.addEventListener("click", () => renameBucket(bucket));
    const remove = el("button", { type: "button", class: "ghost" }, "Delete");
    remove.addEventListener("click", () => deleteBucket(bucket));
    row.append(open, rename, remove);
    buckets.appendChild(row);
  });
  if (!current) {
    queries.appendChild(el("p", { class: "fine" }, "Add a bucket, for example Admin queries or Weekly report."));
    return;
  }
  if (!current.queries.length) {
    queries.appendChild(el("p", { class: "fine" }, "This bucket is empty. Save the worksheet into it."));
  }
  current.queries.forEach((query) => {
    const row = el("div", { class: "lib-item" });
    const open = el("button", { type: "button", class: "name" }, query.title);
    open.addEventListener("click", () => {
      document.getElementById("sql").value = query.sql;
      document.getElementById("library-dialog").close();
      toast("Loaded " + query.title + ". Run it when you are ready.");
    });
    const replace = el("button", { type: "button", class: "ghost" }, "Replace");
    replace.addEventListener("click", () => replaceQuery(query));
    const remove = el("button", { type: "button", class: "ghost" }, "Delete");
    remove.addEventListener("click", () => deleteQuery(query));
    row.append(open, replace, remove);
    queries.appendChild(row);
  });
}

async function createBucket() {
  const name = window.prompt("Bucket name", "Admin queries");
  if (!name) return;
  try {
    const bucket = await api("POST", "/api/library/buckets", { name });
    state.libraryBucket = bucket.id;
    await loadLibrary();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function renameBucket(bucket) {
  const name = window.prompt("Rename bucket", bucket.name);
  if (!name || name === bucket.name) return;
  try {
    await api("PUT", "/api/library/buckets/" + encodeURIComponent(bucket.id), { name });
    await loadLibrary();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function deleteBucket(bucket) {
  if (!window.confirm("Delete the bucket “" + bucket.name + "” and every query in it?")) return;
  try {
    await api("DELETE", "/api/library/buckets/" + encodeURIComponent(bucket.id));
    if (state.libraryBucket === bucket.id) state.libraryBucket = null;
    await loadLibrary();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function saveWorksheet() {
  if (!state.libraryBucket) {
    toast("Create a bucket first.", "error");
    return;
  }
  const sql = document.getElementById("sql").value.trim();
  if (!sql) {
    toast("The worksheet is empty.", "error");
    return;
  }
  const title = window.prompt("Name this query");
  if (!title) return;
  try {
    await api("POST", "/api/library/buckets/" + encodeURIComponent(state.libraryBucket) + "/queries", { title, sql });
    await loadLibrary();
    toast("Saved.");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function replaceQuery(query) {
  const sql = document.getElementById("sql").value.trim();
  if (!sql) {
    toast("The worksheet is empty.", "error");
    return;
  }
  const title = window.prompt("Query title", query.title);
  if (!title) return;
  try {
    await api("PUT", "/api/library/queries/" + encodeURIComponent(query.id), { title, sql });
    await loadLibrary();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function deleteQuery(query) {
  if (!window.confirm("Delete “" + query.title + "”?")) return;
  try {
    await api("DELETE", "/api/library/queries/" + encodeURIComponent(query.id));
    await loadLibrary();
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderMatrix(columns, rows, gridId) {
  const objects = (rows || []).map((row) => {
    const item = {};
    columns.forEach((column, index) => {
      item[column] = Array.isArray(row) ? row[index] : row[column];
    });
    return item;
  });
  return renderGrid(gridId || "grid", columns, objects, {});
}

function renderGrid(id, columns, rows, options) {
  const prefs = gridPrefs(id);
  const wrap = document.createElement("div");
  wrap.className = "grid-wrap";
  const host = document.createElement("div");
  let count = null;
  if (!options.externalFilter) {
    const tools = el("div", { class: "grid-tools" });
    const input = el("input", {
      type: "search",
      placeholder: "Filter these rows",
      value: prefs.filter,
      "aria-label": "Filter rows",
    });
    count = el("span", { class: "fine" });
    input.addEventListener("input", () => {
      prefs.filter = input.value;
      paint();
    });
    tools.append(input, count);
    wrap.appendChild(tools);
  }
  wrap.appendChild(host);

  function paint() {
    const query = String(prefs.filter || "").trim().toLowerCase();
    let view = rows.filter((row) => {
      if (!query) return true;
      return Object.keys(row).some((key) => String(row[key] ?? "").toLowerCase().includes(query));
    });
    if (prefs.sort) {
      const key = prefs.sort;
      const dir = prefs.dir === -1 ? -1 : 1;
      view = view.slice().sort((left, right) => compareCells(left[key], right[key]) * dir);
    }
    if (count) count.textContent = view.length + " of " + rows.length;
    if (options.countId) {
      const outside = document.getElementById(options.countId);
      if (outside) outside.textContent = view.length + " of " + rows.length;
    }
    options.prefs = prefs;
    host.replaceChildren(gridTable(columns, view, rows.length, options, paint));
  }
  paint();
  return wrap;
}

function gridTable(columns, rows, total, options, repaint) {
  const prefs = options.prefs;
  const table = document.createElement("table");
  table.className = "data";
  const head = document.createElement("tr");
  if (options.prefix) head.appendChild(el("th", {}, ""));
  if (options.action) head.appendChild(el("th", {}, ""));
  columns.forEach((column) => {
    const th = document.createElement("th");
    const button = el("button", { type: "button", class: "sort" }, headerLabel(column));
    if (prefs.sort === column) button.appendChild(document.createTextNode(prefs.dir === -1 ? " ▼" : " ▲"));
    button.addEventListener("click", () => {
      if (prefs.sort === column) prefs.dir = prefs.dir === -1 ? 1 : -1;
      else {
        prefs.sort = column;
        prefs.dir = 1;
      }
      repaint();
    });
    th.appendChild(button);
    head.appendChild(th);
  });
  const thead = document.createElement("thead");
  thead.appendChild(head);
  table.appendChild(thead);
  const body = document.createElement("tbody");
  const span = columns.length + (options.prefix ? 1 : 0) + (options.action ? 1 : 0);
  if (!rows.length) {
    const tr = document.createElement("tr");
    tr.appendChild(el("td", { colspan: String(Math.max(span, 1)) }, total ? "No rows match this filter." : "No rows"));
    body.appendChild(tr);
  }
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    if (options.onSelect) {
      tr.dataset.key = sessionKey(row);
      tr.addEventListener("click", () => options.onSelect(row));
    }
    const className = options.rowClass ? options.rowClass(row) : "";
    if (className) tr.className = className;
    if (options.prefix) tr.appendChild(options.prefix(row));
    if (options.action) {
      const actionCell = document.createElement("td");
      const control = options.action(row);
      if (control) actionCell.appendChild(control);
      tr.appendChild(actionCell);
    }
    columns.forEach((column) => {
      const td = cell(row[column]);
      if (options.clip && options.clip.indexOf(column) >= 0 && row[column] !== null && row[column] !== undefined) {
        td.className = "clip";
        td.title = String(row[column]);
      }
      tr.appendChild(td);
    });
    body.appendChild(tr);
  });
  table.appendChild(body);
  return table;
}

function gridPrefs(id) {
  if (!state.gridState[id]) state.gridState[id] = { filter: "", sort: null, dir: 1 };
  return state.gridState[id];
}

function columnsPresent(rows, preferred) {
  if (!rows.length) return preferred.slice();
  const keys = new Set();
  rows.forEach((row) => Object.keys(row).forEach((key) => keys.add(key)));
  const visible = preferred.filter((key) => keys.has(key));
  return visible.length ? visible : Array.from(keys);
}

function headerLabel(column) {
  return HEADER_LABELS[column] || column;
}

function displayValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

function compareCells(left, right) {
  const empty = (value) => value === null || value === undefined || value === "";
  if (empty(left) && empty(right)) return 0;
  if (empty(left)) return 1;
  if (empty(right)) return -1;
  const leftText = String(left).trim();
  const rightText = String(right).trim();
  if (/^-?\d+(\.\d+)?$/.test(leftText) && /^-?\d+(\.\d+)?$/.test(rightText)) {
    return Number(leftText) - Number(rightText);
  }
  return leftText.localeCompare(rightText, undefined, { numeric: true, sensitivity: "base" });
}

const HEADER_LABELS = {
  sid: "SID",
  serial: "Serial#",
  username: "User",
  status: "Status",
  schema_name: "Schema",
  osuser: "OS user",
  machine: "Machine",
  terminal: "Terminal",
  program: "Program",
  module: "Module",
  action: "Action",
  client_info: "Client info",
  client_identifier: "Client identifier",
  process: "Process",
  port: "Port",
  session_type: "Type",
  server: "Server",
  service_name: "Service",
  consumer_group: "Consumer group",
  con_id: "Container",
  logon_time: "Logon time",
  last_call_et: "Last call (s)",
  sql_id: "SQL ID",
  sql_child_number: "Child",
  sql_exec_start: "SQL start",
  prev_sql_id: "Previous SQL ID",
  prev_child_number: "Previous child",
  prev_exec_start: "Previous start",
  event: "Event",
  wait_class: "Wait class",
  state: "State",
  seconds_in_wait: "Seconds in wait",
  blocking_session: "Blocking session",
  blocking_session_status: "Blocking status",
  final_blocking_session: "Final blocking session",
  sql_text: "SQL",
  prev_sql_text: "Previous SQL",
  tablespace_name: "Tablespace",
  contents: "Contents",
  total_mb: "Total MB",
  used_mb: "Used MB",
  free_mb: "Free MB",
  pct_used: "% used",
  max_mb: "Max MB",
  files: "Files",
  column_id: "ID",
  column_name: "Column",
  data_type: "Type",
  data_length: "Length",
  nullable: "Nullable",
  constraint_type: "Type",
  constraint_name: "Constraint",
  position: "Position",
  index_name: "Index",
  uniqueness: "Uniqueness",
};

function cell(value) {
  const td = document.createElement("td");
  if (value === null || value === undefined) {
    td.appendChild(el("span", { class: "null" }, "NULL"));
  } else {
    td.textContent = String(value);
  }
  return td;
}

function paintStatus() {
  const meta = document.getElementById("conn-meta");
  const tx = document.getElementById("tx-text");
  const workspace = state.workspace;
  if (!workspace || !workspace.connected) {
    meta.textContent = "No working connection";
    tx.textContent = "";
    return;
  }
  meta.textContent = "";
  meta.appendChild(document.createTextNode(
    workspace.db_user + (workspace.db_name ? " @ " + workspace.db_name : "") +
      (workspace.connection_name ? " · " + workspace.connection_name : "")
  ));
  if (workspace.read_only) meta.appendChild(el("span", { class: "pill" }, "read only"));
  tx.textContent = workspace.open_transaction ? "Transaction open" : "";
}

function setStatus(text) {
  document.getElementById("status-text").textContent = text;
}

let toastTimer = null;
function toast(message, kind) {
  const node = document.getElementById("toast");
  node.textContent = message;
  const tone = kind === "error" ? " error" : kind === "ok" ? " ok" : "";
  node.className = "toast show" + tone;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.className = "toast"; }, 5000);
}

async function logout() {
  try {
    await api("POST", "/api/logout", {});
  } catch (_err) {
    /* The session is cleared on the way out. */
  }
  window.location.assign("/login");
}

function el(tag, attrs, text) {
  const node = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([key, value]) => {
    if (key === "class") node.className = value;
    else node.setAttribute(key, value);
  });
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
