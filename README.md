# SET50 RRG

Static Relative Rotation Graph for the SET50 universe.

## How it works

- `index.html` is the GitHub Pages frontend.
- `generate_data.py` builds `data-5m.json`, `data-15m.json`, `data-daily.json`, `data-weekly.json`, and `data-monthly.json`.
- `.github/workflows/update-static-data.yml` refreshes those snapshots on schedule or on manual dispatch.

## Local refresh

```powershell
pip install -r requirements.txt
python generate_data.py
```

## GitHub Pages

Publish the repository root with GitHub Pages so `index.html` and the JSON snapshot files are served directly.
