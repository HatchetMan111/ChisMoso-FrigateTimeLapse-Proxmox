# Chismoso-Server auf Proxmox – Einzeiler-Installation (Community-Scripts-Stil)

> Upstream-App (kein Teil dieses Repos): `https://github.com/DanielGarc/chismoso`
> (Frigate-NVR-Client: Live, Timeline, Timelapses, Alerts – Tauri-Desktop-App)
> Dieser Ordner enthält **nur Installer + Server-Wrapper**: Install-Script + FastAPI-App + systemd-Unit.
> Die App läuft nativ (Python/FastAPI + Uvicorn, ohne Docker) – vollständig lokal, keine Cloud nötig.

## Einzeiler (auf dem Proxmox-Host als root)

```bash
bash -c "$(wget -qLO - https://raw.githubusercontent.com/HatchetMan111/ChisMoso-FrigateTimeLapse-Proxmox/main/install/chismoso-server.sh)"
```

Anpassungen per Umgebungsvariable oder Flag (ID immer **nächste freie**, außer gesetzt):

```bash
CT_ID=150 CORES=2 RAM=2048 DISK=8 FRIGATE_URL=http://192.168.178.50:5000 bash -c "$(wget -qLO - https://raw.githubusercontent.com/HatchetMan111/ChisMoso-FrigateTimeLapse-Proxmox/main/install/chismoso-server.sh)"
bash chismoso-server.sh --ctid 150 --cores 2 --memory 2048 --disk 8 --bridge vmbr0 --storage local-lvm --frigate-url http://192.168.178.50:5000
bash chismoso-server.sh --debug   # = bash -x, komplette Fehlermeldungskette + Log unter /tmp/chismoso-server-install-*.log
```

> Bei Neuinstallation fragt das Script interaktiv nach der Frigate-Adresse
> (`Frigate-Adresse [http://frigate:5000]:`), wenn weder `FRIGATE_URL` noch
> `--frigate-url` gesetzt sind. Im Update-Modus (`--ctid` existiert) bleibt die
> gespeicherte Adresse erhalten – nur mit explizitem `--frigate-url`/`FRIGATE_URL`
> wird sie überschrieben.

| Eigenschaft | Wert |
|---|---|
| App-Name / Hostname | `chismoso-server` |
| Zweck | Headless Frigate-Wrapper: Kameras/Timeline/Timelapse-Exporte via Web-UI + REST |
| Tech-Stack | Python/FastAPI + Uvicorn (`:8080`), venv `/opt/chismoso-server/.venv`, Chismoso-CLI als Export-Backend |
| GitHub-Repo (Upstream) | `https://github.com/DanielGarc/chismoso` |
| Web UI | `http://<LXC-IP>:8080`, Health `/health` |
| Standard-Ressourcen | 2 vCPU / 2048 MB RAM / 8 GB Disk |
| CT-ID | immer die **nächste freie ID** (`pvesh get /cluster/nextid`), außer `--ctid` gesetzt |
| Template | `debian-12-standard` (neuestes auf Storage `local`) |
| LXC-Features | **unprivilegiert**, `nesting=1`, `onboot: 1` |

Das Skript (`set -euo pipefail`, idempotent, `trap ERR` mit Befehl+Zeile+Exit-Code):
1. prüft Host/Tools, nimmt die nächste freie CT-ID, lädt das neueste `debian-12-standard`-Template falls nötig,
2. erstellt den LXC `chismoso-server` (`onboot: 1`, unprivilegiert),
3. installiert im Container Python+venv+git, legt User `chismoso` an,
   lädt Wrapper (`app/`) + Unit aus diesem Repo, Upstream-`ui/` (sparse) + Chismoso-CLI (Latest-Release, best effort),
   schreibt `/etc/chismoso-server.env`, `systemctl enable --now chismoso-server`,
4. verifiziert `systemctl is-active chismoso-server` + HTTP auf `localhost:8080/health` + Marker auf `/`
   und gibt die finale URL + Container-IP aus.

Erwartete Schlussausgabe (Beispiel):

```text
[OK]    Service läuft (systemctl is-active chismoso-server = active).
[OK]    Web UI antwortet (HTTP 200 auf localhost:8080/health).

================ INSTALLATION ERFOLGREICH ================
  App       : chismoso-server - Frigate Timelapse/Live Wrapper
  Container : CT 100 (Hostname: chismoso-server, unprivilegiert, onboot=1)
  Ressourcen: 2 vCPU / 2048 MB RAM / 8 GB Disk
  Web UI    : http://192.168.1.100:8080
  Health    : http://192.168.1.100:8080/health
  ...
  Log       : /tmp/chismoso-server-install-2026-....log
==========================================================
```

## Frigate anbinden (Pflicht für Kameras + Timelapse)

Der Wrapper zeigt nur, was dein Frigate-Server liefert. Nach der Installation:

```bash
CT=100  # anpassen
pct exec $CT -- bash -c "printf 'FRIGATE_URL=http://<FRIGATE-IP>:5000\nPORT=8080\n' > /etc/chismoso-server.env && systemctl restart chismoso-server && sleep 3"
curl http://<LXC-IP>:8080/api/cameras   # listet alle Frigate-Kameras
```

Neue Kameras in Frigate erscheinen automatisch – nichts im Wrapper eintragen.

## Timelapse per Web-UI

`http://<LXC-IP>:8080/exports` – Formular: Kameras anhaken (kommen aus `/api/cameras`),
Start/Ende wählen, Speed (25× Standard, 60×, 144×, 720×, 1440× = 1 Tag → 1 min, oder custom 2–3600×),
Name vergeben, **Export starten**. Der Fortschritt wird live abgefragt
(Frigate-Job-Status); fertige Videos liegen auf dem Frigate-Server (Download über die Frigate-UI → Export).

REST-APIs dahinter: `POST /api/exports` (`{cameras[], start, end, speed, name}`,
ISO-Zeit wie `2026-10-10T00:00`, Antwort 202 mit `export_id` je Kamera),
`GET /api/export-jobs/{id}` (Status), `GET /api/exports` (Liste fertiger Exporte).
Technik: Nutzt Frigates eigene Export-Engine (`POST /api/export/custom/...`, Timelapse via `setpts`);
kein Chismoso-Binary nötig. Fehler (z. B. keine Aufnahmen im Zeitraum, volle Export-Queue)
kommen mit Frigate-Meldung als 400/503 zurück.

## Reboot-Test (Reboot-sicher belegen)

```bash
CT=100
pct reboot $CT
sleep 60
pct exec $CT -- systemctl is-active chismoso-server
curl -fs http://<LXC-IP>:8080/health >/dev/null && echo WEB_UI_OK
```

## Update / Deinstall

```bash
bash chismoso-server.sh --ctid 100            # Update: idempotent (pull + pip upgrade + restart)
pct stop 100 && pct destroy 100     # Deinstall
```

## Debugging

- Jeder Fehler gibt Befehl + Zeile + Exit-Code aus, Voll-Log unter `/tmp/chismoso-server-install-*.log`.
- `bash chismoso-server.sh --debug` für `bash -x`-Trace.
- Im Container: `systemctl status chismoso-server --no-pager`, `journalctl -u chismoso-server -n 100`.

## Dateien

- `install/chismoso-server.sh` – Proxmox-Einzeiler (Host, root).
- `app/main.py` – FastAPI-Wrapper (`/`, `/health`, `/api/cameras`, `/api/load`, `/api/exports`).
- `systemd/chismoso-server.service` – Unit (`0.0.0.0:8080`, `After=network-online.target`, `Restart=always`).
