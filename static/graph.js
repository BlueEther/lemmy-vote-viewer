(() => {
  "use strict";

  const retryDelay = 1000;

  async function loadGraph(container) {
    if (!container.isConnected) return;

    try {
      const response = await fetch(container.dataset.voteGraphUrl, {
        credentials: "same-origin",
        headers: { Accept: "text/html" },
      });

      if (response.status === 202) {
        window.setTimeout(() => loadGraph(container), retryDelay);
        return;
      }

      if (!response.ok && !response.headers.has("X-Vote-Graph-Cache")) {
        throw new Error(`Graph request failed with ${response.status}`);
      }

      const fragment = await response.text();
      if (fragment.trim()) container.outerHTML = fragment;
    } catch (_error) {
      container.setAttribute("aria-busy", "false");
      const status = container.querySelector("[data-graph-status]");
      if (status) status.textContent = "The graph could not be loaded.";
    }
  }

  document.querySelectorAll("[data-vote-graph-url]").forEach(loadGraph);
})();
