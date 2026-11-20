(() => {
  const key = "research-agent-theme";
  const researcherKey = "research-agent-researcher-theme";
  const system = window.matchMedia("(prefers-color-scheme: dark)");
  const validTheme = value => ["light", "dark"].includes(value) ? value : "system";
  const participantPage = ["/participate", "/participant.html"].includes(location.pathname);
  const testTheme = participantPage ? new URLSearchParams(location.search).get("test-theme") : null;
  let preference = "system";
  let researcherPreference = false;
  try { preference = validTheme(localStorage.getItem(key)); } catch { /* Use the system setting. */ }
  if (participantPage && ["light", "dark"].includes(testTheme)) {
    preference = testTheme;
    document.documentElement.dataset.testTheme = testTheme;
  } else if (!participantPage) {
    // Apply the account's last preference in this tab before the workspace is fetched.
    try {
      const saved = sessionStorage.getItem(researcherKey);
      if (saved !== null) {
        preference = validTheme(saved);
        researcherPreference = true;
      }
    } catch { /* The account preference will be applied after sign-in. */ }
  }

  function applyTheme() {
    const theme = preference === "system" ? (system.matches ? "dark" : "light") : preference;
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]').content = theme === "dark" ? "#191919" : "#fafafa";
  }
  applyTheme();
  window.applyResearcherTheme = value => {
    researcherPreference = true;
    preference = validTheme(value);
    try { sessionStorage.setItem(researcherKey, preference); } catch { /* Keep the selection on this page. */ }
    applyTheme();
  };

  document.addEventListener("DOMContentLoaded", () => {
    const controls = [...document.querySelectorAll("[data-theme-select]")];
    const syncControls = () => { for (const control of controls) control.value = preference; };
    syncControls();
    for (const control of controls) {
      control.addEventListener("change", () => {
        preference = validTheme(control.value);
        try { localStorage.setItem(key, preference); } catch { /* Keep the selection on this page. */ }
        syncControls();
        applyTheme();
      });
    }
    window.clearTestTheme = () => {
      if (!document.documentElement.dataset.testTheme) return;
      delete document.documentElement.dataset.testTheme;
      try { preference = validTheme(localStorage.getItem(key)); } catch { preference = "system"; }
      syncControls();
      applyTheme();
    };
    system.addEventListener("change", applyTheme);
    window.addEventListener("storage", event => {
      if (researcherPreference || document.documentElement.dataset.testTheme || (event.key !== key && event.key !== null)) return;
      preference = validTheme(event.newValue);
      syncControls();
      applyTheme();
    });
  });
})();
