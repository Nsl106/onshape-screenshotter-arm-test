# Onshape Screenshotter

Watch your robot come together. Onshape Screenshotter takes a picture of your team's
Onshape part studio or assembly a few times a day, and saves it only when the CAD
actually changed — building up a timelapse of your whole season. It runs entirely
inside **your own** private copy of this repository on GitHub, for free.

> 🎬 _Your timelapse will appear under [`timelapse/`](timelapse/) after the first
> Timelapse run. Drop a link to your favorite one here._

## Why you can trust it

This repository is **public code with no server behind it**. There is nothing to
sign up for and nobody to trust:

- You make your **own private copy** of this template. Your copy is yours alone.
- Your Onshape API keys are stored as **encrypted GitHub Actions secrets in your
  repo** — never printed in logs, never committed, never sent anywhere but Onshape.
- The rendered images are committed to **your private repo**, nowhere else.

Because the code is public, you (or a mentor) can read every line and confirm it
only ever talks to `cad.onshape.com`. The author never sees your keys, your repo, or
your images.

## Tracked CAD

This section updates itself each time frames are captured — one link per tracked
document, pointing at its folder of frames.

<!-- targets:start -->
_No frames captured yet. Run the Capture workflow to populate this._
<!-- targets:end -->

## Setup (about 10 minutes)

### 1. Make your own copy

Click **“Use this template” → “Create a new repository.”** Choose your team's
account, give it a name, and **set it to Private**. (Use the template button, not
Fork — forks can't be made private and stay linked to this repo.)

### 2. Get your Onshape API keys

1. Go to <https://cad.onshape.com/user/developer/apiKeys/createApiKey>.
2. Tick **“Application can read your documents”** — that's the only permission this
   tool needs; it never modifies your CAD.
3. Click **Create API key**, then copy the **Access key** and **Secret key**
   somewhere safe for the next step. The secret key is shown only once.

> Some Onshape Education plans restrict API keys. If the portal won't let you make a
> key, check with your Onshape account admin that your plan allows API access.

### 3. Add the keys to your repo as secrets

In **your** new repo: **Settings → Secrets and variables → Actions → New repository
secret.** Add two secrets, named exactly:

| Secret name           | Value                       |
| --------------------- | --------------------------- |
| `ONSHAPE_ACCESS_KEY`  | your Onshape **access** key |
| `ONSHAPE_SECRET_KEY`  | your Onshape **secret** key |

### 4. Point it at your CAD

Open your part studio or assembly in Onshape and **copy the URL straight from your
browser's address bar.** It looks like:

```
https://cad.onshape.com/documents/abc123…/w/def456…/e/ghi789…
```

Edit [`config.toml`](config.toml) in your repo (click the file, then the pencil
icon) and paste your URL into the `url` line:

```toml
[[targets]]
url = "https://cad.onshape.com/documents/abc123…/w/def456…/e/ghi789…"
```

That's the only required edit — the document, workspace, and element are all read
from that link, and the name and type are looked up automatically. To track more
than one document, add another `[[targets]]` block with its own `url`. You can also
set **`capture_hours`** — the local-time hours you want a screenshot at — plus
`timezone`, image size, view angle, and frame rate in the `[settings]` section;
each option is explained inline in the file.

### 5. Turn on Actions

Open the **Actions** tab in your repo and click the green button to enable
workflows. The **Capture** job then wakes hourly and takes a screenshot at each of
your `capture_hours` (default `[8, 12, 16, 20]`). That list is your API-budget dial
— see [API budget](#api-budget) below.

> **Start early.** This tool records history *going forward* from the moment you
> turn it on — it does not (and cannot, affordably) reconstruct the past, because
> Onshape's history is one entry per edit and there's no cheap way to replay it.
> Switch Capture on at kickoff and your whole season builds itself. Want a head
> start before adoption? Make a few **Versions** in Onshape at your milestones —
> those are your permanent snapshots regardless of this tool.

### 6. Make the video

**Actions → Timelapse → Run workflow** stitches your frames into an `.mp4` (tick
`gif` for an animated GIF too). It also runs automatically whenever new frames are
captured. The result lands in `timelapse/`, and the **Tracked CAD** section above
links to each document's frames.

## API budget

This is the one number to understand before you scale up. **Onshape limits API
calls per _year_, per account:**

| Plan | Calls / year | ≈ Calls / day |
| ---- | ------------ | ------------- |
| Education (most FRC teams) | 2,500 | ~6.8 |
| Professional | 5,000 / user | ~13.7 |
| Enterprise | 10,000 / user | ~27 |

You spend **one API call per capture hour, per document** — each fires a single
render that decides locally whether the model changed. The hourly heartbeat costs
*nothing* at hours not in your list (it exits before any Onshape call). So:

```
calls/year  ≈  len(capture_hours) × 365 × (number of documents)
```

Just count the hours in your list. At the default `[8, 12, 16, 20]` that's **~1,460
calls/year for one document** — comfortably inside an Education plan, with room for
a second document. Tune `capture_hours` in [`config.toml`](config.toml):

| `capture_hours` | Frames/day | ≈ Calls/year (1 doc) | Good for |
| --------------- | ---------- | -------------------- | -------- |
| `[12, 20]` | 2 | ~730 | Education, 2–3 docs |
| `[8, 12, 16, 20]` (default) | 4 | ~1,460 | Education, 1–2 docs |
| `[8, 11, 14, 17, 20, 23]` | 6 | ~2,190 | Education, 1 doc |
| `[]` (empty) | 0 | 0 | pause capture (offseason) |

Setting `capture_hours = []` pauses capture entirely — no screenshots, no API calls
— handy in the offseason without disabling Actions. A few frames a day is plenty
for a season timelapse: 4/day over a ~4-month build season is 400–500 frames ≈ a
40–50-second video at 10 fps. To raise your limit, contact Onshape
(`api-support@onshape.com`).

## Troubleshooting

- **Capture stopped running after a couple of months.** GitHub disables scheduled
  workflows after 60 days with no repo activity. During build season the regular
  commits keep it alive; in the offseason it can pause. Leave `keepalive = true` in
  `config.toml` (the default) and it commits a tiny no-op monthly to stay enabled —
  or just open **Actions** in January and re-enable it.
- **Occasional “429” messages.** That's Onshape's per-minute rate limit. The tool
  pauses and retries automatically — nothing to do.
- **“annual API-call quota … is used up (HTTP 402).”** Separate from 429, Onshape
  caps how many API calls an account may make per *year* (see [API budget](#api-budget)).
  If you hit it, you have too many `capture_hours` for your plan, or you're tracking
  too many documents — shorten `capture_hours` in `config.toml` or drop a target. It
  resets annually, and more calls can be requested from Onshape
  (api-support@onshape.com).
- **“Onshape rejected the API credentials.”** Double-check the two secret names are
  exactly `ONSHAPE_ACCESS_KEY` / `ONSHAPE_SECRET_KEY`, that you pasted the keys
  without extra spaces, and that the key's owner can open the document in Onshape.
- **“…is not a recognizable Onshape link.”** Copy the URL while viewing the document
  in your **workspace** (the link should contain `/w/`), not a fixed version
  (`/v/`).

## Development

The tool is plain Python (3.11+) with one runtime dependency (`requests`).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .          # install the package
pip install pytest ruff   # dev tools

pytest                    # run the test suite (no network needed)
ruff check . && ruff format --check .

# Try a capture locally without writing anything (needs the two env vars set):
ONSHAPE_ACCESS_KEY=… ONSHAPE_SECRET_KEY=… python -m screenshotter.capture --dry-run

# The timelapse step needs ffmpeg on your PATH (preinstalled on GitHub runners):
#   macOS: brew install ffmpeg   ·   Ubuntu: sudo apt install ffmpeg
```

`--at <ISO-8601>` on the capture job captures a specific past hour, handy for
filling a gap by hand.
