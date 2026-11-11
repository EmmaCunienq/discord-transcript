// Même dessin pour la page, le favicon et l'icône Windows ; seule la palette change.
(() => {
  const cache = new Map();
  function svg({ accent, accentEnd, ink }) {
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">
      <defs><linearGradient id="tile" x1="0" y1="0" x2="1" y2="1"><stop stop-color="${accentEnd}"/><stop offset="1" stop-color="${accent}"/></linearGradient></defs>
      <rect width="32" height="32" rx="8" fill="url(#tile)"/>
      <path d="M12 10a4 4 0 0 1 8 0v6a4 4 0 0 1-8 0ZM8 14v2a8 8 0 0 0 16 0v-2M16 24v3M12 27h8" fill="none" stroke="${ink}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`;
  }
  const url = palette => "data:image/svg+xml," + encodeURIComponent(svg(palette));
  function png(source, size = 64) {
    const key = `${size}:${source}`;
    if (!cache.has(key)) cache.set(key, new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => {
        const canvas = document.createElement("canvas");
        canvas.width = canvas.height = size;
        canvas.getContext("2d").drawImage(image, 0, 0, size, size);
        resolve(canvas.toDataURL("image/png"));
      };
      image.onerror = () => { cache.delete(key); reject(new Error("Icône indisponible")); };
      image.src = source;
    }));
    return cache.get(key);
  }
  window.AppIcon = Object.freeze({ svg, url, png });
})();
