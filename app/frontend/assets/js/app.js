const API_ROOT = "/api/v1";
const TOKEN_KEY = "docsflow.access_token";
const app = document.querySelector("#app");
const navItems = document.querySelector("#nav-items");

function getToken() {
  return sessionStorage.getItem(TOKEN_KEY);
}

function setToken(token) {
  sessionStorage.setItem(TOKEN_KEY, token);
}

function logout() {
  sessionStorage.removeItem(TOKEN_KEY);
  window.location.assign("/login");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(
    new Date(`${value}T00:00:00`),
  );
}

function formatAmount(amount, currency) {
  if (amount === null || amount === undefined) return "—";
  return `${amount} ${currency || ""}`.trim();
}

function formatFileSize(bytes) {
  if (!Number.isFinite(Number(bytes))) return "—";
  return `${(Number(bytes) / (1024 * 1024)).toFixed(2)} MB`;
}

function statusBadge(status) {
  const classes = {
    uploaded: "text-bg-secondary",
    processing: "text-bg-info",
    completed: "text-bg-success",
    failed: "text-bg-danger",
    draft: "text-bg-secondary",
    corrected: "text-bg-warning",
    confirmed: "text-bg-success",
  };
  return `<span class="badge ${classes[status] || "text-bg-secondary"}">${escapeHtml(status)}</span>`;
}

function showToast(message, variant = "danger") {
  const container = document.querySelector("#toast-container");
  const toast = document.createElement("div");
  toast.className = `toast align-items-center text-bg-${variant} border-0`;
  toast.role = "alert";
  toast.innerHTML = `<div class="d-flex"><div class="toast-body">${escapeHtml(message)}</div><button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div>`;
  container.append(toast);
  const instance = new bootstrap.Toast(toast, { delay: 5000 });
  toast.addEventListener("hidden.bs.toast", () => toast.remove());
  instance.show();
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(`${API_ROOT}${path}`, { ...options, headers });
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json")
    ? await response.json()
    : null;

  if (!response.ok) {
    if (response.status === 401 && token) sessionStorage.removeItem(TOKEN_KEY);
    const detail = typeof body?.detail === "string"
      ? body.detail
      : body?.detail?.message || "Request failed.";
    throw new Error(detail);
  }
  return body;
}

function renderNav() {
  if (getToken()) {
    navItems.innerHTML = `
      <li class="nav-item"><a class="nav-link" href="/documents">Documents</a></li>
      <li class="nav-item"><a class="nav-link" href="/documents/upload">Upload</a></li>
      <li class="nav-item ms-lg-2"><button class="btn btn-outline-light btn-sm" id="logout-button">Log out</button></li>`;
    document.querySelector("#logout-button").addEventListener("click", logout);
  } else {
    navItems.innerHTML = `
      <li class="nav-item"><a class="nav-link" href="/login">Log in</a></li>
      <li class="nav-item ms-lg-2"><a class="btn btn-light btn-sm" href="/register">Create account</a></li>`;
  }
}

async function requireAuth() {
  if (!getToken()) {
    window.location.assign("/login");
    return null;
  }
  try {
    return await api("/users/me");
  } catch (error) {
    window.location.assign("/login");
    return null;
  }
}

function renderLogin() {
  if (getToken()) return window.location.assign("/documents");
  app.innerHTML = `
    <section class="card shadow-sm auth-card"><div class="card-body p-4">
      <h1 class="h3 mb-2">Welcome back</h1><p class="text-secondary mb-4">Log in to manage your documents.</p>
      <form id="login-form"><div class="mb-3"><label class="form-label" for="email">Email</label><input class="form-control" id="email" type="email" required autocomplete="email"></div>
      <div class="mb-4"><label class="form-label" for="password">Password</label><input class="form-control" id="password" type="password" required autocomplete="current-password"></div>
      <button class="btn btn-primary w-100" type="submit">Log in</button></form>
      <p class="small text-center mt-3 mb-0">No account? <a href="/register">Register</a></p>
    </div></section>`;
  document.querySelector("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const body = new URLSearchParams({
      username: form.querySelector("#email").value,
      password: form.querySelector("#password").value,
    });
    try {
      const result = await fetch(`${API_ROOT}/auth/login`, { method: "POST", body });
      const payload = await result.json();
      if (!result.ok) throw new Error(payload.detail || "Unable to log in.");
      setToken(payload.access_token);
      window.location.assign("/documents");
    } catch (error) {
      showToast(error.message);
    }
  });
}

function renderRegister() {
  if (getToken()) return window.location.assign("/documents");
  app.innerHTML = `
    <section class="card shadow-sm auth-card"><div class="card-body p-4">
      <h1 class="h3 mb-2">Create your account</h1><p class="text-secondary mb-4">Start organizing your documents.</p>
      <form id="register-form"><div class="mb-3"><label class="form-label" for="email">Email</label><input class="form-control" id="email" type="email" required autocomplete="email"></div>
      <div class="mb-4"><label class="form-label" for="password">Password</label><input class="form-control" id="password" type="password" required minlength="8" autocomplete="new-password"><div class="form-text">At least 8 characters.</div></div>
      <button class="btn btn-primary w-100" type="submit">Create account</button></form>
      <p class="small text-center mt-3 mb-0">Already registered? <a href="/login">Log in</a></p>
    </div></section>`;
  document.querySelector("#register-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      await api("/users/register", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        email: form.querySelector("#email").value,
        password: form.querySelector("#password").value,
      }) });
      showToast("Account created. You can log in now.", "success");
      window.setTimeout(() => window.location.assign("/login"), 800);
    } catch (error) {
      showToast(error.message);
    }
  });
}

async function renderDocuments() {
  const user = await requireAuth();
  if (!user) return;
  app.innerHTML = `<div class="d-flex flex-wrap gap-3 justify-content-between align-items-center mb-4"><div><h1 class="h2 mb-1">Your documents</h1><p class="text-secondary mb-0">Signed in as ${escapeHtml(user.email)}</p></div><a class="btn btn-primary" href="/documents/upload">Upload document</a></div><div id="documents-content" class="card shadow-sm"><div class="card-body text-center text-secondary py-5">Loading documents…</div></div>`;
  try {
    const documents = await api("/documents");
    const content = document.querySelector("#documents-content");
    if (!documents.length) {
      content.innerHTML = `<div class="card-body text-center empty-state"><h2 class="h4">No documents yet</h2><p class="text-secondary">Upload a PDF, JPG or PNG to start processing it.</p><a class="btn btn-primary" href="/documents/upload">Upload document</a></div>`;
      return;
    }
    content.innerHTML = `<div class="table-responsive"><table class="table table-hover align-middle mb-0"><thead><tr><th>Document</th><th>Size</th><th>Status</th><th>Type</th><th>Uploaded</th><th></th></tr></thead><tbody>${documents.map((document) => `<tr><td><div class="fw-semibold">${escapeHtml(document.original_filename)}</div></td><td data-file-size="${Number(document.file_size_bytes) || 0}">${formatFileSize(document.file_size_bytes)}</td><td>${statusBadge(document.status)}</td><td>${escapeHtml(document.document_type || "—")}</td><td>${formatDate(document.created_at?.slice(0, 10))}</td><td><a class="btn btn-sm btn-outline-primary" href="/documents/${document.id}">Open</a></td></tr>`).join("")}</tbody></table></div>`;
  } catch (error) {
    app.innerHTML = `<div class="alert alert-danger">${escapeHtml(error.message)}</div>`;
  }
}

async function renderUpload() {
  if (!(await requireAuth())) return;
  app.innerHTML = `<div class="row justify-content-center"><section class="col-lg-7"><a class="text-decoration-none" href="/documents">← Documents</a><div class="card shadow-sm mt-3"><div class="card-body p-4"><h1 class="h3">Upload document</h1><p class="text-secondary">PDF, JPG or PNG. The file will be processed asynchronously.</p><form id="upload-form"><div class="mb-3"><label class="form-label" for="file">File</label><input class="form-control" id="file" name="file" type="file" accept="application/pdf,image/jpeg,image/png" required></div><div class="form-check mb-4"><input class="form-check-input" id="confidential" name="confidential" type="checkbox"><label class="form-check-label" for="confidential">Confidential mode — use local extraction only</label></div><button class="btn btn-primary" type="submit">Upload</button></form></div></div></section></div>`;
  document.querySelector("#upload-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submitButton = form.querySelector("button[type=submit]");
    submitButton.disabled = true;
    const data = new FormData(form);
    data.set("confidential", form.confidential.checked ? "true" : "false");
    try {
      const document = await api("/documents/upload", { method: "POST", body: data });
      window.location.assign(`/documents/${document.id}`);
    } catch (error) {
      showToast(error.message);
      submitButton.disabled = false;
    }
  });
}

async function renderDocumentDetail(documentId) {
  if (!(await requireAuth())) return;
  app.innerHTML = `<div class="text-center text-secondary py-5">Loading document…</div>`;
  try {
    const document = await api(`/documents/${documentId}/result`);
    const fields = [
      ["Document type", document.document_type], ["Sender", document.sender], ["Document date", formatDate(document.document_date)], ["Deadline", formatDate(document.deadline)], ["Amount", formatAmount(document.amount, document.currency)], ["Extraction", statusBadge(document.extraction_status)],
    ];
    app.innerHTML = `<a class="text-decoration-none" href="/documents">← Documents</a><div class="d-flex flex-wrap gap-3 justify-content-between align-items-start mt-3 mb-4"><div><h1 class="h2 mb-1">${escapeHtml(document.original_filename)}</h1><div class="d-flex gap-2">${statusBadge(document.status)} ${document.processing_mode === "confidential" ? '<span class="badge text-bg-dark">confidential</span>' : ""}</div></div>${document.file_download_url ? `<a class="btn btn-outline-primary" href="${escapeHtml(document.file_download_url)}">Download</a>` : ""}</div>${document.processing_error ? `<div class="alert alert-danger">${escapeHtml(document.processing_error)}</div>` : ""}<div class="row g-4"><div class="col-lg-7">${document.file_preview_url ? `<iframe class="preview-frame" title="Document preview" src="${escapeHtml(document.file_preview_url)}"></iframe>` : '<div class="alert alert-secondary">Preview is not available yet.</div>'}</div><div class="col-lg-5"><div class="card shadow-sm"><div class="card-body"><h2 class="h5">Extraction result</h2><dl class="row mb-0">${fields.map(([label, value]) => `<dt class="col-sm-5 text-secondary">${label}</dt><dd class="col-sm-7">${value || "—"}</dd>`).join("")}</dl>${document.summary ? `<hr><p class="document-summary mb-0">${escapeHtml(document.summary)}</p>` : ""}</div></div>${document.can_correct ? correctionForm(document) : ""}${document.can_confirm ? `<button class="btn btn-success mt-3" id="confirm-extraction">Confirm extraction</button>` : ""}</div></div>`;
    bindCorrectionForm(document);
    const confirmButton = document.can_confirm && window.document.querySelector("#confirm-extraction");
    if (confirmButton) confirmButton.addEventListener("click", async () => {
      try { await api(`/documents/${documentId}/confirm`, { method: "POST" }); showToast("Extraction confirmed.", "success"); renderDocumentDetail(documentId); } catch (error) { showToast(error.message); }
    });
  } catch (error) {
    app.innerHTML = `<div class="alert alert-danger">${escapeHtml(error.message)}</div>`;
  }
}

function correctionForm(document) {
  const documentTypes = ["invoice", "receipt", "letter", "contract", "bank_statement", "tax_document", "medical_document", "other"];
  return `<div class="card shadow-sm mt-4"><div class="card-body"><h2 class="h5">Correct extraction</h2><form id="correction-form"><div class="mb-3"><label class="form-label" for="document_type">Document type</label><select class="form-select" id="document_type">${documentTypes.map((type) => `<option value="${type}" ${document.document_type === type ? "selected" : ""}>${type}</option>`).join("")}</select></div><div class="mb-3"><label class="form-label" for="sender">Sender / vendor</label><input class="form-control" id="sender" value="${escapeHtml(document.sender || "")}"></div><div class="row"><div class="col-md-6 mb-3"><label class="form-label" for="amount">Amount</label><input class="form-control" id="amount" type="number" min="0" step="0.01" value="${escapeHtml(document.amount || "")}"></div><div class="col-md-6 mb-3"><label class="form-label" for="document_date">Document date</label><input class="form-control" id="document_date" type="date" value="${escapeHtml(document.document_date || "")}"></div></div><button class="btn btn-primary" type="submit">Save changes</button></form></div></div>`;
}

function bindCorrectionForm(document) {
  const form = window.document.querySelector("#correction-form");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fields = ["document_type", "sender", "amount", "document_date"];
    const payload = {};
    fields.forEach((field) => {
      const value = form.querySelector(`#${field}`).value;
      if (String(value) !== String(document[field] ?? "")) payload[field] = value || null;
    });
    if (!Object.keys(payload).length) return showToast("No changes to save.", "warning");
    try { await api(`/documents/${document.id}/extraction`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }); showToast("Changes saved.", "success"); renderDocumentDetail(document.id); } catch (error) { showToast(error.message); }
  });
}

function renderNotFound() {
  app.innerHTML = `<div class="text-center empty-state"><h1 class="h3">Page not found</h1><a class="btn btn-primary" href="/documents">Go to documents</a></div>`;
}

function start() {
  renderNav();
  const path = window.location.pathname;
  if (path === "/login") return renderLogin();
  if (path === "/register") return renderRegister();
  if (path === "/documents") return renderDocuments();
  if (path === "/documents/upload") return renderUpload();
  const detailMatch = path.match(/^\/documents\/(\d+)$/);
  if (detailMatch) return renderDocumentDetail(detailMatch[1]);
  renderNotFound();
}

start();
