# SET50 RRG

Static Relative Rotation Graph for the SET50 universe.

The chart opens with the top 20 stocks from the configured SET50 impact ranking.
Users can change the Top N value or select individual stocks from the Impact control.

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
