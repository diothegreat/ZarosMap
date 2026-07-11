# NAS API για το TAXIS Workbench

Ανακατασκευή του server της πόρτας **8787** που τρέχει στο Synology NAS και
εξυπηρετεί το TAXIS Workbench (desktop) και το Office Control Panel (VPS).

## Τι κάνει

- Διαβάζει τα στοιχεία πελατών από το
  `/volume2/γραφείο2/Aρχείο γραφείου/corrected_output_file.json`
  (και το ξαναδιαβάζει αυτόματα όταν αλλάξει).
- Κρατά ιστορικό ενεργειών (audit events) σε SQLite (`nas_api_events.db`)
  δίπλα στο script — από εκεί βγαίνουν τα «Πρόσφατα δουλεμένοι»,
  «Προβληματικά logins», «Ιστορικό πελάτη» και το «Σήμερα».
- Μόνο Python stdlib — δεν χρειάζεται pip.

## Endpoints

| Endpoint | Token; |
|---|---|
| `GET /health` | — |
| `GET /dashboard/stats` | — |
| `GET /dashboard/client-history/{folder}?limit=` | — |
| `GET /dashboard/missing-clients?limit=` | — |
| `GET /dashboard/recent-worked?limit=` | — |
| `GET /dashboard/problem-logins?limit=` | — |
| `GET /dashboard/latest-folders?limit=` | — |
| `GET /credentials/{folder}/readiness` | — |
| `GET /clients/search?q=&limit=` | — |
| `GET /clients/{folder}` (χωρίς password) | — |
| `GET /internal/launch-payload/{folder}` (με password) | ✔ |
| `POST /internal/audit/event` | ✔ |
| `POST /internal/import/excel` | ✔ |
| `POST /dashboard/recent-worked/clear` | ✔ |

Το token δίνεται με header `X-Internal-Token` και διαβάζεται από το αρχείο
`internal_token.txt` δίπλα στο script (ΔΕΝ είναι μέσα στον κώδικα).

## Εγκατάσταση στο Synology

```sh
mkdir -p "/volume1/Γραφείο/nas_api"
cd "/volume1/Γραφείο/nas_api"
curl -fsSL "https://raw.githubusercontent.com/diothegreat/ZarosMap/claude/nas-server-program-error-eypn4b/nas_api/nas_api.py" -o nas_api.py
curl -fsSL "https://raw.githubusercontent.com/diothegreat/ZarosMap/claude/nas-server-program-error-eypn4b/nas_api/start_nas_api.sh" -o start_nas_api.sh
chmod +x start_nas_api.sh
echo "ΤΟ_TOKEN_ΕΔΩ" > internal_token.txt
sh start_nas_api.sh
```

Μετά, στο DSM → Πίνακας Ελέγχου → Χρονοδιάγραμμα εργασιών, φτιάξε **δύο**
εργασίες (user: root) που τρέχουν
`sh "/volume1/Γραφείο/nas_api/start_nas_api.sh"`:

1. **Triggered Task → Boot-up** — εκκίνηση σε κάθε boot.
2. **Scheduled Task → κάθε ώρα** — αυτο-επανεκκίνηση αν πέσει (το script
   δεν κάνει τίποτα αν ο server ήδη τρέχει).
