/* ============================================================
   Μαέρα & Διόνυσος — booking.js
   Professional booking widget: dual-month availability calendar,
   live price breakdown, "reserve now / pay 10 days before arrival"
   via Stripe (with graceful email fallback until Stripe is live).

   Pricing (mirrors server/server.js):
     Maera    €50/night (2 guests), max 4
     Dionysos €60/night (2 guests), max 3
     +€10 per extra guest / night · €15 one-off cleaning
   ============================================================ */

(function () {
  "use strict";

  var API_BASE = "";
  var UNITS = {
    maera:    { nameEl: "Μαέρα",    nameEn: "Maera",    base: 50, maxGuests: 4 },
    dionysos: { nameEl: "Διόνυσος", nameEn: "Dionysos", base: 60, maxGuests: 3 }
  };
  var EXTRA_GUEST = 10, CLEANING = 15, FREE_CANCEL_DAYS = 10;

  var state = {
    unit: "maera",
    guests: 2,
    checkIn: null,
    checkOut: null,
    month: new Date(new Date().getFullYear(), new Date().getMonth(), 1),
    booked: {},
    stripeEnabled: null
  };

  /* ---------- Helpers ---------- */
  function iso(d) {
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  function lang() { return window.getLang ? window.getLang() : "el"; }
  function t(el, en) { return lang() === "en" ? en : el; }
  function fmt(d) {
    return d.toLocaleDateString(lang() === "en" ? "en-GB" : "el-GR", { day: "numeric", month: "short" });
  }
  function nights(a, b) { return Math.round((b - a) / 86400000); }
  function nightly() { return UNITS[state.unit].base + Math.max(0, state.guests - 2) * EXTRA_GUEST; }
  function total() {
    if (!state.checkIn || !state.checkOut) return null;
    return nights(state.checkIn, state.checkOut) * nightly() + CLEANING;
  }

  /* ---------- Availability ---------- */
  function loadAvailability(unit) {
    return fetch(API_BASE + "/api/availability?unit=" + unit)
      .then(function (r) { if (!r.ok) throw new Error(); return r.json(); })
      .then(function (data) { state.booked[unit] = new Set(data.bookedDates || []); })
      .catch(function () { state.booked[unit] = state.booked[unit] || new Set(); });
  }
  function isBooked(d) { var s = state.booked[state.unit]; return s ? s.has(iso(d)) : false; }
  function rangeIsFree(a, b) {
    for (var d = new Date(a); d < b; d.setDate(d.getDate() + 1)) if (isBooked(d)) return false;
    return true;
  }

  /* ---------- Calendar (dual month) ---------- */
  var calMonths = document.getElementById("calMonths");
  var calHint = document.getElementById("calHint");
  var DOW = { el: ["Δε", "Τρ", "Τε", "Πε", "Πα", "Σα", "Κυ"], en: ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"] };

  function buildMonth(base, offset) {
    var m = new Date(base.getFullYear(), base.getMonth() + offset, 1);
    var wrap = document.createElement("div");
    wrap.className = "cp-month" + (offset === 1 ? " second" : "");
    var title = document.createElement("div");
    title.className = "cm-title";
    title.textContent = m.toLocaleDateString(lang() === "en" ? "en-GB" : "el-GR", { month: "long", year: "numeric" });
    wrap.appendChild(title);

    var grid = document.createElement("div");
    grid.className = "cm-grid";
    (lang() === "en" ? DOW.en : DOW.el).forEach(function (d) {
      var c = document.createElement("div"); c.className = "cal-dow"; c.textContent = d; grid.appendChild(c);
    });

    var first = new Date(m.getFullYear(), m.getMonth(), 1);
    var startOffset = (first.getDay() + 6) % 7;
    var days = new Date(m.getFullYear(), m.getMonth() + 1, 0).getDate();
    var today = new Date(); today.setHours(0, 0, 0, 0);

    for (var i = 0; i < startOffset; i++) grid.appendChild(document.createElement("div"));

    for (var day = 1; day <= days; day++) {
      var d = new Date(m.getFullYear(), m.getMonth(), day);
      var btn = document.createElement("button");
      btn.type = "button"; btn.className = "cal-day"; btn.textContent = day;
      var past = d < today, booked = isBooked(d);
      if (past) { btn.disabled = true; btn.classList.add("past"); }
      if (booked) { btn.disabled = true; btn.classList.add("booked"); }
      if (state.checkIn && iso(d) === iso(state.checkIn)) btn.classList.add("range-start");
      if (state.checkOut && iso(d) === iso(state.checkOut)) btn.classList.add("range-end");
      if (state.checkIn && state.checkOut && d > state.checkIn && d < state.checkOut) btn.classList.add("in-range");
      (function (date) { btn.addEventListener("click", function () { pickDate(date); }); })(d);
      grid.appendChild(btn);
    }
    wrap.appendChild(grid);
    return wrap;
  }

  function renderCalendar() {
    calMonths.innerHTML = "";
    calMonths.appendChild(buildMonth(state.month, 0));
    calMonths.appendChild(buildMonth(state.month, 1));
    if (!state.checkIn) calHint.textContent = t("Επιλέξτε ημερομηνία άφιξης.", "Select your check-in date.");
    else if (!state.checkOut) calHint.textContent = t("Τώρα επιλέξτε αναχώρηση.", "Now select your check-out date.");
    else calHint.textContent = nights(state.checkIn, state.checkOut) + " " + t("διανυκτερεύσεις επιλέχθηκαν.", "nights selected.");
  }

  function pickDate(d) {
    if (!state.checkIn || (state.checkIn && state.checkOut)) {
      state.checkIn = d; state.checkOut = null;
    } else if (d > state.checkIn) {
      if (rangeIsFree(state.checkIn, d)) state.checkOut = d;
      else { state.checkIn = d; state.checkOut = null; }
    } else {
      state.checkIn = d; state.checkOut = null;
    }
    renderCalendar(); renderCard();
  }

  document.getElementById("calPrev").addEventListener("click", function () {
    var now = new Date(); now.setDate(1); now.setHours(0, 0, 0, 0);
    var prev = new Date(state.month.getFullYear(), state.month.getMonth() - 1, 1);
    if (prev >= now) { state.month = prev; renderCalendar(); }
  });
  document.getElementById("calNext").addEventListener("click", function () {
    state.month = new Date(state.month.getFullYear(), state.month.getMonth() + 1, 1);
    renderCalendar();
  });

  /* ---------- Unit tabs ---------- */
  document.querySelectorAll(".unit-tab").forEach(function (tab) {
    tab.addEventListener("click", function () {
      document.querySelectorAll(".unit-tab").forEach(function (b) { b.classList.remove("active"); });
      tab.classList.add("active");
      state.unit = tab.dataset.unit;
      state.checkIn = state.checkOut = null;
      if (state.guests > UNITS[state.unit].maxGuests) state.guests = UNITS[state.unit].maxGuests;
      document.getElementById("guestsCount").textContent = state.guests;
      loadAvailability(state.unit).then(function () { renderCalendar(); renderCard(); });
    });
  });
  // Apartment-card "Book X" buttons preselect the unit
  document.querySelectorAll('a[data-unit]').forEach(function (a) {
    a.addEventListener("click", function () {
      var tab = document.querySelector('.unit-tab[data-unit="' + a.dataset.unit + '"]');
      if (tab) tab.click();
    });
  });

  /* ---------- Guests ---------- */
  var guestsCount = document.getElementById("guestsCount");
  document.getElementById("guestsMinus").addEventListener("click", function () {
    if (state.guests > 1) { state.guests--; guestsCount.textContent = state.guests; renderCard(); }
  });
  document.getElementById("guestsPlus").addEventListener("click", function () {
    if (state.guests < UNITS[state.unit].maxGuests) { state.guests++; guestsCount.textContent = state.guests; renderCard(); }
  });

  /* ---------- Reservation card ---------- */
  var rcIn = document.getElementById("rcIn"), rcOut = document.getElementById("rcOut");
  var rcFieldIn = document.getElementById("rcFieldIn"), rcFieldOut = document.getElementById("rcFieldOut");
  var rcBreakdown = document.getElementById("rcBreakdown");
  var guestDetails = document.getElementById("guestDetails");

  function renderCard() {
    document.getElementById("rcNightly").innerHTML = nightly() + "&nbsp;€";

    // dates
    if (state.checkIn) { rcIn.textContent = fmt(state.checkIn); rcFieldIn.classList.add("filled"); }
    else { rcIn.textContent = t("Επιλογή", "Add date"); rcFieldIn.classList.remove("filled"); }
    if (state.checkOut) { rcOut.textContent = fmt(state.checkOut); rcFieldOut.classList.add("filled"); }
    else { rcOut.textContent = t("Επιλογή", "Add date"); rcFieldOut.classList.remove("filled"); }

    // which field is "active" (next to fill)
    rcFieldIn.classList.toggle("active-field", !state.checkIn);
    rcFieldOut.classList.toggle("active-field", !!state.checkIn && !state.checkOut);

    // breakdown + guest details
    if (state.checkIn && state.checkOut) {
      var n = nights(state.checkIn, state.checkOut);
      document.getElementById("rcNightsLabel").textContent =
        nightly() + " € × " + n + " " + t("διαν.", "nights") + " (" + state.guests + " " + t("άτ.", "guests") + ")";
      document.getElementById("rcNightsVal").innerHTML = (n * nightly()) + "&nbsp;€";
      document.getElementById("sumTotal").innerHTML = total() + "&nbsp;€";
      rcBreakdown.hidden = false;
      guestDetails.hidden = false;
    } else {
      rcBreakdown.hidden = true;
      guestDetails.hidden = true;
    }
  }
  document.addEventListener("langchange", function () { renderCalendar(); renderCard(); });

  /* ---------- Stripe config ---------- */
  var payBtn = document.getElementById("payBtn");
  var fallback = document.getElementById("bookingFallback");
  var reassure = document.getElementById("rcReassure");
  var msgEl = document.getElementById("bookingMsg");

  fetch(API_BASE + "/api/config")
    .then(function (r) { if (!r.ok) throw new Error(); return r.json(); })
    .then(function (cfg) { state.stripeEnabled = !!cfg.stripeEnabled; if (cfg.freeCancelDays) FREE_CANCEL_DAYS = cfg.freeCancelDays; })
    .catch(function () { state.stripeEnabled = false; })
    .then(function () {
      if (state.stripeEnabled) {
        setBtn("Επιβεβαίωση κράτησης", "Confirm reservation");
        reassure.hidden = false;
      } else {
        setBtn("Αίτημα κράτησης", "Request booking");
        reassure.hidden = true;
        fallback.hidden = false;
      }
    });
  function setBtn(el, en) { payBtn.textContent = t(el, en); payBtn.dataset.el = el; payBtn.dataset.en = en; }

  function showMsg(text, cls) { msgEl.textContent = text; msgEl.className = "booking-msg " + cls; msgEl.hidden = false; }

  /* ---------- Submit ---------- */
  document.getElementById("bookingForm").addEventListener("submit", function (e) {
    e.preventDefault();
    msgEl.hidden = true;
    if (!state.checkIn || !state.checkOut) {
      showMsg(t("Επιλέξτε ημερομηνίες άφιξης και αναχώρησης.", "Please select check-in and check-out dates."), "error");
      document.getElementById("booking").scrollIntoView({ behavior: "smooth" });
      return;
    }
    var name = document.getElementById("bkName").value.trim();
    var email = document.getElementById("bkEmail").value.trim();
    if (!name || !email) {
      showMsg(t("Συμπληρώστε όνομα και email.", "Please enter your name and email."), "error");
      return;
    }
    var payload = {
      unit: state.unit, checkIn: iso(state.checkIn), checkOut: iso(state.checkOut),
      guests: state.guests, name: name, email: email,
      phone: document.getElementById("bkPhone").value.trim(), lang: lang()
    };

    if (state.stripeEnabled) {
      payBtn.disabled = true;
      setBtn("Ανακατεύθυνση…", "Redirecting…");
      fetch(API_BASE + "/api/reserve", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
      })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          if (res.ok && res.j.url) window.location.href = res.j.url;
          else throw new Error(res.j.error || "reservation failed");
        })
        .catch(function (err) {
          payBtn.disabled = false;
          setBtn("Επιβεβαίωση κράτησης", "Confirm reservation");
          showMsg(t("Η κράτηση δεν ολοκληρώθηκε: ", "Reservation could not start: ") + err.message, "error");
        });
    } else {
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
      showMsg(t("Θα σας απαντήσουμε άμεσα για την επιβεβαίωση.", "We will get back to you shortly to confirm."), "ok");
    }
  });

  /* ---------- Return from Stripe ---------- */
  var params = new URLSearchParams(window.location.search);
  if (params.get("booking") === "success") {
    showMsg(t("Η κράτησή σας επιβεβαιώθηκε! Δεν χρεωθήκατε τώρα — η κάρτα θα χρεωθεί " + FREE_CANCEL_DAYS + " ημέρες πριν την άφιξη. Θα λάβετε email επιβεβαίωσης.",
             "Your reservation is confirmed! You were not charged now — your card will be charged " + FREE_CANCEL_DAYS + " days before arrival. A confirmation email is on its way."), "ok");
    setTimeout(function () { document.getElementById("booking").scrollIntoView(); }, 200);
  } else if (params.get("booking") === "cancelled") {
    showMsg(t("Η κράτηση ακυρώθηκε. Μπορείτε να δοκιμάσετε ξανά.", "The reservation was cancelled. You can try again."), "error");
    setTimeout(function () { document.getElementById("booking").scrollIntoView(); }, 200);
  }

  /* ---------- Init ---------- */
  loadAvailability(state.unit).then(function () { renderCalendar(); renderCard(); });
})();
