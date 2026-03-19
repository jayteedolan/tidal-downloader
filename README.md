# Tidal Music Downloader

A self-hosted web app for downloading Hi-Fi FLAC files from Tidal, tagging them with MusicBrainz metadata, and organizing them into your Plex music library — with a guided, confirmation-at-every-step UI.

## Features

- Paste a **Tidal, Spotify, or Apple Music** album URL to auto-identify the album
- Or **text-search** by artist + album name
- Browse and pick from **Tidal album results** (cover art, quality, track count)
- Browse and pick from **MusicBrainz release options** (date, country, label, format)
- **Fuzzy-search** your Plex artist folders to pick the right destination, or create a new one
- Download all tracks as **FLAC** with full MusicBrainz metadata tags + embedded cover art
- **Prompts before overwriting** if the album folder already has files
- **Triggers a Plex library scan** automatically after organizing files

## Requirements

- Python 3.11+
- A running Plex Media Server
- A Plex API token (see below)

## Getting Your Plex Token

1. Open Plex Web in your browser and sign in
2. Navigate to any media item, click the **⋮** menu → **Get Info** → **View XML**
3. In the URL that opens, copy the value after `X-Plex-Token=`

## Setup

```bash
# 1. Clone / place this folder on your machine
cd tidal-downloader

# 2. Create and activate a virtual environment (required on Raspberry Pi OS)
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env — fill in PLEX_TOKEN and MUSIC_LIBRARY_PATH at minimum

# 5. Run the server
uvicorn main:app --host 0.0.0.0 --port 8766
```

Open `http://localhost:8766` or `http://<your-pi-ip>:8766` from any device on your network.

To reactivate the environment in a new shell:
```bash
source .venv/bin/activate
```

## Running as a System Service (Raspberry Pi / headless)

A systemd service file is included. Install it so the app starts automatically on boot:

```bash
# Copy the service file into systemd
sudo cp tidal-downloader.service /etc/systemd/system/

# Reload systemd, enable on boot, and start now
sudo systemctl daemon-reload
sudo systemctl enable tidal-downloader
sudo systemctl start tidal-downloader

# Check status
sudo systemctl status tidal-downloader
```

To view logs:
```bash
journalctl -u tidal-downloader -f
```

## Remote Access

To access the app from outside your home network, forward port **8766** on your router to your Pi's local IP address.

Then open `http://<your-public-ip>:8766` from any device.

## Configuration (`.env`)

| Variable | Description | Default |
|---|---|---|
| `PLEX_TOKEN` | Your Plex API token | *(required)* |
| `MUSIC_LIBRARY_PATH` | Full path to your Plex music library root | *(required)* |
| `PLEX_URL` | Plex server URL | `http://localhost:32400` |
| `PLEX_MUSIC_SECTION` | Name of your Plex Music library section | `Music` |
| `PORT` | Port to run the app on | `8766` |

## File Organization

Downloaded albums are placed at:
```
<MUSIC_LIBRARY_PATH>/<Artist Folder>/<Album Name (Year)>/
  01 - Track Title.flac
  02 - Track Title.flac
  ...
```

Multi-disc albums use `disc-track` numbering (e.g. `1-01 - Track Title.flac`).

## Download Workflow

1. **Find the album** — paste a URL or search by name; pick from results
2. **Pick a MusicBrainz release** — choose the correct pressing/edition for accurate metadata
3. **Pick a destination** — fuzzy-search your existing artist folders or name a new one
4. **Download** — per-track progress shown live; Plex scan triggered on completion

## Notes

- Downloads use the public Tidal proxy at `triton.squid.wtf` and several fallback hosts. No Tidal account required.
- MusicBrainz metadata is fetched from the public MusicBrainz API. No account required.
- This project is for personal use with music you have the right to access.
