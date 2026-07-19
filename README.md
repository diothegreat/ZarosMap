# Μαέρα & Διόνυσος — Ζαρός Κρήτης / Zaros, Crete

Δίγλωσση (ΕΛ/EN) ιστοσελίδα παρουσίασης και κρατήσεων για δύο πετρόκτιστα καταλύματα στον Ζαρό Ηρακλείου, με ενσωματωμένο σύστημα κρατήσεων έτοιμο για πληρωμές Stripe.

## Δομή

```
index.html          Η ιστοσελίδα (μία σελίδα, όλα τα sections)
css/style.css       Στυλ — μινιμαλιστική παλέτα (εκρού, λαδί, τερακότα)
js/main.js          Εναλλαγή γλώσσας ΕΛ/EN, animations, χάρτης περιοχής (Leaflet)
js/booking.js       Ημερολόγιο διαθεσιμότητας, υπολογισμός τιμής, ροή Stripe
server/             Προαιρετικό backend (Node/Express) για κρατήσεις & Stripe
assets/img/         Εδώ μπαίνουν οι πραγματικές φωτογραφίες
assets/video/       Εδώ μπαίνει το βίντεο παρουσίασης (apartments-tour.mp4)
```

Τα αρχεία `Zaros_map_final_color_coded_v2.html` / `ζαρος.html` είναι ο αρχικός αυτόνομος χάρτης της περιοχής (τα σημεία του έχουν ενσωματωθεί στον χάρτη της νέας σελίδας).

## Τοπική προβολή

Η σελίδα είναι στατική — ανοίξτε το `index.html` ή σερβίρετέ το:

```bash
npx serve .            # ή python3 -m http.server
```

Χωρίς backend, το εργαλείο κράτησης λειτουργεί πλήρως (ημερολόγιο, υπολογισμός τιμής) και στέλνει το αίτημα με email αντί για πληρωμή.

## Backend κρατήσεων (προαιρετικό, απαραίτητο για Stripe)

```bash
cd server
npm install
cp .env.example .env   # συμπληρώστε τα κλειδιά όταν είναι διαθέσιμα
npm start              # σερβίρει ΚΑΙ τη σελίδα στο http://localhost:3000
```

Παρέχει:

- `GET /api/availability?unit=maera|dionysos` — κατειλημμένες ημερομηνίες (αποτρέπει διπλοκράτηση· οι εκκρεμείς πληρωμές δεσμεύουν τις ημερομηνίες για 30′)
- `POST /api/checkout` — δημιουργεί Stripe Checkout session· η τιμή υπολογίζεται **πάντα στον server**
- `POST /api/webhook` — επιβεβαιώνει την κράτηση στο `checkout.session.completed` και στέλνει email επιβεβαίωσης (αν έχει ρυθμιστεί SMTP)
- `POST /api/cancel` — δωρεάν ακύρωση (με refund) έως 10 ημέρες πριν την άφιξη

Οι κρατήσεις αποθηκεύονται στο `server/data/bookings.json`.

## Ενεργοποίηση Stripe (όταν δημιουργηθεί ο λογαριασμός)

1. Δημιουργήστε λογαριασμό στο [stripe.com](https://stripe.com) και πάρτε τα κλειδιά (Developers → API keys).
2. Στο `server/.env` συμπληρώστε `STRIPE_SECRET_KEY` (και προαιρετικά `STRIPE_PUBLISHABLE_KEY`).
3. Στο Stripe Dashboard → Developers → Webhooks προσθέστε endpoint `https://your-domain.com/api/webhook` με events `checkout.session.completed`, `checkout.session.expired`, και βάλτε το `STRIPE_WEBHOOK_SECRET` στο `.env`.
4. Επανεκκινήστε τον server — **τίποτα άλλο δεν χρειάζεται αλλαγή**: το frontend ρωτά το `/api/config` και «ανάβει» αυτόματα το κουμπί πληρωμής.

Δοκιμάστε πρώτα με test keys (`sk_test_...`) και την κάρτα `4242 4242 4242 4242`.

## Τιμολόγηση (ορίζεται σε `js/booking.js` **και** `server/server.js`)

| Κατάλυμα | Χωρητικότητα | Βασική τιμή (2 άτομα) | Ανά επιπλέον άτομο |
|---|---|---|---|
| Μαέρα | έως 4 | 50 € / διαν. | +10 € |
| Διόνυσος | έως 3 | 60 € / διαν. | +10 € |

Καθαρισμός 15 € εφάπαξ · Δωρεάν ακύρωση έως 10 ημέρες πριν την άφιξη.

## Πριν τη δημοσίευση (checklist)

- [ ] Αντικαταστήστε τα placeholders φωτογραφιών (`.ph` tiles) με πραγματικές φωτογραφίες στο `assets/img/`
- [ ] Ανεβάστε το βίντεο ως `assets/video/apartments-tour.mp4` (+ poster `tour-poster.jpg`)
- [ ] Αλλάξτε το τηλέφωνο (`tel:+30...`) και το email (`info@example.com`) στα `index.html`, `js/main.js`, `js/booking.js`
- [ ] Επιβεβαιώστε τις ακριβείς συντεταγμένες του καταλύματος στον χάρτη (`js/main.js`, σημείο "stay") και στο Google Maps embed
- [ ] Επιβεβαιώστε αποστάσεις/χρόνους του οδηγού περιοχής
- [ ] Ενεργοποιήστε το Stripe (βλ. παραπάνω)
