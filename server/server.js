/* ============================================================
   Μαέρα & Διόνυσος — Booking & Stripe backend
   Zaros, Heraklion, Crete

   Stripe is NOT active yet. The server runs fine without keys:
   - GET  /api/config        -> { stripeEnabled: false } until keys are set
   - GET  /api/availability  -> booked dates per unit (prevents double booking)
   - POST /api/checkout      -> creates a Stripe Checkout session (when enabled)
   - POST /api/webhook       -> confirms bookings on payment success
   - POST /api/cancel        -> free cancellation up to 10 days before arrival

   To activate payments, set in .env (see .env.example):
     STRIPE_SECRET_KEY=sk_test_... (or sk_live_...)
     STRIPE_WEBHOOK_SECRET=whsec_...
   Nothing else needs to change — the frontend picks it up automatically.
   ============================================================ */

require("dotenv").config();
const express = require("express");
const fs = require("fs");
const path = require("path");

const app = express();
const PORT = process.env.PORT || 3000;
const SITE_URL = process.env.SITE_URL || `http://localhost:${PORT}`;

const STRIPE_KEY = process.env.STRIPE_SECRET_KEY || "";
const stripe = STRIPE_KEY ? require("stripe")(STRIPE_KEY) : null;

/* ---------- Pricing policy (single source of truth) ---------- */
const UNITS = {
  maera:    { nameEl: "Μαέρα",    nameEn: "Maera",    base: 50, maxGuests: 4 },
  dionysos: { nameEl: "Διόνυσος", nameEn: "Dionysos", base: 60, maxGuests: 3 }
};
const EXTRA_GUEST = 10;   // € per extra guest per night (beyond 2)
const CLEANING = 15;      // € one-off
const FREE_CANCEL_DAYS = 10;

/* ---------- Tiny JSON datastore ---------- */
const DATA_DIR = path.join(__dirname, "data");
const DB_FILE = path.join(DATA_DIR, "bookings.json");

function loadBookings() {
  try {
    return JSON.parse(fs.readFileSync(DB_FILE, "utf8"));
  } catch {
    return [];
  }
}
function saveBookings(bookings) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  fs.writeFileSync(DB_FILE, JSON.stringify(bookings, null, 2));
}

/* ---------- Date helpers ---------- */
const ISO_RE = /^\d{4}-\d{2}-\d{2}$/;
function parseISO(s) {
  if (!ISO_RE.test(s)) return null;
  const d = new Date(s + "T00:00:00Z");
  return isNaN(d) ? null : d;
}
function nightsBetween(a, b) {
  return Math.round((b - a) / 86400000);
}
function* eachNight(checkIn, checkOut) {
  for (let d = new Date(checkIn); d < checkOut; d.setUTCDate(d.getUTCDate() + 1)) {
    yield d.toISOString().slice(0, 10);
  }
}

/* Bookings that block dates: paid, or pending & created < 30 min ago
   (a pending hold while the guest is on the Stripe Checkout page). */
function blockingBookings(bookings, unit) {
  const now = Date.now();
  return bookings.filter(b =>
    b.unit === unit &&
    b.status !== "cancelled" &&
    (b.status === "paid" || now - new Date(b.createdAt).getTime() < 30 * 60 * 1000)
  );
}
function bookedDateSet(bookings, unit) {
  const set = new Set();
  for (const b of blockingBookings(bookings, unit)) {
    for (const night of eachNight(parseISO(b.checkIn), parseISO(b.checkOut))) set.add(night);
  }
  return set;
}

function computeTotal(unit, guests, nights) {
  const u = UNITS[unit];
  return nights * (u.base + Math.max(0, guests - 2) * EXTRA_GUEST) + CLEANING;
}

/* ---------- Optional confirmation email ---------- */
async function sendConfirmationEmail(booking) {
  const { SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_FROM } = process.env;
  if (!SMTP_HOST) return; // email not configured — skip silently
  const nodemailer = require("nodemailer");
  const transport = nodemailer.createTransport({
    host: SMTP_HOST,
    port: Number(SMTP_PORT || 587),
    secure: Number(SMTP_PORT) === 465,
    auth: SMTP_USER ? { user: SMTP_USER, pass: SMTP_PASS } : undefined
  });
  const en = booking.lang === "en";
  const u = UNITS[booking.unit];
  await transport.sendMail({
    from: MAIL_FROM || SMTP_USER,
    to: booking.email,
    bcc: process.env.MAIL_OWNER || undefined,
    subject: en
      ? `Booking confirmed — ${u.nameEn}, Zaros`
      : `Επιβεβαίωση κράτησης — ${u.nameEl}, Ζαρός`,
    text: en
      ? `Dear ${booking.name},\n\nYour booking is confirmed!\n\nApartment: ${u.nameEn}\nCheck-in: ${booking.checkIn}\nCheck-out: ${booking.checkOut}\nGuests: ${booking.guests}\nTotal paid: €${booking.total}\n\nFree cancellation up to ${FREE_CANCEL_DAYS} days before arrival.\nPepi will personally welcome you on arrival.\n\nSee you in Zaros!`
      : `Αγαπητέ/ή ${booking.name},\n\nΗ κράτησή σας επιβεβαιώθηκε!\n\nΚατάλυμα: ${u.nameEl}\nΆφιξη: ${booking.checkIn}\nΑναχώρηση: ${booking.checkOut}\nΆτομα: ${booking.guests}\nΣύνολο: ${booking.total} €\n\nΔωρεάν ακύρωση έως ${FREE_CANCEL_DAYS} ημέρες πριν την άφιξη.\nΗ Πέπη θα σας υποδεχτεί προσωπικά κατά την άφιξη.\n\nΚαλή αντάμωση στον Ζαρό!`
  }).catch(err => console.error("Email failed:", err.message));
}

/* ============================================================
   Routes
   ============================================================ */

/* Stripe webhook needs the raw body — register BEFORE express.json() */
app.post("/api/webhook", express.raw({ type: "application/json" }), async (req, res) => {
  if (!stripe) return res.status(503).end();
  let event;
  try {
    const sig = req.headers["stripe-signature"];
    if (process.env.STRIPE_WEBHOOK_SECRET) {
      event = stripe.webhooks.constructEvent(req.body, sig, process.env.STRIPE_WEBHOOK_SECRET);
    } else {
      event = JSON.parse(req.body); // dev mode without webhook secret
    }
  } catch (err) {
    return res.status(400).send(`Webhook error: ${err.message}`);
  }

  if (event.type === "checkout.session.completed") {
    const session = event.data.object;
    const bookings = loadBookings();
    const booking = bookings.find(b => b.sessionId === session.id);
    if (booking && booking.status !== "paid") {
      booking.status = "paid";
      booking.paidAt = new Date().toISOString();
      booking.paymentIntent = session.payment_intent;
      saveBookings(bookings);
      sendConfirmationEmail(booking);
    }
  }
  if (event.type === "checkout.session.expired") {
    const session = event.data.object;
    const bookings = loadBookings();
    const booking = bookings.find(b => b.sessionId === session.id);
    if (booking && booking.status === "pending") {
      booking.status = "cancelled";
      saveBookings(bookings);
    }
  }
  res.json({ received: true });
});

app.use(express.json());
app.use(express.static(path.join(__dirname, ".."))); // serve the site itself

/* Frontend asks whether Stripe payments are live */
app.get("/api/config", (req, res) => {
  res.json({
    stripeEnabled: Boolean(stripe),
    publishableKey: process.env.STRIPE_PUBLISHABLE_KEY || null
  });
});

/* Booked (unavailable) dates for a unit */
app.get("/api/availability", (req, res) => {
  const unit = req.query.unit;
  if (!UNITS[unit]) return res.status(400).json({ error: "Unknown unit" });
  res.json({ unit, bookedDates: [...bookedDateSet(loadBookings(), unit)].sort() });
});

/* Create a booking + Stripe Checkout session */
app.post("/api/checkout", async (req, res) => {
  try {
    const { unit, checkIn, checkOut, guests, name, email, phone, lang } = req.body || {};

    /* --- validation --- */
    const u = UNITS[unit];
    if (!u) return res.status(400).json({ error: "Unknown unit" });
    const inD = parseISO(checkIn), outD = parseISO(checkOut);
    if (!inD || !outD) return res.status(400).json({ error: "Invalid dates" });
    const nights = nightsBetween(inD, outD);
    if (nights < 1) return res.status(400).json({ error: "Check-out must be after check-in" });
    const today = new Date(new Date().toISOString().slice(0, 10) + "T00:00:00Z");
    if (inD < today) return res.status(400).json({ error: "Check-in is in the past" });
    const g = Number(guests);
    if (!Number.isInteger(g) || g < 1 || g > u.maxGuests) {
      return res.status(400).json({ error: `Guests must be 1–${u.maxGuests}` });
    }
    if (!name || !email) return res.status(400).json({ error: "Name and email are required" });

    /* --- double-booking check --- */
    const bookings = loadBookings();
    const taken = bookedDateSet(bookings, unit);
    for (const night of eachNight(inD, outD)) {
      if (taken.has(night)) {
        return res.status(409).json({ error: "Some of these dates are no longer available" });
      }
    }

    /* --- price computed server-side; the client never sets it --- */
    const total = computeTotal(unit, g, nights);

    if (!stripe) {
      return res.status(503).json({
        error: "Online payment is not activated yet. Please contact us to complete your booking.",
        stripeEnabled: false
      });
    }

    const en = lang === "en";
    const session = await stripe.checkout.sessions.create({
      mode: "payment",
      customer_email: email,
      line_items: [
        {
          price_data: {
            currency: "eur",
            unit_amount: (total - CLEANING) * 100,
            product_data: {
              name: en ? `${u.nameEn} — ${nights} night(s), ${g} guest(s)` : `${u.nameEl} — ${nights} διαν., ${g} άτομα`,
              description: `${checkIn} → ${checkOut}`
            }
          },
          quantity: 1
        },
        {
          price_data: {
            currency: "eur",
            unit_amount: CLEANING * 100,
            product_data: { name: en ? "Cleaning fee (one-off)" : "Καθαρισμός (εφάπαξ)" }
          },
          quantity: 1
        }
      ],
      metadata: { unit, checkIn, checkOut, guests: String(g), name, phone: phone || "" },
      success_url: `${SITE_URL}/?booking=success#booking`,
      cancel_url: `${SITE_URL}/?booking=cancelled#booking`,
      expires_at: Math.floor(Date.now() / 1000) + 30 * 60
    });

    bookings.push({
      id: "bk_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8),
      unit, checkIn, checkOut, guests: g, name, email, phone: phone || "",
      lang: lang || "el",
      total,
      status: "pending",
      sessionId: session.id,
      createdAt: new Date().toISOString()
    });
    saveBookings(bookings);

    res.json({ url: session.url });
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: "Something went wrong. Please try again." });
  }
});

/* Free cancellation up to 10 days before arrival */
app.post("/api/cancel", async (req, res) => {
  try {
    const { bookingId, email } = req.body || {};
    const bookings = loadBookings();
    const booking = bookings.find(b => b.id === bookingId && b.email === email);
    if (!booking) return res.status(404).json({ error: "Booking not found" });
    if (booking.status === "cancelled") return res.json({ ok: true, status: "cancelled" });

    const daysUntil = nightsBetween(new Date(), parseISO(booking.checkIn));
    if (daysUntil < FREE_CANCEL_DAYS) {
      return res.status(403).json({
        error: `Free cancellation is only available up to ${FREE_CANCEL_DAYS} days before arrival.`
      });
    }

    if (booking.status === "paid" && stripe && booking.paymentIntent) {
      await stripe.refunds.create({ payment_intent: booking.paymentIntent });
    }
    booking.status = "cancelled";
    booking.cancelledAt = new Date().toISOString();
    saveBookings(bookings);
    res.json({ ok: true, status: "cancelled", refunded: Boolean(booking.paymentIntent) });
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: "Cancellation failed. Please contact us." });
  }
});

app.listen(PORT, () => {
  console.log(`Zaros booking server on ${SITE_URL}`);
  console.log(`Stripe payments: ${stripe ? "ENABLED" : "disabled (set STRIPE_SECRET_KEY to activate)"}`);
});
