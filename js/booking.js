/* ============================================================
   Μαέρα & Διόνυσος — booking.js
   Availability calendar, price calculation, Stripe checkout.

   Pricing policy (must match server/server.js):
     - Maera:    €50/night for 2 guests, max 4
     - Dionysos: €60/night for 2 guests, max 3
     - +€10 per extra guest per night
     - €15 one-off cleaning fee
     - Free cancellation up to 10 days before arrival

   Stripe is not active yet: if the backend (/api) is missing or
   reports stripeEnabled=false, the pay button is replaced by a
   contact fallback. Nothing else on the page breaks.
   ============================================================ */

(function () {
  "use strict";

  var API_BASE = ""; // same origin; set e.g. "https://api.example.com" if hosted separately

  var UNITS = {
    maera:    { nameEl: "Μαέρα",    nameEn: "Maera",    base: 50, maxGuests: 4 },
    dionysos: { nameEl: "Διόνυσος", nameEn: "Dionysos", base: 60, maxGuests: 3 }
  };
  var EXTRA_GUEST = 10;
  var CLEANING = 15;

  var FREE_CANCEL_DAYS = 10;

  var state = {
    unit: "maera",
    guests: 2,
    checkIn: null,   // Date
    checkOut: null,  // Date
    month: new Date(new Date().getFullYear(), new Date().getMonth(), 1),
    booked: {},      // unit -> Set of "YYYY-MM-DD"
    stripeEnabled: null // null = unknown, true/false once /api/config answers
  };

  /* ---------- Helpers ---------- */
  function iso(d) {
    return d.getFullYear() + "-" +
      String(d.getMonth() + 1).padStart(2, "0") + "-" +
      String(d.getDate()).padStart(2, "0");
  }
  function fmt(d) {
    var l = window.getLang ? window.getLang() : "el";
    return d.toLocaleDateString(l === "en" ? "en-GB" : "el-GR", { day: "numeric", month: "short", year: "numeric" });
  }
  function nightsBetween(a, b) { return Math.round((b - a) / 86400000); }
  function t(el, en) { return (window.getLang && window.getLang() === "en") ? en : el; }

  function nightly() {
    var u = UNITS[state.unit];
    return u.base + Math.max(0, state.guests - 2) * EXTRA_GUEST;
  }
  function total() {
    if (!state.checkIn || !state.checkOut) return null;
    return nightsBetween(state.checkIn, state.checkOut) * nightly() + CLEANING;
  }

  /* ---------- Availability ---------- */
  function loadAvailability(unit) {
    return fetch(API_BASE + "/api/availability?unit=" + unit)
      .then(function (r) { if (!r.ok) throw new Error("no api"); return r.json(); })
      .then(function (data) {
        state.booked[unit] = new Set(data.bookedDates || []);
      })
      .catch(function () {
        // No backend yet — treat everything as available.
        state.booked[unit] = state.booked[unit] || new Set();
      });
  }

  function isBooked(d) {
    var set = state.booked[state.unit];
    return set ? set.has(iso(d)) : false;
  }

  /* ---------- Calendar rendering ---------- */
  var calTitle = document.getElementById("calTitle");
  var calGrid = document.getElementById("calGrid");
  var DOW_EL = ["Δε", "Τρ", "Τε", "Πε", "Πα", "Σα", "Κυ"];
  var DOW_EN = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];

  function renderCalendar() {
    var m = state.month;
    var l = window.getLang ? window.getLang() : "el";
    calTitle.textContent = m.toLocaleDateString(l === "en" ? "en-GB" : "el-GR", { month: "long", year: "numeric" });
    calGrid.innerHTML = "";

    (l === "en" ? DOW_EN : DOW_EL).forEach(function (d) {
      var el = document.createElement("div");
      el.className = "cal-dow";
      el.textContent = d;
      calGrid.appendChild(el);
    });

    var first = new Date(m.getFullYear(), m.getMonth(), 1);
    var startOffset = (first.getDay() + 6) % 7; // Monday-first
    var daysInMonth = new Date(m.getFullYear(), m.getMonth() + 1, 0).getDate();
    var today = new Date(); today.setHours(0, 0, 0, 0);

    for (var i = 0; i < startOffset; i++) {
      calGrid.appendChild(document.createElement("div"));
    }
    for (var day = 1; day <= daysInMonth; day++) {
      var d = new Date(m.getFullYear(), m.getMonth(), day);
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "cal-day";
      btn.textContent = day;

      var past = d < today;
      var booked = isBooked(d);
      if (past) btn.disabled = true;
      if (booked) { btn.classList.add("booked"); btn.disabled = true; }

      if (state.checkIn && iso(d) === iso(state.checkIn)) btn.classList.add("range-start");
      if (state.checkOut && iso(d) === iso(state.checkOut)) btn.classList.add("range-end");
      if (state.checkIn && state.checkOut && d > state.checkIn && d < state.checkOut) btn.classList.add("in-range");

      (function (date) {
        btn.addEventListener("click", function () { pickDate(date); });
      })(d);

      calGrid.appendChild(btn);
    }
  }

  function rangeIsFree(a, b) {
    // every night from a (inclusive) to b (exclusive) must be free
    for (var d = new Date(a); d < b; d.setDate(d.getDate() + 1)) {
      if (isBooked(d)) return false;
    }
    return true;
  }

  function pickDate(d) {
    if (!state.checkIn || (state.checkIn && state.checkOut)) {
      state.checkIn = d;
      state.checkOut = null;
    } else if (d > state.checkIn) {
      if (rangeIsFree(state.checkIn, d)) {
        state.checkOut = d;
      } else {
        state.checkIn = d; // range crosses a booked night — restart selection
        state.checkOut = null;
      }
    } else {
      state.checkIn = d;
      state.checkOut = null;
    }
    renderCalendar();
    renderSummary();
  }

  document.getElementById("calPrev").addEventListener("click", function () {
    state.month = new Date(state.month.getFullYear(), state.month.getMonth() - 1, 1);
    renderCalendar();
  });
  document.getElementById("calNext").addEventListener("click", function () {
    state.month = new Date(state.month.getFullYear(), state.month.getMonth() + 1, 1);
    renderCalendar();
  });

  /* ---------- Unit picker ---------- */
  document.querySelectorAll(".unit-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll(".unit-btn").forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
      state.unit = btn.dataset.unit;
      state.checkIn = state.checkOut = null;
      if (state.guests > UNITS[state.unit].maxGuests) state.guests = UNITS[state.unit].maxGuests;
      document.getElementById("guestsCount").textContent = state.guests;
      loadAvailability(state.unit).then(function () {
        renderCalendar();
        renderSummary();
      });
    });
  });

  // "Book Maera/Dionysos" buttons on the apartment cards preselect the unit.
  document.querySelectorAll('a[data-unit]').forEach(function (a) {
    a.addEventListener("click", function () {
      var target = document.querySelector('.unit-btn[data-unit="' + a.dataset.unit + '"]');
      if (target) target.click();
    });
  });

  /* ---------- Guests ---------- */
  var guestsCount = document.getElementById("guestsCount");
  document.getElementById("guestsMinus").addEventListener("click", function () {
    if (state.guests > 1) { state.guests--; guestsCount.textContent = state.guests; renderSummary(); }
  });
  document.getElementById("guestsPlus").addEventListener("click", function () {
    if (state.guests < UNITS[state.unit].maxGuests) { state.guests++; guestsCount.textContent = state.guests; renderSummary(); }
  });

  /* ---------- Summary ---------- */
  function renderSummary() {
    var u = UNITS[state.unit];
    var l = window.getLang ? window.getLang() : "el";
    document.getElementById("sumUnit").textContent = l === "en" ? u.nameEn : u.nameEl;

    var datesEl = document.getElementById("sumDates");
    var nightsEl = document.getElementById("sumNights");
    var totalEl = document.getElementById("sumTotal");

    if (state.checkIn && state.checkOut) {
      var n = nightsBetween(state.checkIn, state.checkOut);
      datesEl.textContent = fmt(state.checkIn) + " → " + fmt(state.checkOut);
      nightsEl.textContent = n + " × " + nightly() + " € (" + state.guests + " " + t("άτομα", "guests") + ")";
      totalEl.textContent = total() + " €";
    } else if (state.checkIn) {
      datesEl.textContent = fmt(state.checkIn) + " → " + t("επιλέξτε αναχώρηση", "select check-out");
      nightsEl.textContent = "—";
      totalEl.textContent = "—";
    } else {
      datesEl.textContent = "—";
      nightsEl.textContent = "—";
      totalEl.textContent = "—";
    }
  }
  document.addEventListener("langchange", function () { renderCalendar(); renderSummary(); });

  /* ---------- Stripe config ---------- */
  var payBtn = document.getElementById("payBtn");
  var fallback = document.getElementById("bookingFallback");
  var msgEl = document.getElementById("bookingMsg");

  fetch(API_BASE + "/api/config")
    .then(function (r) { if (!r.ok) throw new Error("no api"); return r.json(); })
    .then(function (cfg) {
      state.stripeEnabled = !!cfg.stripeEnabled;
      if (cfg.freeCancelDays) FREE_CANCEL_DAYS = cfg.freeCancelDays;
    })
    .catch(function () { state.stripeEnabled = false; })
    .then(function () {
      if (state.stripeEnabled) {
        payBtn.textContent = t("Επιβεβαίωση κράτησης", "Confirm reservation");
        payBtn.dataset.el = "Επιβεβαίωση κράτησης";
        payBtn.dataset.en = "Confirm reservation";
      } else {
        payBtn.textContent = t("Αίτημα κράτησης", "Request booking");
        payBtn.dataset.en = "Request booking";
        payBtn.dataset.el = "Αίτημα κράτησης";
        fallback.hidden = false;
      }
    });

  function showMsg(text, cls) {
    msgEl.textContent = text;
    msgEl.className = "booking-msg " + cls;
    msgEl.hidden = false;
  }

  /* ---------- Submit ---------- */
  document.getElementById("bookingForm").addEventListener("submit", function (e) {
    e.preventDefault();
    msgEl.hidden = true;

    if (!state.checkIn || !state.checkOut) {
      showMsg(t("Επιλέξτε πρώτα ημερομηνίες άφιξης και αναχώρησης.", "Please select check-in and check-out dates first."), "error");
      return;
    }

    var payload = {
      unit: state.unit,
      checkIn: iso(state.checkIn),
      checkOut: iso(state.checkOut),
      guests: state.guests,
      name: document.getElementById("bkName").value,
      email: document.getElementById("bkEmail").value,
      phone: document.getElementById("bkPhone").value,
      lang: window.getLang ? window.getLang() : "el"
    };

    if (state.stripeEnabled) {
      payBtn.disabled = true;
      fetch(API_BASE + "/api/reserve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          if (res.ok && res.j.url) {
            window.location.href = res.j.url; // Stripe setup checkout (saves card, no charge)
          } else {
            throw new Error(res.j.error || "reservation failed");
          }
        })
        .catch(function (err) {
          payBtn.disabled = false;
          showMsg(t("Η κράτηση δεν ολοκληρώθηκε: ", "Reservation could not start: ") + err.message, "error");
        });
    } else {
      // Fallback: pre-filled email request until Stripe is activated.
      var u = UNITS[state.unit];
      var subject = t("Αίτημα κράτησης — ", "Booking request — ") + u.nameEl;
      var body =
        t("Κατάλυμα: ", "Apartment: ") + u.nameEl + "\n" +
        t("Άφιξη: ", "Check-in: ") + payload.checkIn + "\n" +
        t("Αναχώρηση: ", "Check-out: ") + payload.checkOut + "\n" +
        t("Άτομα: ", "Guests: ") + payload.guests + "\n" +
        t("Σύνολο (εκτίμηση): ", "Total (estimate): ") + total() + " €\n\n" +
        payload.name + " · " + payload.email + (payload.phone ? " · " + payload.phone : "");
      var href = "mailto:info@example.com?subject=" + encodeURIComponent(subject) + "&body=" + encodeURIComponent(body);
      document.getElementById("fallbackMail").href = href;
      window.location.href = href;
      showMsg(t("Θα σας απαντήσουμε άμεσα για την επιβεβαίωση της κράτησης.", "We will get back to you shortly to confirm your booking."), "ok");
    }
  });

  /* ---------- Payment result (return from Stripe) ---------- */
  var params = new URLSearchParams(window.location.search);
  if (params.get("booking") === "success") {
    showMsg(t("Η κράτησή σας επιβεβαιώθηκε! Δεν χρεωθήκατε τώρα — η κάρτα σας θα χρεωθεί " + FREE_CANCEL_DAYS + " ημέρες πριν την άφιξη. Θα λάβετε email επιβεβαίωσης.",
             "Your reservation is confirmed! You were not charged now — your card will be charged " + FREE_CANCEL_DAYS + " days before arrival. A confirmation email is on its way."), "ok");
    document.getElementById("booking").scrollIntoView();
  } else if (params.get("booking") === "cancelled") {
    showMsg(t("Η κράτηση ακυρώθηκε. Μπορείτε να δοκιμάσετε ξανά.", "The reservation was cancelled. You can try again."), "error");
    document.getElementById("booking").scrollIntoView();
  }

  /* ---------- Init ---------- */
  loadAvailability(state.unit).then(function () {
    renderCalendar();
    renderSummary();
  });
})();
