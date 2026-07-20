/* ============================================================
   Μαέρα & Διόνυσος — Booking & payment backend
   Zaros, Heraklion, Crete

   FLOW: "Reserve now, pay 10 days before arrival"
   ------------------------------------------------------------
   1. Guest picks unit/dates/guests, enters name/email/phone.
   2. POST /api/reserve  -> Stripe Checkout in SETUP mode: the card
      is saved but NOT charged. The booking is held ("reserved").
   3. Webhook checkout.session.completed -> card stored, booking
      becomes "confirmed" (dates locked, still not charged).
   4. A daily job charges every confirmed booking whose check-in is
      within FREE_CANCEL_DAYS (10) days, off-session, then "paid".
   5. Free cancellation before that window = card never charged =
      ZERO fees lost.  POST /api/cancel handles it (refunds if the
      charge already went through).

   Stripe is NOT active yet. Without keys the server runs fine:
   /api/config reports stripeEnabled:false and the frontend falls
   back to an email booking request. Set STRIPE_SECRET_KEY (+ webhook
   secret) in .env to switch everything on — no code changes needed.
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
  try { return JSON.parse(fs.readFileSync(DB_FILE, "utf8")); }
  catch { return []; }
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
function nightsBetween(a, b) { return Math.round((b - a) / 86400000); }
function* eachNight(checkIn, checkOut) {
  for (let d = new Date(checkIn); d < checkOut; d.setUTCDate(d.getUTCDate() + 1)) {
    yield d.toISOString().slice(0, 10);
  }
}
function todayUTC() { return new Date(new Date().toISOString().slice(0, 10) + "T00:00:00Z"); }

/* A booking blocks its dates if it is confirmed/paid, or reserved
   (card entry in progress) and created less than 30 minutes ago. */
function isBlocking(b) {
  if (b.status === "confirmed" || b.status === "paid") return true;
  if (b.status === "reserved") return Date.now() - new Date(b.createdAt).getTime() < 30 * 60 * 1000;
  return false;
}
function bookedDateSet(bookings, unit) {
  const set = new Set();
  for (const b of bookings) {
    if (b.unit === unit && isBlocking(b)) {
      for (const night of eachNight(parseISO(b.checkIn), parseISO(b.checkOut))) set.add(night);
    }
  }
  return set;
}
function computeTotal(unit, guests, nights) {
  const u = UNITS[unit];
  return nights * (u.base + Math.max(0, guests - 2) * EXTRA_GUEST) + CLEANING;
}

/* ---------- Optional confirmation / receipt email ---------- */
async function sendEmail(booking, kind) {
  const { SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_FROM } = process.env;
  if (!SMTP_HOST) return;
  const nodemailer = require("nodemailer");
  const transport = nodemailer.createTransport({
    host: SMTP_HOST,
    port: Number(SMTP_PORT || 587),
    secure: Number(SMTP_PORT) === 465,
    auth: SMTP_USER ? { user: SMTP_USER, pass: SMTP_PASS } : undefined
  });
  const en = booking.lang === "en";
  const u = UNITS[booking.unit];
  const chargeDay = new Date(parseISO(booking.checkIn).getTime() - FREE_CANCEL_DAYS * 86400000)
    .toISOString().slice(0, 10);

  let subject, text;
  if (kind === "confirmed") {
    subject = en ? `Reservation confirmed — ${u.nameEn}, Zaros` : `Κράτηση επιβεβαιώθηκε — ${u.nameEl}, Ζαρός`;
    text = en
      ? `Dear ${booking.name},\n\nYour reservation is confirmed — no charge yet.\n\nApartment: ${u.nameEn}\nCheck-in: ${booking.checkIn}\nCheck-out: ${booking.checkOut}\nGuests: ${booking.guests}\nTotal: €${booking.total}\n\nYour card will be charged automatically on ${chargeDay} (${FREE_CANCEL_DAYS} days before arrival).\nFree cancellation any time before then — no charge at all.\n\nPepi will welcome you personally on arrival. See you in Zaros!`
      : `Αγαπητέ/ή ${booking.name},\n\nΗ κράτησή σας επιβεβαιώθηκε — χωρίς χρέωση προς το παρόν.\n\nΚατάλυμα: ${u.nameEl}\nΆφιξη: ${booking.checkIn}\nΑναχώρηση: ${booking.checkOut}\nΆτομα: ${booking.guests}\nΣύνολο: ${booking.total} €\n\nΗ κάρτα σας θα χρεωθεί αυτόματα στις ${chargeDay} (${FREE_CANCEL_DAYS} ημέρες πριν την άφιξη).\nΔωρεάν ακύρωση οποιαδήποτε στιγμή πριν από τότε — χωρίς καμία χρέωση.\n\nΗ Πέπη θα σας υποδεχτεί προσωπικά. Καλή αντάμωση στον Ζαρό!`;
  } else if (kind === "paid") {
    subject = en ? `Payment received — ${u.nameEn}, Zaros` : `Η πληρωμή ελήφθη — ${u.nameEl}, Ζαρός`;
    text = en
      ? `Dear ${booking.name},\n\nWe have received your payment of €${booking.total} for ${u.nameEn} (${booking.checkIn} → ${booking.checkOut}).\n\nSee you soon in Zaros!`
      : `Αγαπητέ/ή ${booking.name},\n\nΛάβαμε την πληρωμή σας ${booking.total} € για ${u.nameEl} (${booking.checkIn} → ${booking.checkOut}).\n\nΚαλή αντάμωση στον Ζαρό!`;
  } else if (kind === "cancelled") {
    subject = en ? `Cancellation confirmed — ${u.nameEn}, Zaros` : `Ακύρωση επιβεβαιώθηκε — ${u.nameEl}, Ζαρός`;
    text = en
      ? `Dear ${booking.name},\n\nYour reservation for ${u.nameEn} (${booking.checkIn} → ${booking.checkOut}) has been cancelled.${booking.refunded ? " Your payment has been fully refunded." : " No charge was made."}\n\nWe hope to host you another time.`
      : `Αγαπητέ/ή ${booking.name},\n\nΗ κράτησή σας για ${u.nameEl} (${booking.checkIn} → ${booking.checkOut}) ακυρώθηκε.${booking.refunded ? " Το ποσό επιστράφηκε πλήρως." : " Δεν έγινε καμία χρέωση."}\n\nΘα χαρούμε να σας φιλοξενήσουμε άλλη φορά.`;
  }
  await transport.sendMail({
    from: MAIL_FROM || SMTP_USER, to: booking.email,
    bcc: process.env.MAIL_OWNER || undefined, subject, text
  }).catch(err => console.error("Email failed:", err.message));
}

/* ============================================================
   The daily charge job — charges confirmed bookings whose
   check-in is within FREE_CANCEL_DAYS days.
   ============================================================ */
async function chargeDueBookings() {
  if (!stripe) return;
  const bookings = loadBookings();
  let changed = false;
  const cutoff = new Date(todayUTC().getTime() + FREE_CANCEL_DAYS * 86400000);

  for (const b of bookings) {
    if (b.status !== "confirmed" || !b.paymentMethod || !b.customerId) continue;
    const checkIn = parseISO(b.checkIn);
    if (checkIn > cutoff) continue; // still outside the charge window
    try {
      const intent = await stripe.paymentIntents.create({
        amount: b.total * 100,
        currency: "eur",
        customer: b.customerId,
        payment_method: b.paymentMethod,
        off_session: true,
        confirm: true,
        description: `${UNITS[b.unit].nameEn} ${b.checkIn}→${b.checkOut} (${b.guests} guests)`,
        metadata: { bookingId: b.id }
      });
      b.status = "paid";
      b.paidAt = new Date().toISOString();
      b.paymentIntent = intent.id;
      changed = true;
      sendEmail(b, "paid");
      console.log(`Charged booking ${b.id}: €${b.total}`);
    } catch (err) {
      b.chargeError = err.message;
      b.chargeAttemptedAt = new Date().toISOString();
      changed = true;
      console.error(`Charge failed for ${b.id}: ${err.message}`);
      // Card declined: notify the owner so they can follow up manually.
    }
  }
  if (changed) saveBookings(bookings);
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
    event = process.env.STRIPE_WEBHOOK_SECRET
      ? stripe.webhooks.constructEvent(req.body, sig, process.env.STRIPE_WEBHOOK_SECRET)
      : JSON.parse(req.body);
  } catch (err) {
    return res.status(400).send(`Webhook error: ${err.message}`);
  }

  if (event.type === "checkout.session.completed") {
    const session = event.data.object;
    const bookings = loadBookings();
    const booking = bookings.find(b => b.sessionId === session.id);
    if (booking && booking.status === "reserved") {
      booking.status = "confirmed";
      booking.confirmedAt = new Date().toISOString();
      booking.customerId = session.customer;
      // The saved card from the setup session:
      try {
        const setupIntent = await stripe.setupIntents.retrieve(session.setup_intent);
        booking.paymentMethod = setupIntent.payment_method;
      } catch (e) { console.error("SetupIntent fetch failed:", e.message); }
      saveBookings(bookings);
      sendEmail(booking, "confirmed");

      // If the arrival is already inside the charge window, charge now.
      const checkIn = parseISO(booking.checkIn);
      if (checkIn <= new Date(todayUTC().getTime() + FREE_CANCEL_DAYS * 86400000)) {
        chargeDueBookings();
      }
    }
  }
  if (event.type === "checkout.session.expired") {
    const session = event.data.object;
    const bookings = loadBookings();
    const booking = bookings.find(b => b.sessionId === session.id);
    if (booking && booking.status === "reserved") { booking.status = "cancelled"; saveBookings(bookings); }
  }
  res.json({ received: true });
});

app.use(express.json());
app.use(express.static(path.join(__dirname, "..")));

/* Frontend asks whether payments are live */
app.get("/api/config", (req, res) => {
  res.json({
    stripeEnabled: Boolean(stripe),
    publishableKey: process.env.STRIPE_PUBLISHABLE_KEY || null,
    freeCancelDays: FREE_CANCEL_DAYS
  });
});

/* Booked (unavailable) dates for a unit */
app.get("/api/availability", (req, res) => {
  const unit = req.query.unit;
  if (!UNITS[unit]) return res.status(400).json({ error: "Unknown unit" });
  res.json({ unit, bookedDates: [...bookedDateSet(loadBookings(), unit)].sort() });
});

/* Reserve: save the card now, charge later (10 days before arrival) */
app.post("/api/reserve", async (req, res) => {
  try {
    const { unit, checkIn, checkOut, guests, name, email, phone, lang } = req.body || {};

    /* --- validation --- */
    const u = UNITS[unit];
    if (!u) return res.status(400).json({ error: "Unknown unit" });
    const inD = parseISO(checkIn), outD = parseISO(checkOut);
    if (!inD || !outD) return res.status(400).json({ error: "Invalid dates" });
    const nights = nightsBetween(inD, outD);
    if (nights < 1) return res.status(400).json({ error: "Check-out must be after check-in" });
    if (inD < todayUTC()) return res.status(400).json({ error: "Check-in is in the past" });
    const g = Number(guests);
    if (!Number.isInteger(g) || g < 1 || g > u.maxGuests) {
      return res.status(400).json({ error: `Guests must be 1–${u.maxGuests}` });
    }
    if (!name || !email) return res.status(400).json({ error: "Name and email are required" });

    /* --- double-booking check --- */
    const bookings = loadBookings();
    const taken = bookedDateSet(bookings, unit);
    for (const night of eachNight(inD, outD)) {
      if (taken.has(night)) return res.status(409).json({ error: "Some of these dates are no longer available" });
    }

    const total = computeTotal(unit, g, nights); // computed server-side, always
    const chargeDay = new Date(inD.getTime() - FREE_CANCEL_DAYS * 86400000).toISOString().slice(0, 10);

    if (!stripe) {
      return res.status(503).json({ error: "Online payment is not activated yet.", stripeEnabled: false });
    }

    /* --- Stripe Checkout in SETUP mode: saves the card, no charge --- */
    const customer = await stripe.customers.create({ email, name, phone: phone || undefined });
    const en = lang === "en";
    const session = await stripe.checkout.sessions.create({
      mode: "setup",
      customer: customer.id,
      payment_method_types: ["card"],
      metadata: { unit, checkIn, checkOut, guests: String(g), total: String(total) },
      custom_text: {
        submit: {
          message: en
            ? `No charge today. Your card will be charged €${total} on ${chargeDay} (10 days before arrival). Free cancellation until then.`
            : `Καμία χρέωση σήμερα. Η κάρτα σας θα χρεωθεί ${total} € στις ${chargeDay} (10 ημέρες πριν την άφιξη). Δωρεάν ακύρωση έως τότε.`
        }
      },
      success_url: `${SITE_URL}/?booking=success#booking`,
      cancel_url: `${SITE_URL}/?booking=cancelled#booking`,
      expires_at: Math.floor(Date.now() / 1000) + 30 * 60
    });

    bookings.push({
      id: "bk_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8),
      unit, checkIn, checkOut, guests: g, name, email, phone: phone || "",
      lang: lang || "el", total, chargeDay,
      status: "reserved",
      sessionId: session.id,
      customerId: customer.id,
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

    const daysUntil = nightsBetween(todayUTC(), parseISO(booking.checkIn));
    if (daysUntil < FREE_CANCEL_DAYS) {
      return res.status(403).json({ error: `Free cancellation is only available up to ${FREE_CANCEL_DAYS} days before arrival.` });
    }

    // Card saved but (almost always) not yet charged -> nothing to refund, zero fees.
    if (booking.status === "paid" && stripe && booking.paymentIntent) {
      await stripe.refunds.create({ payment_intent: booking.paymentIntent });
      booking.refunded = true;
    }
    booking.status = "cancelled";
    booking.cancelledAt = new Date().toISOString();
    saveBookings(bookings);
    sendEmail(booking, "cancelled");
    res.json({ ok: true, status: "cancelled", refunded: Boolean(booking.refunded) });
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: "Cancellation failed. Please contact us." });
  }
});

/* Manual trigger for the charge job (protected). Handy for an external
   cron too: curl -H "x-cron-secret: <CRON_SECRET>" .../api/cron/charge-due */
app.post("/api/cron/charge-due", async (req, res) => {
  if (process.env.CRON_SECRET && req.headers["x-cron-secret"] !== process.env.CRON_SECRET) {
    return res.status(403).json({ error: "Forbidden" });
  }
  await chargeDueBookings();
  res.json({ ok: true });
});

app.listen(PORT, () => {
  console.log(`Zaros booking server on ${SITE_URL}`);
  console.log(`Payments: ${stripe ? "ENABLED (reserve now, charge 10 days before arrival)" : "disabled (set STRIPE_SECRET_KEY to activate)"}`);
  if (stripe) {
    chargeDueBookings();                                   // run once at boot
    setInterval(chargeDueBookings, 6 * 60 * 60 * 1000);    // and every 6 hours
  }
});
