// Un seul menu de navigation, intégré à la page principale et aux réglages.
(() => {
  const host = document.querySelector("[data-app-menu]");
  if (!host) return;
  const icon = paths => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths}</svg>`;
  const icons = {
    home: icon('<path d="m3 10 9-7 9 7M5 9v12h14V9M9 21v-8h6v8"/>'),
    settings: icon('<circle cx="12" cy="12" r="3"/><path d="m9 3-1 3-3 1-2 5 2 5 3 1 1 3h6l1-3 3-1 2-5-2-5-3-1-1-3Z"/>'),
    folder: icon('<path d="M3 7V5h6l2 2h10v13H3Z"/>'),
    file: icon('<path d="M14 3H5v18h14V8ZM14 3v5h5M8 12h8M8 16h6"/>'),
    exit: icon('<path d="M10 4H4v16h6M9 12h12m-4-4 4 4-4 4"/>'),
  };
  const menu = document.createElement("details");
  menu.className = "app-menu";
  menu.innerHTML = `<summary class="app-menu-trigger" aria-label="Menu de l’application" title="Menu de l’application" aria-controls="appMenuPanel">
      ${icon('<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>')}
    </summary>
    <nav class="app-menu-panel" id="appMenuPanel" aria-label="Application">
      <p class="app-menu-heading">Discord Transcript</p>
      <a href="/" data-home-link>${icons.home}<span>Accueil</span></a>
      <a href="/static/settings.html" id="settingsLink">${icons.settings}<span>Réglages</span></a>
      <div class="app-menu-divider" data-desktop-divider hidden></div>
      <button type="button" data-desktop-action="open_data_folder" hidden>${icons.folder}<span>Dossier des réunions</span></button>
      <button type="button" data-desktop-action="open_log_file" hidden>${icons.file}<span>Ouvrir le journal</span></button>
      <div class="app-menu-divider" data-quit-divider hidden></div>
      <button type="button" class="app-menu-quit" data-desktop-action="quit_app" hidden>${icons.exit}<span>Quitter l’application</span></button>
      <p class="app-menu-error" role="alert" hidden></p>
    </nav>`;
  host.append(menu);
  const trigger = menu.querySelector("summary");
  const panel = menu.querySelector("nav");
  const error = menu.querySelector(".app-menu-error");
  const settings = menu.querySelector("#settingsLink");
  const home = menu.querySelector("[data-home-link]");
  home.href = "/" + location.search;
  settings.href = "/static/settings.html" + location.search;
  (location.pathname === "/static/settings.html" ? settings : home).setAttribute("aria-current", "page");

  function close(returnFocus = false) {
    menu.open = false;
    if (returnFocus) trigger.focus();
  }
  menu.addEventListener("keydown", event => {
    if (event.key === "Escape" && menu.open) {
      event.preventDefault();
      close(true);
    }
  });
  document.addEventListener("pointerdown", event => { if (!menu.contains(event.target)) close(); });
  document.addEventListener("focusin", event => { if (!menu.contains(event.target)) close(); });
  panel.querySelectorAll("a").forEach(link => link.addEventListener("click", () => close()));

  function connectDesktop() {
    const api = window.pywebview?.api;
    menu.querySelectorAll("[data-desktop-action]").forEach(button => {
      button.hidden = typeof api?.[button.dataset.desktopAction] !== "function";
    });
    menu.querySelector("[data-desktop-divider]").hidden = !["open_data_folder", "open_log_file"].some(name => typeof api?.[name] === "function");
    menu.querySelector("[data-quit-divider]").hidden = typeof api?.quit_app !== "function";
  }
  window.addEventListener("pywebviewready", connectDesktop);
  connectDesktop();
  menu.querySelectorAll("[data-desktop-action]").forEach(button => {
    button.addEventListener("click", async () => {
      const api = window.pywebview?.api;
      const action = button.dataset.desktopAction;
      if (typeof api?.[action] !== "function") return;
      button.disabled = true;
      error.hidden = true;
      try {
        await api[action]();
        close(true);
      } catch {
        error.textContent = "Cette action n’a pas pu aboutir. Réessayez.";
        error.hidden = false;
      } finally {
        button.disabled = false;
      }
    });
  });
})();
