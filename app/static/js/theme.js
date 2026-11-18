(function () {
  const KEY = "e1p-theme";
  const THEMES = ["dark", "light", "midnight", "harbor", "sand", "contrast"];

  function current() {
    try {
      const stored = localStorage.getItem(KEY);
      return THEMES.indexOf(stored) >= 0 ? stored : "dark";
    } catch (_err) {
      return "dark";
    }
  }

  function apply(theme) {
    const next = THEMES.indexOf(theme) >= 0 ? theme : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(KEY, next);
    } catch (_err) {
      /* private browsing can refuse storage */
    }
    document.querySelectorAll('input[name="theme"]').forEach((input) => {
      input.checked = input.value === next;
    });
  }

  apply(current());
  document.addEventListener("DOMContentLoaded", () => {
    apply(current());
    document.addEventListener("change", (event) => {
      const target = event.target;
      if (target && target.name === "theme") apply(target.value);
    });
  });
})();
