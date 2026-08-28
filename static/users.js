(() => {
  "use strict";

  const retryDelay = 1000;

  async function loadUsersOverview(container) {
    if (!container.isConnected) return;

    try {
      const response = await fetch(container.dataset.usersOverviewUrl, {
        credentials: "same-origin",
        headers: { Accept: "text/html" },
      });

      if (response.status === 202) {
        window.setTimeout(() => loadUsersOverview(container), retryDelay);
        return;
      }

      if (!response.ok && !response.headers.has("X-Users-Overview-Cache")) {
        throw new Error(`Users overview request failed with ${response.status}`);
      }

      const fragment = await response.text();
      if (fragment.trim()) {
        container.outerHTML = fragment;
      }
    } catch (_error) {
      container.setAttribute("aria-busy", "false");
      const status = container.querySelector("[data-users-status]");
      if (status) status.textContent = "The users overview could not be loaded.";
    }
  }

  document.querySelectorAll("[data-users-overview-url]").forEach(loadUsersOverview);
})();
