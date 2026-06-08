# Recepten Bot — Project Context

## Infrastructure

| Component | Host | IP | Deploy path |
|-----------|------|----|-------------|
| LXC 102 (bot) | proxmox.home.example.com | `<lxc-ip>` | `/opt/recepten-bot` |
| VPS (webhook + webapp) | recepten.example.com | `<vps-ip>` | `/opt/recepten` |

## Architecture

- **LXC 102**: Debian 12, Docker, Telegram bot, Claude API client, SQLite DB, scheduler, pushes plan to VPS
- **VPS**: nginx + SSL, FastAPI webhook receiver, static webapp

Both sides run via Docker Compose.

## LXC details

- Proxmox CT ID: 102, hostname: `recepten-bot`
- Disk: usbssd (`/mnt/pve/usbssd/images/102/`), 8GB
- Resources: 2 vCPU, 1GB RAM, 512MB swap
- Network: vmbr0 tag=10 (inside VLAN), IP <lxc-ip>/24
- SSH: `ssh root@<lxc-ip>` (from inside network) or `pct exec 102 -- bash` (from PVE host)

## VPS details

- Host: `recepten.example.com` / `<vps-ip>`
- SSH: `ssh root@<vps-ip>` (key auth — publickey only, no password)
- Deploy path: `/opt/recepten`
- Containers: `recepten-nginx` (80/443), `recepten-api` (intern), `recepten-certbot`
- Data: `/opt/recepten/data/current_plan.json` — actief weekplan (overschreven bij elke push)
- SSL: Let's Encrypt via certbot, auto-renew elke 12h
- IP-whitelist: `NUC_ALLOWED_IP=<home-public-ip>` in `vps/.env` — alleen dit IP mag naar `/api/push`
- API endpoints:
  - `POST /api/push` — ontvangt plan van LXC (vereist `X-Secret` header + IP-check)
  - `GET /api/plan` — levert plan aan webapp
  - `GET /api/health` — health check

## Status

- Telegram bot token: set in `nuc/.env`
- Anthropic API key: set in `nuc/.env`
- Shared secret: generated and set in both `nuc/.env` and `vps/.env`
- NUC_ALLOWED_IP: set to `<home-public-ip>` in `vps/.env`
- **Deployed and running**
  - LXC 102: `recepten-bot` container up, polling Telegram, scheduler active
  - VPS: `recepten-nginx`, `recepten-api`, `recepten-certbot-1` containers up, HTTPS live
- SQLite DB: `/opt/recepten-bot/data/recepten.db` (bind-mounted into container)
- Docker is **not** installed on the PVE host — runs inside LXC 102 only

## Key files

- `nuc/main.py` — entrypoint
- `nuc/bot.py` — Telegram command handlers
- `nuc/claude_api.py` — recipe generation via Claude
- `nuc/db.py` — SQLite schema and queries
- `nuc/vps_push.py` — pushes weekly plan to VPS webhook
- `vps/webhook/` — FastAPI app receiving pushes from NUC
- `vps/webapp/index.html` — frontend at https://recepten.example.com

## Deploy commands

```bash
# Bot (LXC 102) — copy file and rebuild image
scp nuc/bot.py root@proxmox.home.example.com:/tmp/bot.py
ssh root@proxmox.home.example.com "pct push 102 /tmp/bot.py /opt/recepten-bot/bot.py && pct exec 102 -- bash -c 'cd /opt/recepten-bot && docker compose up -d --build'"

# Full redeploy to LXC
scp -r nuc/* root@proxmox.home.example.com:/tmp/recepten-nuc/
ssh root@proxmox.home.example.com "pct exec 102 -- bash -c 'cp -r /tmp/recepten-nuc/* /opt/recepten-bot/ && cd /opt/recepten-bot && docker compose up -d --build'"

# VPS
scp -r vps/* root@<vps-ip>:/opt/recepten/
ssh root@<vps-ip> "cd /opt/recepten && docker compose up -d"
```
