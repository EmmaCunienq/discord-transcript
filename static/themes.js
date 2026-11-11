// Chargé avant le premier affichage sur les deux pages.
(() => {
  const themes = [
    { id: "basic", name: "Basique", detail: "Épuré, sans décor · suit le système" },
    { id: "woodland", name: "Forêt", detail: "Nature & parchemin · suit le système" },
    { id: "city", name: "Ville", detail: "Toits au crépuscule & lumières cuivrées" },
    { id: "mountain", name: "Montagne", detail: "Sommets enneigés & lac alpin" },
    { id: "ocean", name: "Mer", detail: "Rivage méditerranéen & lumière d’été" },
    { id: "japan", name: "Jardin japonais", detail: "Cerisiers, lanternes & reflets du soir" },
  ];
  const key = "dt.design";
  const previous = { paper: "basic", midnight: "city", aurora: "ocean" };
  const normalize = value => previous[value] || value;
  const valid = value => themes.some(theme => theme.id === value);
  let nativeUpdate = Promise.resolve();
  let currentIcon = "";
  function syncWindowTheme() {
    const api = window.pywebview?.api;
    if (!api?.set_window_theme) return;
    const style = getComputedStyle(document.documentElement);
    const palette = {
      background: style.getPropertyValue("--card").trim(),
      text: style.getPropertyValue("--text").trim(),
      border: style.getPropertyValue("--border").trim(),
      dark: style.colorScheme === "dark",
    };
    const icon = currentIcon;
    // Le pont Python est asynchrone : le dernier choix doit aussi être le dernier appliqué.
    nativeUpdate = nativeUpdate.catch(() => {}).then(async () => {
      palette.icon = await window.AppIcon.png(icon);
      return api.set_window_theme(palette);
    });
    nativeUpdate.catch(() => {});
  }
  let selected = "basic";
  try {
    const saved = normalize(localStorage.getItem(key));
    if (valid(saved)) selected = saved;
  } catch { /* Le thème reste utilisable si le stockage est indisponible. */ }

  function apply(value) {
    value = normalize(value);
    selected = valid(value) ? value : "basic";
    document.documentElement.dataset.design = selected;
    document.querySelectorAll('[name="design"]').forEach(input => {
      input.checked = input.value === selected;
    });
    const meta = document.querySelector('meta[name="theme-color"]');
    const style = getComputedStyle(document.documentElement);
    if (meta) meta.content = style.getPropertyValue("--bg").trim();
    currentIcon = window.AppIcon.url({
      accent: style.getPropertyValue("--accent").trim(),
      accentEnd: style.getPropertyValue("--accent-end").trim(),
      ink: style.getPropertyValue("--accent-text").trim(),
    });
    document.documentElement.style.setProperty("--app-icon", `url("${currentIcon}")`);
    const favicon = document.querySelector('link[rel="icon"]');
    if (favicon) favicon.href = currentIcon;
    syncWindowTheme();
  }
  apply(selected);
  window.addEventListener("pywebviewready", syncWindowTheme);

  window.addEventListener("storage", event => {
    if (event.key === key || event.key === null) apply(event.newValue);
  });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => apply(selected));

  document.addEventListener("DOMContentLoaded", () => {
    const picker = document.querySelector("[data-theme-picker]");
    const notice = document.querySelector("[data-theme-notice]");
    if (picker) themes.forEach(theme => {
      const label = document.createElement("label");
      label.className = "theme-option";
      label.innerHTML = `<input type="radio" name="design" value="${theme.id}">
        <span class="theme-choice"><span class="theme-preview preview-${theme.id}" aria-hidden="true">
          <span class="preview-window"><span class="preview-line"></span><span class="preview-record"></span><span class="preview-panel"></span></span>
        </span><span class="theme-name">${theme.name}<span class="theme-check" aria-hidden="true">✓</span></span>
        <span class="theme-detail">${theme.detail}</span></span>`;
      label.querySelector("input").addEventListener("change", () => {
        apply(theme.id);
        try {
          localStorage.setItem(key, theme.id);
          notice.textContent = `Thème ${theme.name} appliqué et mémorisé sur cet appareil.`;
        } catch {
          notice.textContent = `Thème ${theme.name} appliqué. Le stockage est indisponible : ce choix ne sera pas conservé.`;
        }
      });
      picker.append(label);
    });
    apply(selected);
    document.querySelectorAll("[data-home-link]").forEach(link => { link.href = "/" + location.search; });
    const settingsLink = document.getElementById("settingsLink");
    if (settingsLink) settingsLink.href = "/static/settings.html" + location.search;
  });
})();
