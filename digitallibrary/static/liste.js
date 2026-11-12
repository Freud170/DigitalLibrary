// Liste: Suche (nur Titel/Untertitel) und Mehrfachauswahl für die Status-Änderung.
(() => {
  const karten = () => [...document.querySelectorAll(".raster .karte")];
  const box = (karte) => karte.querySelector(".auswahl");

  function markieren(karte, an) {
    box(karte).checked = an;
    karte.classList.toggle("gewaehlt", an);
  }

  function aktualisieren() {
    const leiste = document.querySelector(".auswahl-leiste");
    if (!leiste) return;
    const anzahl = karten().filter((k) => box(k).checked).length;
    leiste.hidden = anzahl === 0;
    leiste.querySelector(".auswahl-anzahl").textContent = `${anzahl} ausgewählt`;
  }

  document.addEventListener("change", (e) => {
    if (e.target.matches(".karte .auswahl")) {
      markieren(e.target.closest(".karte"), e.target.checked);
      aktualisieren();
    }
  });

  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-alle-sichtbaren]")) {
      karten().filter((k) => !k.hidden).forEach((k) => markieren(k, true));
      aktualisieren();
    } else if (e.target.closest("[data-auswahl-leeren]")) {
      karten().forEach((k) => markieren(k, false));
      aktualisieren();
    }
  });

  document.addEventListener("input", (e) => {
    if (!e.target.matches(".suche")) return;
    const suche = e.target.value.trim().toLowerCase();
    karten().forEach((k) => {
      const text = [".karte-titel", ".karte-unter"]
        .map((sel) => k.querySelector(sel)?.textContent ?? "")
        .join(" ")
        .toLowerCase();
      k.hidden = !text.includes(suche);
    });
  });

  // Nach HTMX-Austausch (Status einer Karte, Steam-Abgleich) Zähler neu berechnen
  document.addEventListener("htmx:afterSwap", aktualisieren);
  // Browser merkt sich Häkchen beim Zurück-Navigieren → Zustand übernehmen
  window.addEventListener("pageshow", () => {
    karten().forEach((k) => markieren(k, box(k).checked));
    aktualisieren();
  });
})();
