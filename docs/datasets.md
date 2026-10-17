# Datasets

Raw files live in `data/raw/` (not committed). How to get each one is below.

## 1. Mendeley: Buono (2022), heat tolerance. **Main real dataset.**

- **Source:** Buono, M. (2022). *Predicting heat tolerance status from exercise in thermoneutral conditions* (v1). Mendeley Data. https://doi.org/10.17632/g5sb382smp.1 · San Diego State University
- **License:** CC BY 4.0 (fine to use; cite it).
- **Note:** not a firefighter dataset, as PROJECT.md guessed. It's lab exercise.
- **Local file:** `data/raw/mendeley_buono/data.csv` (copy of Aiden's `data.csv`, already cleaned to 1-minute rows).

**What's in it (checked):**

| | |
| --- | --- |
| People | 65 |
| Sessions | 130: every person did one **heat** and one **neutral** session |
| Length | 2 hours, one row per minute (one session is only 29 min) |
| Columns | `core_temp_c`, `hr_bpm`, `rpe` (effort rating, sparse), cleaning flags, `hr_clean`, `core_temp_clean`, `usable`, `ectemp`, `ectemp_init_measured` |
| Missing | Core temp 0.5%, HR under 0.2% |
| Core temp range | 35.1 to 39.1 °C |
| Heat sessions reaching 38.0 / 38.5 / 39.0 °C | 58 / 36 / 6 of 65 |
| Neutral sessions reaching 38.0 / 38.5 / 39.0 °C | 43 / 9 / 1 of 65 |

**Kalman baseline on this data:**
- Our `kalman.py` matches the dataset's own `ectemp` column to 1e-14. Locked in by `backend/tests/test_kalman_real_data.py`.
- ECTemp starting at 37.1: **RMSE 0.47 °C, bias +0.02, 95% limits ±0.92 °C.** That's worse than Buller's published ±0.63, so there's real room for our model to beat it.
- ECTemp started at the measured temp: RMSE 0.43.

**What it's good for:**
1. Fine-tuning and testing the forecaster on real people (HR + Kalman inputs).
2. Conformal calibration on held-out people.
3. Alert scoring: 45 sessions cross 38.5 °C, so we can measure episodes caught and warning lead time.
4. **Twin proof on real data:** each person has two sessions. Adapt their twin on the neutral session (HR only), then test core-temp forecasts on their heat session.

**What it's missing:**
- No skin temp, no activity, no per-minute weather. We train with those inputs marked "missing" (the model already supports that).
- Need the study protocol for the heat and neutral conditions (air temp, humidity, exercise intensity) so we can fill WBGT and workload. Look for the linked paper.
- Unknown who made the cleaning columns (`hr_flag`, `usable`, etc.) and how. Document it before we rely on them.

**Split (by person, fixed seed):** 45 train / 10 calibration / 10 test. Test people are never touched until final results.

## 2. Abertay: heat acclimation (MbR thesis). **Probably a weak fit.**

- **Source:** Abertay University. *Data for the effects of heat acclimation on physiological responses...* DOI 10.57995/3dw0-d534 · https://rke.abertay.ac.uk/en/datasets/data-for-the-effects-of-heat-acclimation-on-physiological-respons/
- **License:** CC BY. Embargo ended July 2025.
- **What it is:** 14 people. Repeated-sprint heat training at 38 °C, 30% RH. Measures core temp, HR, skin temp, sweat rate, muscle oxygen, power, before and after 3 weeks of acclimation.
- **Files:** `Data_sheet_MbR.xlsx` (753 KB), `ReadMe_Document_for_Dataset.pdf` (151 KB). Not downloaded yet.

**Fit:**
- Short sprint tests (Wingate, 15-min time trial, 6 x 15 s sprints), not steady work. Probably too short and bursty for 30-min forecasts.
- Only 14 people.
- Might help with: **skin temp vs core temp**, and **acclimatization** (same people before and after 3 weeks). That's the exact effect our acclimatization score models.
- Decide after reading the ReadMe: if core temp is per-minute, use it as a small extra test set. If it's summary-only, skip it.

## 3. Synthetic (our simulator)

See PLAN.md Phase 1b. Pretraining data plus the adversarial curriculum.
