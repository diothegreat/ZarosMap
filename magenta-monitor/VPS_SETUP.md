# Πρόσβαση μέσω VPS (χωρίς domain)

Το video παίζει **πάντα στο PC σου**. Το VPS απλώς κάνει τη σελίδα ελέγχου
(dropdown, ήχος, πλήρης οθόνη) προσβάσιμη από κινητό ή άλλο υπολογιστή:

```
κινητό ──https──► VPS (nginx :443) ──SSH tunnel──► PC σου (Flask :5000 + Chrome)
```

Κανένας κωδικός VPS δεν αποθηκεύεται πουθενά: η σύνδεση γίνεται με κλειδί SSH.

## 1. Κλειδί SSH στο PC (μία φορά)
Στο PowerShell του PC:
```powershell
ssh-keygen -t ed25519
```
Πάτα Enter σε όλες τις ερωτήσεις (χωρίς passphrase, ώστε να συνδέεται μόνο του).

Αντέγραψε το κλειδί στο VPS (θα σου ζητήσει τον κωδικό του VPS **αυτή τη μία φορά**):
```powershell
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh ΧΡΗΣΤΗΣ@IP_VPS "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```
Δοκιμή: `ssh ΧΡΗΣΤΗΣ@IP_VPS` πρέπει να μπαίνει **χωρίς** κωδικό.

## 2. Ρύθμιση του VPS (μία φορά)
Αντέγραψε το `vps_setup.sh` στο VPS και τρέξ' το:
```powershell
scp C:\magenta-monitor\vps_setup.sh ΧΡΗΣΤΗΣ@IP_VPS:~/
ssh ΧΡΗΣΤΗΣ@IP_VPS "sudo bash ~/vps_setup.sh"
```
Εγκαθιστά nginx με HTTPS πάνω στην IP (δικό του πιστοποιητικό). Ο Flask **δεν**
εκτίθεται απευθείας: ακούει μόνο εσωτερικά στο VPS (127.0.0.1:5055).

## 3. Ρύθμιση στο PC
Αντέγραψε το `vps_config.example.bat` σε `vps_config.bat` και συμπλήρωσε:
```bat
set VPS_HOST=IP_VPS
set VPS_USER=ΧΡΗΣΤΗΣ
set VPS_SSH_PORT=22
set VPS_REMOTE_PORT=5055
set VPS_URL=https://IP_VPS
```

## 4. Χρήση
- Node-RED → **▶ Άνοιγμα (μέσω VPS)**: ξεκινά server + tunnel (ελαχιστοποιημένο
  παράθυρο «Magenta Tunnel», ξανασυνδέεται μόνο του αν πέσει).
- Από κινητό: `https://IP_VPS` → την πρώτη φορά ο browser λέει «μη ασφαλής
  σύνδεση» (επειδή το πιστοποιητικό δεν είναι από αρχή πιστοποίησης) →
  «Για προχωρημένους» → «Συνέχεια». Η σύνδεση είναι κανονικά κρυπτογραφημένη.
- Μπαίνεις με τα στοιχεία της σελίδας (αρχικά admin/admin – **άλλαξέ τα πριν
  ανοίξεις τη σελίδα στο internet**).

## Ασφάλεια
- 5 λάθος κωδικοί από την ίδια IP → κλείδωμα 10 λεπτά.
- Το κλείσιμο του server (`/api/shutdown`) δουλεύει μόνο από το ίδιο το PC,
  όχι μέσω VPS.
- Θες να μην προειδοποιεί ο browser; Πάρε δωρεάν subdomain (π.χ. duckdns.org)
  που δείχνει στην IP του VPS και πες μου να το περάσουμε σε Let's Encrypt.
- Αν το Node-RED τρέχει ως Windows service (όχι με τον χρήστη σου), δεν θα βρει
  το κλειδί SSH – τρέξε το Node-RED κανονικά με τον λογαριασμό σου.
