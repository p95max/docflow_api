(() => {
  const TOKEN_KEY = "docsflow.access_token";
  const appRoot = document.querySelector("#app");
  const documentDetailPattern = /^\/documents\/(\d+)$/;

  function notify(message, variant = "danger") {
    if (typeof window.showToast === "function") {
      window.showToast(message, variant);
      return;
    }

    window.alert(message);
  }

  function normalizeDocumentUrl(value) {
    if (!value) return value;

    try {
      const url = new URL(value, window.location.origin);
      const isLocalHost = ["localhost", "127.0.0.1", "0.0.0.0"].includes(
        url.hostname,
      );
      const isDocumentFileUrl = url.pathname.startsWith("/api/v1/documents/");

      if (isLocalHost && isDocumentFileUrl) {
        return `${window.location.origin}${url.pathname}${url.search}${url.hash}`;
      }

      return value;
    } catch {
      return value;
    }
  }

  function normalizePreviewAndDownloadUrls() {
    appRoot.querySelectorAll("iframe.preview-frame[src]").forEach((iframe) => {
      const currentUrl = iframe.getAttribute("src");
      const normalizedUrl = normalizeDocumentUrl(currentUrl);

      if (normalizedUrl !== currentUrl) {
        iframe.setAttribute("src", normalizedUrl);
      }
    });

    appRoot.querySelectorAll("a[href]").forEach((link) => {
      const currentUrl = link.getAttribute("href");
      const normalizedUrl = normalizeDocumentUrl(currentUrl);

      if (normalizedUrl !== currentUrl) {
        link.setAttribute("href", normalizedUrl);
      }
    });
  }

  function showConfidentialModeNotice() {
    if (!documentDetailPattern.test(window.location.pathname)) return;
    if (appRoot.querySelector("[data-confidential-mode-notice]")) return;

    const confidentialBadge = Array.from(
      appRoot.querySelectorAll("span.badge"),
    ).find((badge) => badge.textContent.trim().toLowerCase() === "confidential");

    if (!confidentialBadge) return;

    const title = appRoot.querySelector("h1.h2");
    const header = title?.parentElement?.parentElement;

    if (!header) return;

    const notice = document.createElement("div");
    notice.className = "alert alert-info";
    notice.dataset.confidentialModeNotice = "true";
    notice.innerHTML = [
      "<strong>Confidential mode:</strong>",
      "This document was processed locally only.",
      "Structured AI fields are intentionally not generated, and no document data was sent to OpenAI.",
    ].join(" ");

    header.insertAdjacentElement("afterend", notice);
  }

  async function deleteDocument(documentId, filename, button) {
    const confirmed = window.confirm(
      `Delete "${filename}" permanently? This action cannot be undone.`,
    );

    if (!confirmed) return;

    button.disabled = true;

    const headers = new Headers();
    const token = sessionStorage.getItem(TOKEN_KEY);

    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }

    try {
      const response = await fetch(`/api/v1/documents/${documentId}`, {
        method: "DELETE",
        headers,
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        const detail = typeof payload?.detail === "string"
          ? payload.detail
          : payload?.detail?.message || "Unable to delete document.";
        throw new Error(detail);
      }

      notify("Document deleted.", "success");

      if (documentDetailPattern.test(window.location.pathname)) {
        window.location.assign("/documents");
        return;
      }

      window.location.reload();
    } catch (error) {
      notify(error.message);
      button.disabled = false;
    }
  }

  function createDeleteButton(documentId, filename, extraClasses = "") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `btn btn-outline-danger ${extraClasses}`.trim();
    button.textContent = "Delete";
    button.dataset.documentDelete = documentId;
    button.addEventListener("click", () => {
      deleteDocument(documentId, filename, button);
    });
    return button;
  }

  function enhanceDocumentDetail() {
    const match = window.location.pathname.match(documentDetailPattern);

    if (!match || appRoot.querySelector("[data-document-delete]")) return;

    const title = appRoot.querySelector("h1.h2");
    const header = title?.parentElement?.parentElement;

    if (!title || !header) return;

    const actions = document.createElement("div");
    actions.className = "d-flex gap-2";
    actions.dataset.documentActions = "true";

    const downloadLink = Array.from(header.children).find(
      (element) => element.matches?.("a.btn[href]"),
    );

    if (downloadLink) {
      actions.append(downloadLink);
    }

    actions.append(
      createDeleteButton(match[1], title.textContent.trim()),
    );
    header.append(actions);
  }

  function enhanceDocumentList() {
    appRoot.querySelectorAll('a[href^="/documents/"]').forEach((openLink) => {
      const match = openLink.getAttribute("href")?.match(/^\/documents\/(\d+)$/);
      const cell = openLink.parentElement;

      if (!match || !cell || cell.querySelector("[data-document-delete]")) return;

      const row = openLink.closest("tr");
      const filename = row?.querySelector(".fw-semibold")?.textContent?.trim()
        || `document ${match[1]}`;

      cell.append(
        createDeleteButton(match[1], filename, "btn-sm ms-2"),
      );
    });
  }

  function enhanceDocumentPages() {
    normalizePreviewAndDownloadUrls();
    showConfidentialModeNotice();
    enhanceDocumentDetail();
    enhanceDocumentList();
  }

  const observer = new MutationObserver(enhanceDocumentPages);
  observer.observe(appRoot, { childList: true, subtree: true });
  enhanceDocumentPages();
})();