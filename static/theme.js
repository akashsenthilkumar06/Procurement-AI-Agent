(() => {
  const key = "research-agent-theme";
  const system = window.matchMedia("(prefers-color-scheme: dark)");
  const validTheme = value => ["light", "dark"].includes(value) ? value : "system";
  let preference = "system";
  let researcherPreference = false;
  try { preference = validTheme(localStorage.getItem(key)); } catch { /* Use the system setting. */ }

  function applyTheme() {
    const theme = preference === "system" ? (system.matches ? "dark" : "light") : preference;
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]').content = theme === "dark" ? "#191919" : "#fafafa";
  }
  applyTheme();
  window.applyResearcherTheme = value => {
    researcherPreference = true;
    preference = validTheme(value);
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
    system.addEventListener("change", applyTheme);
    window.addEventListener("storage", event => {
      if (researcherPreference || (event.key !== key && event.key !== null)) return;
      preference = validTheme(event.newValue);
      syncControls();
      applyTheme();
    });
  });
})();
