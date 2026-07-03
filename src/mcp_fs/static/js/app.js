// mcp-fs file manager. Talks to the /api/fs data plane (session cookie is sent
// automatically). One project (mount) and one current path at a time.
(function () {
  "use strict";

  const projectSel = document.getElementById("project");
  if (!projectSel) return; // no accessible projects

  const filesBody = document.getElementById("files");
  const breadcrumb = document.getElementById("breadcrumb");
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("fileInput");
  const folderInput = document.getElementById("folderInput");

  const state = { mount: projectSel.value, path: "/" };

  const enc = encodeURIComponent;
  const joinPath = (base, name) => (base === "/" ? "/" + name : base + "/" + name);

  function toast(message, type) {
    const el = document.createElement("div");
    el.className = "toast" + (type ? " " + type : "");
    el.textContent = message;
    document.getElementById("toasts").appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  function humanSize(n) {
    if (n < 1024) return n + " B";
    const units = ["KB", "MB", "GB", "TB"];
    let v = n / 1024, i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return v.toFixed(v < 10 ? 1 : 0) + " " + units[i];
  }

  async function api(path, opts) {
    const res = await fetch("/api/fs/" + state.mount + path, opts);
    if (!res.ok) {
      let detail = res.status + "";
      try { detail = (await res.json()).detail || detail; } catch (e) { /* ignore */ }
      throw new Error(detail);
    }
    return res;
  }

  function renderBreadcrumb() {
    breadcrumb.innerHTML = "";
    const parts = state.path.split("/").filter(Boolean);
    const mkLink = (label, target) => {
      const a = document.createElement("a");
      a.textContent = label;
      a.onclick = () => navigate(target);
      return a;
    };
    breadcrumb.appendChild(mkLink(state.mount, "/"));
    let acc = "";
    for (const p of parts) {
      acc += "/" + p;
      const sep = document.createElement("span");
      sep.className = "sep";
      sep.textContent = "/";
      breadcrumb.appendChild(sep);
      breadcrumb.appendChild(mkLink(p, acc));
    }
  }

  function iconBtn(symbol, title, handler) {
    const b = document.createElement("button");
    b.className = "btn-icon";
    b.title = title;
    b.textContent = symbol;
    b.onclick = handler;
    return b;
  }

  async function render() {
    renderBreadcrumb();
    filesBody.innerHTML = "";
    let data;
    try {
      data = await (await api("/list?path=" + enc(state.path))).json();
    } catch (e) {
      toast("List failed: " + e.message, "error");
      return;
    }
    for (const entry of data.entries) {
      const isDir = entry.kind === "dir";
      const full = joinPath(state.path, entry.name);
      const tr = document.createElement("tr");

      const nameTd = document.createElement("td");
      const cell = document.createElement("div");
      cell.className = "name-cell" + (isDir ? " dir" : "");
      cell.innerHTML = '<span class="kind">' + (isDir ? "\u{1F4C1}" : "\u{1F4C4}") + "</span>";
      const label = document.createElement("span");
      label.className = "label";
      label.textContent = entry.name;
      cell.appendChild(label);
      if (isDir) cell.onclick = () => navigate(full);
      else cell.onclick = () => download(full);
      nameTd.appendChild(cell);

      const sizeTd = document.createElement("td");
      sizeTd.className = "col-size";
      sizeTd.textContent = isDir ? "" : humanSize(entry.size);

      const actionsTd = document.createElement("td");
      actionsTd.className = "col-actions";
      const actions = document.createElement("div");
      actions.className = "row-actions";
      if (isDir) actions.appendChild(iconBtn("⬇", "Download as zip", () => zip(full)));
      else actions.appendChild(iconBtn("⬇", "Download", () => download(full)));
      actions.appendChild(iconBtn("✥", "Move / rename", () => move(full)));
      actions.appendChild(iconBtn("✕", "Delete", () => del(full, entry.name)));
      actionsTd.appendChild(actions);

      tr.appendChild(nameTd);
      tr.appendChild(sizeTd);
      tr.appendChild(actionsTd);
      filesBody.appendChild(tr);
    }
  }

  function navigate(path) { state.path = path || "/"; render(); }

  function download(path) { window.location = "/api/fs/" + state.mount + "/download?path=" + enc(path); }
  function zip(path) { window.location = "/api/fs/" + state.mount + "/download-zip?path=" + enc(path); }

  async function mkdir() {
    const name = prompt("New folder name:");
    if (!name) return;
    try {
      await api("/mkdir", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: joinPath(state.path, name) }) });
      toast("Folder created", "success");
      render();
    } catch (e) { toast("Create failed: " + e.message, "error"); }
  }

  async function move(path) {
    const dest = prompt("Move / rename to (full path):", path);
    if (!dest || dest === path) return;
    try {
      await api("/move", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: path, destination: dest }) });
      toast("Moved", "success");
      render();
    } catch (e) { toast("Move failed: " + e.message, "error"); }
  }

  async function del(path, name) {
    if (!confirm("Delete " + name + " ?")) return;
    try {
      await api("/delete", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }) });
      toast("Deleted", "success");
      render();
    } catch (e) { toast("Delete failed: " + e.message, "error"); }
  }

  async function uploadFiles(fileList, relPaths) {
    if (!fileList || !fileList.length) return;
    const form = new FormData();
    form.append("directory", state.path);
    for (let i = 0; i < fileList.length; i++) {
      form.append("files", fileList[i]);
      form.append("paths", relPaths && relPaths[i] ? relPaths[i] : "");
    }
    try {
      const res = await (await api("/upload", { method: "POST", body: form })).json();
      toast("Uploaded " + res.count + " file(s)", "success");
      render();
    } catch (e) { toast("Upload failed: " + e.message, "error"); }
  }

  // -- wiring ----------------------------------------------------------------
  projectSel.onchange = () => { state.mount = projectSel.value; navigate("/"); };
  document.getElementById("btn-mkdir").onclick = mkdir;
  document.getElementById("btn-upload").onclick = () => fileInput.click();
  document.getElementById("btn-upload-folder").onclick = () => folderInput.click();
  document.getElementById("btn-zip").onclick = () => zip(state.path);

  fileInput.onchange = () => { uploadFiles(fileInput.files); fileInput.value = ""; };
  folderInput.onchange = () => {
    const paths = Array.from(folderInput.files).map((f) => f.webkitRelativePath || f.name);
    uploadFiles(folderInput.files, paths);
    folderInput.value = "";
  };

  ["dragenter", "dragover"].forEach((ev) =>
    dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add("dragover"); }));
  ["dragleave", "drop"].forEach((ev) =>
    dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.remove("dragover"); }));
  dropzone.addEventListener("drop", (e) => { if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files); });

  render();
})();
