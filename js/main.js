/* ============================================================
   Μαέρα & Διόνυσος — main.js
   Language toggle (EL/EN), navigation, reveal animations,
   hero parallax, area map (Leaflet), video placeholder.
   ============================================================ */

(function () {
  "use strict";

  /* ---------- Language toggle (EL default) ---------- */
  // Greek text lives in the HTML; English lives in data-en / data-en-placeholder.
  // On first toggle we stash the Greek original in data-el so we can switch back.
  let lang = localStorage.getItem("zaros-lang") || "el";

  function applyLang(next) {
    document.querySelectorAll("[data-en]").forEach(function (el) {
      if (!el.dataset.el) el.dataset.el = el.innerHTML;
      el.innerHTML = next === "en" ? el.dataset.en : el.dataset.el;
    });
    document.querySelectorAll("[data-en-placeholder]").forEach(function (el) {
      if (!el.dataset.elPlaceholder) el.dataset.elPlaceholder = el.placeholder;
      el.placeholder = next === "en" ? el.dataset.enPlaceholder : el.dataset.elPlaceholder;
    });
    document.documentElement.lang = next;
    var btn = document.getElementById("langToggle");
    if (btn) btn.querySelector(".lang-el").textContent = next === "en" ? "ΕΛ" : "EN";
    lang = next;
    localStorage.setItem("zaros-lang", next);
    document.dispatchEvent(new CustomEvent("langchange", { detail: next }));
  }

  window.getLang = function () { return lang; };

  document.getElementById("langToggle").addEventListener("click", function () {
    applyLang(lang === "el" ? "en" : "el");
  });
  if (lang === "en") applyLang("en");

  /* ---------- Header state + mobile nav ---------- */
  var header = document.getElementById("siteHeader");
  var navLinks = document.getElementById("navLinks");
  var navToggle = document.getElementById("navToggle");

  function onScroll() {
    header.classList.toggle("scrolled", window.scrollY > 40);
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  navToggle.addEventListener("click", function () {
    navLinks.classList.toggle("open");
  });
  navLinks.addEventListener("click", function (e) {
    if (e.target.tagName === "A") navLinks.classList.remove("open");
  });

  /* ---------- Reveal on scroll ---------- */
  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        entry.target.classList.add("visible");
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.12 });
  document.querySelectorAll(".reveal").forEach(function (el) { observer.observe(el); });

  /* ---------- Gentle hero parallax ---------- */
  var heroBg = document.querySelector(".hero-bg");
  if (heroBg && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    window.addEventListener("scroll", function () {
      var y = window.scrollY;
      if (y < window.innerHeight) heroBg.style.transform = "translateY(" + y * 0.35 + "px)";
    }, { passive: true });
  }

  /* ---------- Video placeholder ---------- */
  var video = document.getElementById("tourVideo");
  var placeholder = document.getElementById("videoPlaceholder");
  if (video && placeholder) {
    // Hide the placeholder only when a real video source is actually available.
    video.addEventListener("loadedmetadata", function () { placeholder.hidden = true; });
    video.addEventListener("error", function () { placeholder.hidden = false; }, true);
    placeholder.addEventListener("click", function () {
      video.load();
      video.play().catch(function () { /* no source yet — keep placeholder */ });
    });
  }

  /* ---------- Footer year ---------- */
  document.getElementById("year").textContent = new Date().getFullYear();

  /* ---------- Area map (Leaflet) ---------- */
  var mapEl = document.getElementById("areaMap");
  if (mapEl && window.L) {
    var map = L.map("areaMap", { scrollWheelZoom: false });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://openstreetmap.org">OpenStreetMap</a> contributors',
      maxZoom: 18
    }).addTo(map);

    var colors = {
      stay: "#8e2f3c",
      nature: "#4a5d3a",
      monastery: "#7a5ba6",
      arch: "#a3661f",
      beach: "#3d7a99",
      food: "#c4744f"
    };

    // [lat, lng, category, name EL, name EN]
    var pois = [
      [35.130187, 24.903187, "stay", "Zaros Filoxenia Apartments — Μαέρα & Διόνυσος", "Zaros Filoxenia Apartments — Maera & Dionysos"],
      [35.1427, 24.8983, "nature", "Λίμνη Ζαρού (Βότομος)", "Lake Zaros (Votomos)"],
      [35.13,   24.92,   "nature", "Φαράγγι Αγίου Νικολάου", "Agios Nikolaos Gorge"],
      [35.159,  24.9022, "nature", "Δάσος Ρούβα", "Rouvas Forest"],
      [35.211,  24.837,  "nature", "Οροπέδιο Νίδας & Ιδαίον Άντρον", "Nida Plateau & Ideon Cave"],
      [35.135,  24.93,   "monastery", "Μονή Αγίου Νικολάου", "Agios Nikolaos Monastery"],
      [35.1403, 24.8861, "monastery", "Μονή Βροντησίου", "Vrontisi Monastery"],
      [35.1394, 24.858,  "monastery", "Μονή Βαλσαμονέρου", "Valsamonero Monastery"],
      [35.0514, 24.814,  "arch", "Φαιστός", "Phaistos"],
      [35.059,  24.792,  "arch", "Αγία Τριάδα", "Agia Triada"],
      [35.063,  24.947,  "arch", "Γόρτυνα", "Gortyna"],
      [35.1706, 24.8286, "arch", "Καμάρες & Σπήλαιο Καμαρών", "Kamares & Kamares Cave"],
      [34.995,  24.749,  "beach", "Μάταλα", "Matala Beach"],
      [35.008,  24.765,  "beach", "Κομμός", "Kommos Beach"],
      [35.028,  24.76,   "beach", "Καλαμάκι", "Kalamaki Beach"],
      [35.052,  24.873,  "food", "Μοίρες — αγορά & λαϊκή (Σάββατο)", "Moires — shops & Saturday market"],
      [35.1428, 24.8951, "food", "Ταβέρνες με πέστροφα στη λίμνη", "Fresh-trout tavernas by the lake"]
    ];

    var markers = [];
    pois.forEach(function (p) {
      var m = L.circleMarker([p[0], p[1]], {
        radius: p[2] === "stay" ? 10 : 7,
        color: "#fffdf9",
        weight: 2,
        fillColor: colors[p[2]],
        fillOpacity: 0.95
      }).addTo(map);
      m.poiNames = { el: p[3], en: p[4] };
      m.bindPopup(lang === "en" ? p[4] : p[3]);
      markers.push(m);
      if (p[2] === "stay") m.bindTooltip(lang === "en" ? p[4] : p[3], { permanent: false });
    });

    map.fitBounds(L.latLngBounds(pois.map(function (p) { return [p[0], p[1]]; })).pad(0.08));

    document.addEventListener("langchange", function (e) {
      markers.forEach(function (m) {
        m.setPopupContent(m.poiNames[e.detail] || m.poiNames.el);
      });
    });
  }
})();
