# Recepten Bot — Setup Guide

## Overzicht

```
VPS (recepten.example.com)          NUC (homelab)
├── nginx + SSL                    ├── Telegram bot
├── FastAPI webhook                ├── Claude API client
└── Webapp (index.html)           ├── Scheduler
                                   ├── SQLite database
                                   └── → pusht plan naar VPS
```

---

## Stap 1 — Telegram bot aanmaken

1. Open Telegram, zoek **@BotFather**
2. Stuur `/newbot`
3. Geef het een naam: `Weekmenu `
4. Geef het een username: `weekmenu_bot` (of iets unieks)
5. Kopieer het **token** (ziet eruit als `123456789:ABCdef...`)

**Groep aanmaken:**
1. Maak een nieuwe Telegram groep: "Weekmenu"
2. Voeg jezelf, je vrouw, en de bot toe
3. Stuur een bericht in de groep
4. Haal de group ID op:
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
   Zoek naar `"chat":{"id":` — dat is een negatief getal zoals `-1001234567890`

---

## Stap 2 — Anthropic API key

1. Ga naar https://console.anthropic.com
2. Maak een account aan (of log in)
3. Ga naar **API Keys** → **Create Key**
4. Kopieer de key (`sk-ant-...`)
5. Voeg krediet toe onder **Billing** (begin met €10, geeft maanden mee)

---

## Stap 3 — Shared secret genereren

Op je Fedora workstation of NUC:
```bash
openssl rand -hex 32
```
Kopieer de output — dit gebruik je op zowel NUC als VPS.

---

## Stap 4 — VPS instellen

**SSH naar VPS:**
```bash
ssh root@<vps-ip>
```

**Docker installeren:**
```bash
apt update && apt install -y docker.io docker-compose-plugin
systemctl enable --now docker
```

**Firewall:**
```bash
ufw allow 22
ufw allow 80
ufw allow 443
ufw enable
```

**DNS:** Zorg dat `recepten.example.com` een A-record heeft naar `<vps-ip>`

**Project uploaden:**
```bash
# Vanaf je workstation
scp -r recepten/vps root@<vps-ip>:/opt/recepten
```

**SSL certificaat ophalen (eerst nginx tijdelijk op poort 80):**
```bash
cd /opt/recepten
# Start nginx alleen op HTTP eerst voor de challenge
docker run --rm -p 80:80 -v /opt/recepten/certbot-www:/var/www/certbot certbot/certbot certonly \
  --webroot --webroot-path=/var/www/certbot \
  -d recepten.example.com \
  --email jouw@email.nl --agree-tos --no-eff-email
```

**Webapp naar nginx html map:**
```bash
mkdir -p /opt/recepten/nginx/html
cp /opt/recepten/webapp/index.html /opt/recepten/nginx/html/
```

**Nginx config naar juiste map:**
```bash
mkdir -p /opt/recepten/nginx/conf.d
# conf.d/recepten.conf staat al in het project
```

**.env aanmaken:**
```bash
cp /opt/recepten/.env.example /opt/recepten/.env
nano /opt/recepten/.env
# Vul VPS_SHARED_SECRET en NUC_ALLOWED_IP in
```

**Starten:**
```bash
cd /opt/recepten
docker compose up -d
```

---

## Stap 5 — NUC instellen

**Project naar NUC kopiëren:**
```bash
scp -r recepten/nuc user@<nuc-ip>:/opt/recepten-bot
```

**.env aanmaken:**
```bash
cp /opt/recepten-bot/.env.example /opt/recepten-bot/.env
nano /opt/recepten-bot/.env
# Vul alle waarden in
```

**Data directory aanmaken:**
```bash
mkdir -p /opt/recepten-bot/data
```

**Starten:**
```bash
cd /opt/recepten-bot
docker compose up -d
```

**Logs controleren:**
```bash
docker logs -f recepten-bot
```

---

## Stap 6 — Claude Code installeren

Claude Code is een CLI tool voor agentic coding — ideaal om dit project
verder te ontwikkelen en debuggen.

**Installeren op Fedora:**
```bash
# Node.js 18+ vereist
node --version  # check eerst

npm install -g @anthropic-ai/claude-code
```

**Starten in het project:**
```bash
cd ~/recepten
claude
```

Gebruik Claude Code voor:
- Bugs fixen ("de shopping list toont verkeerde items")
- Features toevoegen ("voeg een /favorieten command toe")
- Deployen naar NUC/VPS via SSH

---

## Commands in Telegram

| Command | Functie |
|---------|---------|
| `/genereer` | Start nieuwe receptenronde (ook handmatig) |
| `1 3 5 7 9` | Kies recepten na proposal |
| `/menu` | Toon weekoverzicht |
| `/recept Maandag` | Toon volledig recept voor die dag |
| `/swap Maandag Vrijdag` | Wissel twee dagen om |
| `/lijst` | Stuur boodschappenlijst opnieuw |

---

## Automatische tijden

| Moment | Actie |
|--------|-------|
| Dinsdag 09:00 | Bot stuurt 10 receptenvoorstellen |
| Dagelijks 18:00 | Herinnering vanavond + ontdooialert morgen |
| Donderdag 14:00 | Boodschappenlijst reminder |

---

## Webapp

Bereikbaar op: **https://recepten.example.com**

- **Deze week** tab: weekoverzicht, vandaag prominent bovenaan
- **Boodschappen** tab: afvinkbare boodschappenlijst
- Tik op een dag → volledig recept
- Ontdooialert zichtbaar als morgen vlees/vis
