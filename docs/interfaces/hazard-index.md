# Hazard index (session C2) interface

The 0-10 site hazard index: how dangerous is each job site **tomorrow**, using only what's known by **6 PM local the evening before**. Built in `ml/hazard_index/`. Results: `docs/sessions/hazard-index.md`.

The index ranks sites and fills the 6 PM briefing. It is **not an alert** and never makes a safety call. Live alerts come from `backend/hazards` (C1) and `backend/alerts` (A).

## The function

```python
from ml.hazard_index.predict import predict_index, predict_all_sites

r = predict_index("ph-gainesville", "2024-07-10")      # date = the day being scored, a date or "YYYY-MM-DD"
ranked = predict_all_sites("2026-10-18")               # every site, highest index first (for the map)
```

- Pass tomorrow's date at 6 PM for the briefing. Old dates work too (handy for replay and demos).
- `offline=True` uses only cached downloads (no network). Raises `FileNotFoundError` if something isn't cached.
- Dates older than 7 days use the Open-Meteo archive, the same as training. Recent dates use the Open-Meteo forecast API plus live IEM warnings, storm reports and SPC outlooks, re-downloaded at most once an hour.
- Speed: first call per site and day takes a few seconds (a handful of polite web calls). After that it's cached.
- `site_id` comes from `ml/hazard_index/sites.py:load_sites()`. That reads C1's `backend/sites/plants.geojson` once it exists (fields `site_id`, `id`, `plant_id` or `eia_plant_id`). Until then it uses 12 placeholder sites with ids starting `ph-`.

## JSON shape

```json
{
  "site_id": "ph-gainesville",
  "site_name": "Gainesville (Deerhaven area), placeholder",
  "date": "2024-07-10",
  "index": 4.1,                       // 0-10, overall
  "band": "high",                     // low <2, moderate <4, high <6, extreme 6+ (display only)
  "climatology_index": 2.3,           // what "a usual day this month at this site" scores, for comparison
  "p_advisory": 0.71,                 // P(worst hazard reaches advisory level or worse)
  "p_warning": 0.42,
  "p_damage": 0.08,
  "hazards": {
    "heat":     {"index": 2.6, "p_advisory": 0.8, "p_warning": 0.05, "p_damage": 0.0, "climatology_index": 0.9},
    "storm":    {...}, "flood": {...}, "fire": {...}, "tropical": {...}
  },
  "top_reasons": [
    {"hazard": "heat", "feature": "hi_max_today", "value": 109.4, "points": 1.2,
     "text": "Heat index reached 109°F by 6 PM today"},
    {"hazard": "storm", "feature": "spc_day2_cat", "value": 3.0, "points": 0.6,
     "text": "SPC storm outlook for tomorrow: slight risk"}
  ],
  "cutoff": "2024-07-09T18:00:00-04:00",   // latest data the score is allowed to use
  "data_mode": "archive",                  // or "live"
  "model_version": "2026-10-17",
  "notes": ["Trained on placeholder sites; retrain once backend/sites/plants.geojson lands."]
}
```

How to read the number: it's the **expected level** on the label ladder (watch 1, advisory 3, warning 5, damage 8, death 10):
`index = 3·P(advisory+) + 2·P(warning+) + 3·P(damage+) + 2·P(death)`. A day that will surely get a heat advisory and nothing worse scores about 3. `points` in a reason is roughly how many index points that input added.

Show `text` as-is in the briefing. Show `notes` in small print.

## Hazards

`heat`, `storm` (thunderstorms, lightning damage, wind, tornado, hail), `flood`, `fire`, `tropical`. Lightning itself isn't a separate hazard here because no free historical strike record exists; live lightning is C1's 10-mile rule.

## Retrain

1. `python -m ml.hazard_index.labels` (downloads NWS warnings and storm reports per site, cached; about 3 s per site-year)
2. `python -m ml.hazard_index.features` (downloads Open-Meteo archive weather, about 1-2 min per site, spaced out politely)
3. `python -m ml.hazard_index.evaluate` (scores on 2023-2024, prints the table)
4. `python -m ml.hazard_index.train` (fits on all years, writes `ml/hazard_index/model/hazard_index.json`)

Add `--sites placeholder` or `--sites geojson` to any step to pick the site list. Downloads are cached in `data/raw/hazard_index/` in the main checkout (gitignored). For 50+ sites, Open-Meteo's free daily limit is the bottleneck: run step 2 in two sittings if it complains.

Optional extra label sources (better damage/injury labels). Put the files here and rerun step 1:
- NOAA Storm Events yearly details CSVs → `data/raw/hazard_index/stormevents/`
- OSHA Severe Injury Reports zip → `data/raw/hazard_index/osha/`
