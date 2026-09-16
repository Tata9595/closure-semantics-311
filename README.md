# Closure Semantics in Urban Service Requests

Replication package for:

> **Which Kind of Non-Resolution Predicts Recurrence? Decomposing Closure Semantics in Large-Scale Urban Service Requests**
> CSAI 2026, Beijing.

This repository contains the complete pipeline — data acquisition, annotation, analysis, robustness checks, and figure/table generation — for a study of 10,336,532 New York City 311 service requests filed between 2023 and 2025.

---

## What the paper finds

The official closure rate over this period is **98.24%**, but only **37.64%** of closures record substantive action. We test whether closure semantics predict subsequent recurrence, and find that the answer depends on how "non-resolution" is defined:

| Scope | Categories | Mean ρ (6 settings) | Range |
|---|---|---|---|
| Wide | B, C, D, E, F, G | 0.134 | 0.076–0.296 |
| **Middle** | **B, C, D, E, F** | **0.333** | **0.288–0.369** |
| Strict | B, C, E, F | 0.314 | 0.217–0.369 |

Because the seven category shares sum to one, the wide scope is algebraically `1 − p_A`, so its correlation is entirely determined by the substantive-action share — which does not predict recurrence at all.

---

## Data

The raw data is **not included** in this repository. It is public and can be downloaded with the script provided.

| | |
|---|---|
| Source | [NYC Open Data](https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2010-to-Present/erm2-nwe9) |
| Dataset ID | `erm2-nwe9` |
| Period used | 2023-01-01 to 2025-12-31 |
| Volume | 10,336,532 tickets (~4 GB as Parquet) |
| Retrieval time | ~3 hours (API rate limited) |

An app token is recommended but not required. Register at the NYC Open Data portal and set it as an environment variable:

```bash
export NYC_APP_TOKEN="your_token"      # Linux / macOS
$env:NYC_APP_TOKEN="your_token"        # Windows PowerShell
```

### Why daily slicing

The platform's standard pagination requires a sort clause so offsets stay stable, but sorted queries on this dataset take over 90 seconds and frequently time out, while unsorted queries return in about 3 seconds. `step1_download.py` therefore narrows each request to a single day — 7,000–9,000 tickets, below the return limit — which removes the need for sorting and pagination entirely. This reduces full retrieval from infeasible to roughly three hours.

---

## Requirements

```bash
pip install duckdb requests pyarrow polars matplotlib
```

| | Version used |
|---|---|
| Python | 3.14.4 |
| DuckDB | 1.5.5 |
| Matplotlib | 3.11.2 |

Hardware used: Intel Core i7-13620H, 16 GB RAM. DuckDB is configured for a 6 GB memory limit and 4 threads. All aggregation and self-joins run inside DuckDB rather than pandas, so ten million rows are never fully materialised in memory.

---

## Pipeline

Run in order. Times are approximate on the hardware above.

| Step | Script | What it does | Time |
|---|---|---|---|
| 1 | `step1_download.py` | Download via the Socrata API in daily slices; write monthly Parquet | ~3 h |
| 2 | `step2_analyze.py` | Build the DuckDB database; field completeness; raw recurrence | ~8 min |
| 3 | `step3_diagnose.py` | Group-size stratification; excess recurrence; resolution-text frequency | ~10 min |
| 4 | `step4_classify.py --extract` | Produce the annotation sheet from resolution templates | ~2 min |
| — | *(manual annotation)* | Fill the `code` column — see below | 2–3 h |
| 4 | `step4_classify.py --apply` | Apply the annotation; compute per-agency and per-type shares | ~5 min |
| 5 | `step5_robustness.py` | Five observation windows × two null models | ~35 min |
| 6 | `step6_strict.py` | Wide vs strict scope; per-category decomposition | ~10 min |
| 7 | `step7_finalize.py --mid` | Add the middle scope | ~5 min |
| 7 | `step7_finalize.py --sheet` / `--kappa` | Blind sheet for the second annotator; inter-rater agreement | ~2 min |
| 8 | `step8_finalize2.py --placeholder` | Placeholder-address diagnosis and exclusion | ~10 min |
| 8 | `step8_finalize2.py --scatter` | Scatter data for the main figure | ~3 min |
| — | `make_figures.py` | All figures, 300 dpi | ~1 min |
| — | `make_tables.py` | All tables as ACM-ready LaTeX | ~1 min |

### Helper scripts

| Script | Purpose |
|---|---|
| `peek.py` | Show the full text of a resolution template and its variants — used during annotation when a truncated prefix is ambiguous |
| `check_addr.py` | Diurnal and monthly distribution of a given address, plus comparison against the citywide pattern for the same complaint type — used to diagnose the placeholder address |


### A note on the code comments

Module documentation, usage instructions and all output shown to the user
are in English. Some inline comments within the scripts remain in Chinese,
the language in which the analysis was developed. They explain
implementation details rather than method, and every decision that bears on
the results is documented in English here and in the paper.

---

## Annotation

`data/mapping_post_adjudication.csv` is the classification actually used in the paper. Each row maps the first 250 characters of a resolution description to one of eight codes.

| Code | Category | Criterion |
|---|---|---|
| **A** | Substantive action | Condition fixed; summons issued; violation served; requested service or item provided |
| **B** | No evidence found | No evidence observed on arrival; reported condition not found; no violation on inspection |
| **C** | Responsible party gone | Those responsible had left before arrival |
| **D** | No action necessary | Agency determined it need not act |
| **E** | No access | Unable to enter the dwelling or premises |
| **F** | Duplicate | Duplicates an existing ticket for the same building or condition |
| **G** | Referred / informational | Referred elsewhere, or information supplied without action on the problem |
| **X** | Unclassifiable | Text insufficient to judge |

The single criterion is: **from the reporting resident's standpoint, did the state of the problem change?** If yes, A; if no, assign B–G by reason; if undeterminable, X.

### Prefix length

Templates are keyed on their first **250** characters. We initially used 120 and found that the housing agency's core template had not diverged at that length — "verified that the following conditions *were corrected*" (A) and "*still exist*" (B) collapsed into a single template, leaving 2.17 million tickets unclassifiable. The parameter is set at the top of `step4_classify.py`.

### Files provided, and one that is not

| File | Templates | Description |
|---|---|---|
| `data/mapping_final.csv` | 164 | **The classification used in the paper**, after adjudication |
| `data/mapping_keyword_rules.csv` | 164 | The rule-based suggestions that seeded annotation, before any human review |

⚠️ **The first annotator's pre-adjudication coding is not included, because the annotation file was edited in place during adjudication and that intermediate state was not preserved.** The pre/post comparison reported below and in the paper rests on the saved analysis outputs from each stage, not on the annotation file itself. We state this plainly rather than reconstructing the file, which would not be the original.

Inter-rater agreement was computed before adjudication: observed agreement 81.10%, expected 0.192, **Cohen's κ = 0.766**, ticket-weighted agreement 94.15%.

**The disagreements were not random.** Fourteen of the thirty-one concentrated between "referred / informational" and "substantive action", because the initial keyword rules triggered the referral category too broadly. Templates recording that a requested lead test kit was mailed (54,528 tickets), that violations were found with follow-up scheduled (33,174), or that a vehicle owner had claimed the vehicle (31,248) all record substantive action but contained words such as "mailed" or "contact".

The scale of the correction is visible in the two files provided: the keyword rules left 77 of 164 templates unclassifiable and assigned 20 to substantive action, whereas the final annotation leaves 6 unclassifiable and assigns 43 to substantive action.

Adjudicating the disagreements changed several reported quantities:

| Quantity | Pre-adjudication | Post-adjudication |
|---|---|---|
| Substantive action share | 34.24% | 37.64% |
| Referred / informational share | 9.04% | 5.89% |
| Wide-scope mean ρ | 0.005 | 0.134 |
| Strict-scope mean ρ | 0.320 | 0.314 |
| ρ for F (duplicate) | 0.151 | 0.315 |
| ρ for D (no action necessary) | 0.256 | 0.162 |

Note the asymmetry: the strict scope barely moved while the wide scope moved by a factor of 27. The wide scope is algebraically `1 − p_A`, and the adjudication corrected precisely the A–G boundary. **An indicator defined as the complement of a single category is highly sensitive to that category's annotation accuracy.**

---

## Excluded records

| Excluded | Reason | Share |
|---|---|---|
| All three location fields missing | Same-location judgement impossible | 0.43% |
| Not closed | No closure time | 1.76% |
| One placeholder address | Geocoding default — see below | 1.68% |

The largest location–type group holds 173,945 tickets (residential noise at one Bronx address, 158.7 per day), **10.73×** the second largest, while groups two through five differ from one another by factors of 1.06–1.21. Four independent lines of evidence indicate a geocoding default rather than a real hotspot:

1. **Order-of-magnitude gap** against the next largest group.
2. **On–off monthly pattern** — exactly one ticket in each of six separate months, but 39,989 in January 2025.
3. **Anomalous in one category only** — 173,945 noise tickets against 1,560 parking and 61 heating at the same address.
4. **Diurnal distribution unlike any real reporting pattern** — peak-to-trough ratio 1.85 against a citywide 5.5 for the same complaint type, with 05:00–08:00 shares about 1.8× citywide.

The group's resolution descriptions are entirely ordinary, matching the citywide disposition structure. **The complaints and dispositions are real events; only the location field was defaulted.** Since recurrence detection depends wholly on location, these tickets are excluded.

Excluding them changes the three scopes' correlations by at most 0.002 (wide 0.1224→0.1207, middle 0.3432→0.3424, strict 0.3310→0.3325). Reproduce with `step8_finalize2.py --placeholder`; the rule is a configurable list at the top of the script.

---

## Reproducing specific results

| Paper element | Command | Output |
|---|---|---|
| Recurrence by group size | `step3_diagnose.py` | `out/H2_by_group_size.csv` |
| Two null models | `step5_robustness.py` | `out/J3_null_model_overall.csv` |
| Three scopes, six settings | `step7_finalize.py --mid` | `out/L2_three_scopes_summary.csv` |
| Per-category correlations | `step6_strict.py` | `out/K4_rho_per_category.csv` |
| Inter-rater agreement | `step7_finalize.py --kappa` | `out/L5_kappa.csv` |
| Placeholder exclusion | `step8_finalize2.py --placeholder` | `out/M5_before_after_overall.csv` |
| All figures | `make_figures.py` | `out/fig/*.png` |
| All tables | `make_tables.py` | `out/tables.tex` |

All computation is deterministic — group aggregation, self-joins and rank correlation, with no random sampling or model training. The only stochastic element is the circular time-shift null, whose offsets are fixed at {91, 182, 273, 365} days, so it is fully reproducible.

---

## Known limitations

These are stated in the paper and repeated here so that anyone reusing the code is aware of them.

- **Recurrence is not failure of disposition.** A problem may recur independently after closure.
- **Non-recurrence is not resolution**, and the bias has a direction: residents dissatisfied with a previous disposition may stop reporting, and such cases are recorded as successful. **The estimated distortion is a lower bound.**
- **Annotation involves subjective judgement.** A systematic error was detected and corrected, but adjudication was performed by the same researcher, so further undetected bias cannot be excluded. κ = 0.766 is mid-band, not high agreement.
- **Both null models rest on assumptions** — uniform arrivals for Poisson, a stationary arrival rhythm for the circular shift. The two differ by a factor of 3.9 in absolute excess recurrence.
- **Correlations are computed at the complaint-type level while the proposed mechanism operates at the ticket level.** Strictly, types with more C-coded closures have higher average excess recurrence; this does not establish that an individual C-coded ticket is more likely to recur. The mechanistic reading is a conjecture, not a finding.
- **The tax lot identifier operates at parcel level** and cannot distinguish units within a lot.
- **Five agencies fall below the 80% annotation coverage threshold** and are excluded from agency-level comparison. They are not a random selection and skew toward lower-volume agencies with more dispersed templates.
- **Single city.** The pipeline should transfer to other Socrata-based open data portals, but this has not been tested.

---

## Extending to other cities

`step1_download.py` targets the Socrata API and should work against other portals by changing the dataset identifier and base URL. Two things will need rework:

1. **The template mapping.** Resolution descriptions differ by city. If another city's descriptions are free text rather than templated, rule-based classification will not transfer and a text classifier will be needed — which introduces its own annotation and model-selection questions.
2. **The location key.** BBL is specific to New York City. Other cities will need an equivalent parcel identifier, or the 100 m grid fallback implemented in `step2_analyze.py`.

---

## Citation

```bibtex
@inproceedings{closure-semantics-2026,
  title     = {Which Kind of Non-Resolution Predicts Recurrence?
               Decomposing Closure Semantics in Large-Scale Urban Service Requests},
  author    = {GU JUNJIE},			   
  booktitle = {Proceedings of the 10th International Conference on
               Computer Science and Artificial Intelligence (CSAI)},
  year      = {2026},
  address   = {Beijing, China}
}
```

## License

Code released under the MIT License. The 311 data is published by NYC Open Data under its own terms.
