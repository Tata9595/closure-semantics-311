# Closure Semantics in Urban Service Requests

Replication package for:

> Junjie Gu, “Which Kind of Non-Resolution Predicts Recurrence? Decomposing Closure Semantics in Large-Scale Urban Service Requests,” 2026 IEEE 9th International Conference on Data Science and Information Technology (DSIT 2026).

This repository contains the complete pipeline — data acquisition, annotation, analysis, robustness checks, and figure/table generation — for a study of 10,336,532 New York City 311 service requests filed between 2023 and 2025.

---

## What the paper finds

The official closure rate over this period is **98.24% of all tickets**, while **37.64% of classified closures** record substantive action. The two figures have different bases and measure different things: the other closure categories are administratively valid, so the second figure is not a failure rate.

We test whether closure semantics are associated with subsequent excess recurrence across the 74 complaint types that meet the inclusion rules, and find that the association depends on how “non-resolution” is defined:

|Scope|Categories|Primary ρ (Poisson, Δ = 30 d)|Empirical null (Δ = 30 d)|Range (6 settings)|
|-|-|-|-|-|
|Wide|B, C, D, E, F, G|0.122|0.296|0.076–0.296|
|**Middle**|**B, C, D, E, F**|**0.343**|**0.369**|**0.288–0.369**|
|Strict|B, C, E, F|0.331|0.363|0.217–0.369|

At the primary setting the wide–middle difference is narrowly distinguishable from zero (bootstrap 95% interval 0.01–0.45; Steiger’s test Z = 2.44, p = .015), and the pattern persists after controlling for agency. Under the empirical null, however, the gap shrinks to 0.073. The evidence is at the complaint-type level (ecological), and the scope comparison is exploratory.

Because the seven category shares sum to one, the wide scope is algebraically `1 − p_A`, so its correlation is fixed by that of the substantive-action share, which is itself only weakly related to recurrence.

---

## Data

The raw data is **not included** in this repository. It is public and can be downloaded with the script provided.

|||
|-|-|
|Source|[NYC Open Data](https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2010-to-Present/erm2-nwe9)|
|Dataset ID|`erm2-nwe9`|
|Period used|2023-01-01 to 2025-12-31|
|Volume|10,336,532 tickets (~4 GB as Parquet)|
|Retrieval time|~3 hours (API rate limited)|

An app token is recommended but not required. Register at the NYC Open Data portal and set it as an environment variable:

```bash
export NYC_APP_TOKEN="your_token"      # Linux / macOS
$env:NYC_APP_TOKEN="your_token"        # Windows PowerShell
```

### Why daily slicing

The platform’s standard pagination requires a sort clause so offsets stay stable, but sorted queries on this dataset take over 90 seconds and frequently time out, while unsorted queries return in about 3 seconds. `step1_download.py` therefore narrows each request to a single day — 7,000–9,000 tickets, below the return limit — which removes the need for sorting and pagination entirely. This reduces full retrieval from infeasible to roughly three hours.

---

## Requirements

```bash
pip install -r requirements.txt
```

||Version used|
|-|-|
|Python|3.14.4|
|DuckDB|1.5.5|
|Matplotlib|3.11.2|

Hardware used: Intel Core i7-13620H, 16 GB RAM. DuckDB is configured for a 6 GB memory limit and 4 threads. All aggregation and self-joins run inside DuckDB rather than pandas, so ten million rows are never fully materialised in memory.

---

## Pipeline

All scripts are in `scripts/`. Run in order. Times are approximate on the hardware above.

|Step|Script|What it does|Time|
|-|-|-|-|
|1|`step1_download.py`|Download via the Socrata API in daily slices; write monthly Parquet|~3 h|
|2|`step2_analyze.py`|Build the DuckDB database; field completeness; raw recurrence|~8 min|
|3|`step3_diagnose.py`|Group-size stratification; excess recurrence; resolution-text frequency|~10 min|
|4|`step4_classify.py --extract`|Produce the annotation sheet from resolution templates|~2 min|
|—|*(manual annotation)*|Fill the `code` column — see below|2–3 h|
|4|`step4_classify.py --apply`|Apply the annotation; compute per-agency and per-type shares|~5 min|
|5|`step5_robustness.py`|Five observation windows × two null models|~35 min|
|6|`step6_strict.py`|Wide vs strict scope; per-category decomposition|~10 min|
|7|`step7_finalize.py --mid`|Add the middle scope|~5 min|
|7|`step7_finalize.py --sheet` / `--kappa`|Blind sheet for the second annotator; inter-rater agreement|~2 min|
|8|`step8_finalize2.py --placeholder`|Placeholder-address diagnosis; results with and without it|~10 min|
|8|`step8_finalize2.py --scatter`|Scatter data for the main figure|~3 min|
|—|`make_figures.py`|All figures, 300 dpi|~1 min|
|—|`make_tables.py`|All tables as LaTeX|~1 min|
|R|`revision_analyses.py [path/to/out]`|Statistics added in revision: tie-handling check, bootstrap and Steiger test, agency-adjusted correlation, template- and ticket-weighted κ (needs only numpy; reads the pipeline’s `out/` folder)|~10 s|
|R|`check_agency.py`|Verifies the complaint-type-to-agency assignments used by `revision_analyses.py` against the database|<1 min|

### Helper scripts

|Script|Purpose|
|-|-|
|`peek.py`|Show the full text of a resolution template and its variants — used during annotation when a truncated prefix is ambiguous|
|`check_addr.py`|Diurnal and monthly distribution of a given address, plus comparison against the citywide pattern for the same complaint type — used to diagnose the placeholder address|

### A note on the code comments

Module documentation, usage instructions and all output shown to the user are in English. Some inline comments within the scripts remain in Chinese, the language in which the analysis was developed. They explain implementation details rather than method, and every decision that bears on the results is documented in English here and in the paper.

---

## Annotation

`data/mapping_final.csv` is the classification actually used in the paper. Each row maps the first 250 characters of a resolution description to one of eight codes.

|Code|Category|Criterion|
|-|-|-|
|**A**|Substantive action|Condition fixed; summons issued; violation served; requested service or item provided|
|**B**|No evidence found|No evidence observed on arrival; reported condition not found; no violation on inspection|
|**C**|Responsible party gone|Those responsible had left before arrival|
|**D**|No action necessary|Agency determined it need not act|
|**E**|No access|Unable to enter the dwelling or premises|
|**F**|Duplicate|Duplicates an existing ticket for the same building or condition|
|**G**|Referred / informational|Referred elsewhere, or information supplied without action on the problem|
|**X**|Unclassifiable|Text insufficient to judge|

The single criterion is: **from the reporting resident’s standpoint, did the state of the problem change?** If yes, A; if no, assign B–G by reason; if undeterminable, X.

### Prefix length

Templates are keyed on their first **250** characters. We initially used 120 and found that the housing agency’s core template had not diverged at that length — “verified that the following conditions *were corrected*” (A) and “*still exist*” (B) collapsed into a single template, leaving 2.17 million tickets unclassifiable. The parameter is set at the top of `step4_classify.py`.

### Files provided

|File|Templates|Description|
|-|-|-|
|`data/mapping_final.csv`|164|**The classification used in the paper**, after adjudication|
|`data/mapping_keyword_rules.csv`|164|The rule-based suggestions that seeded annotation, before any human review|
|`data/I1_templates_to_label_bak.csv`|164|The first annotator’s coding **before adjudication** (column `code`), saved before the annotation file was edited; GBK-encoded|
|`data/L1_kappa_sheet_utf8.csv`|164|The second annotator’s independent coding (column `code2`)|
|`data/I1_templates_to_label_utf8.csv`|164|The pipeline’s working copy of the final coding, in the format read by `revision_analyses.py`|

To run `revision_analyses.py` on a fresh pipeline run, copy the last three files into `out/` and keep their names.

### Inter-rater agreement and adjudication

All 164 templates were re-coded independently by a second annotator, a master’s student in management science and engineering who took no part in the analysis, with the first coding withheld. Agreement before adjudication: observed 81.10%, expected 0.192, **Cohen’s κ = 0.766**; ticket-weighted agreement 94.15% (ticket-weighted κ = 0.925).

**The disagreements were not random.** Fourteen of the thirty-one concentrated between “referred / informational” and “substantive action”, because the initial keyword rules triggered the referral category too broadly. Templates recording that a requested lead test kit was mailed (54,528 tickets), that violations were found with follow-up scheduled (33,174), or that a vehicle owner had claimed the vehicle (31,248) all record substantive action but contained words such as “mailed” or “contact”.

The 31 disagreements were adjudicated by the first annotator after the main analyses had been run, without the second annotator. Twenty-six were resolved to the second annotator’s label; four templates that the second annotator had left unclassifiable were coded A; and one (78,263 tickets, E vs G) kept the first coding. After adjudication the final labels and the second coding differ on five templates (κ = 0.961). This post-adjudication comparison is what `step7_finalize.py --kappa` writes to `out/L5_kappa.csv`; the pre-adjudication κ reported in the paper is reproduced by `revision_analyses.py`.

The scale of the change is visible in the files: the keyword rules left 77 of 164 templates unclassifiable and assigned 20 to substantive action; the first annotator’s coding left 6 unclassifiable and assigned 43 to A; the final classification leaves 9 unclassifiable and assigns 63 to A.

Adjudicating the disagreements changed several reported quantities:

|Quantity|Pre-adjudication|Post-adjudication|
|-|-|-|
|Substantive action share|34.24%|37.64%|
|Referred / informational share|9.04%|5.89%|
|Wide-scope mean ρ|0.005|0.134|
|Strict-scope mean ρ|0.320|0.314|
|ρ for F (duplicate)|0.151|0.315|
|ρ for D (no action necessary)|0.256|0.162|

Note the asymmetry: the strict scope barely moved while the wide scope changed substantially. The wide scope is algebraically `1 − p_A`, and the adjudication corrected precisely the A–G boundary. **An indicator defined as the complement of a single category is highly sensitive to that category’s annotation accuracy.**

---

## Excluded and flagged records

|Record|Treatment|Share of all tickets|
|-|-|-|
|All three location fields missing|Excluded: same-location judgement impossible|0.43%|
|Not closed|Excluded: no closure time|1.76%|
|One placeholder address|Flagged as a geocoding default — see below|1.68%|

The largest location–type group holds 173,945 tickets (residential noise at one Bronx address, 158.7 per day), **10.73×** the second largest, while groups two through five differ from one another by factors of 1.06–1.21. Four independent lines of evidence indicate a geocoding default rather than a real hotspot:

1. **Order-of-magnitude gap** against the next largest group.
2. **On–off monthly pattern** — exactly one ticket in each of six separate months, but 39,989 in January 2025.
3. **Anomalous in one category only** — 173,945 noise tickets against 1,560 parking and 61 heating at the same address.
4. **Diurnal distribution unlike any real reporting pattern** — peak-to-trough ratio 1.85 against a citywide 5.5 for the same complaint type, with 05:00–08:00 shares about 1.8× citywide.

The group’s resolution descriptions are entirely ordinary, matching the citywide disposition structure. **The complaints and dispositions are real events; only the location field was defaulted.** Since recurrence detection depends wholly on location, these tickets should not enter location-based analysis.

The type-level results reported in the paper were computed **with this group included**. Excluding it (`step8_finalize2.py --placeholder`) changes the three scopes’ correlations by at most 0.002 (wide 0.1224→0.1207, middle 0.3432→0.3424, strict 0.3310→0.3325) and citywide excess recurrence from 4.80 to 4.89 pp. The rule is a configurable list at the top of the script.

---

## Reproducing specific results

|Paper element|Command|Output|
|-|-|-|
|Recurrence by group size (Table I)|`step3_diagnose.py`|`out/H2_by_group_size.csv`|
|Two null models|`step5_robustness.py`|`out/J3_null_model_overall.csv`|
|Three scopes, six settings (Table IV)|`step7_finalize.py --mid`|`out/L3_three_scopes_detail.csv`|
|Per-category correlations (Table V)|`step6_strict.py`|`out/K4_rho_per_category.csv`|
|Inter-rater agreement (post-adjudication)|`step7_finalize.py --kappa`|`out/L5_kappa.csv`|
|Placeholder exclusion|`step8_finalize2.py --placeholder`|`out/M5_before_after_overall.csv`|
|Pre-adjudication κ, bootstrap, Steiger test, agency-adjusted ρ|`revision_analyses.py`|printed; `out/R1_type74_with_agency.csv`|
|All figures|`make_figures.py`|`out/fig/*.png`|
|All tables|`make_tables.py`|`out/tables.tex`|

The pipeline computes Spearman’s ρ with DuckDB’s `rank()`, which gives tied values their minimum rank; `revision_analyses.py` also reports standard average-rank values (primary setting: 0.122, 0.339 and 0.323 for the three scopes). All computation is deterministic. The circular time-shift null uses fixed offsets of {91, 182, 273, 365} days, and the bootstrap in `revision_analyses.py` uses a fixed seed (20261110).

---

## Known limitations

These are stated in the paper and repeated here so that anyone reusing the code is aware of them.

* **Recurrence is not failure of disposition.** A problem may recur independently after closure.
* **Non-recurrence is not resolution**, and the bias has a direction: residents dissatisfied with a previous disposition may stop reporting, and such cases are recorded as successful. **The estimated distortion is a lower bound.**
* **Annotation involves subjective judgement.** A systematic error was detected and corrected, but adjudication was performed by the first annotator after the analyses had been run, so further undetected bias cannot be excluded. κ = 0.766 is mid-band, not high agreement.
* **Both null models rest on assumptions** — uniform arrivals for Poisson (with a rate that includes the focal ticket), a stationary arrival rhythm for the circular shift. The two differ by a factor of 3.9 in absolute excess recurrence, and the boundary effect is much smaller under the circular shift.
* **Tickets closed near the end of 2025 have truncated observation windows**; they are retained.
* **Correlations are computed at the complaint-type level while the proposed mechanism operates at the ticket level.** Strictly, types with more C-coded closures have higher average excess recurrence; this does not establish that an individual C-coded ticket is more likely to recur. The mechanistic reading is a conjecture, not a finding.
* **The scope comparison is exploratory.** The scopes were not pre-registered, and the wide–middle difference is tested only at the primary setting.
* **The tax lot identifier operates at parcel level** and cannot distinguish units within a lot.
* **Seven of the fifteen agencies with resolution descriptions fall below the 80% annotation coverage threshold** and are excluded from agency-level comparison; three of them have no annotated template. They are not a random selection.
* **Single city.** The pipeline should transfer to other Socrata-based open data portals, but this has not been tested.

---

## Extending to other cities

`step1_download.py` targets the Socrata API and should work against other portals by changing the dataset identifier and base URL. Two things will need rework:

1. **The template mapping.** Resolution descriptions differ by city. If another city’s descriptions are free text rather than templated, rule-based classification will not transfer and a text classifier will be needed — which introduces its own annotation and model-selection questions.
2. **The location key.** BBL is specific to New York City. Other cities will need an equivalent parcel identifier, or the 100 m grid fallback implemented in `step2_analyze.py`.

---

## Citation

```bibtex
@inproceedings{gu2026closure,
  author    = {Junjie Gu},
  title     = {Which Kind of Non-Resolution Predicts Recurrence?
               Decomposing Closure Semantics in Large-Scale Urban Service Requests},
  booktitle = {Proceedings of the 2026 IEEE 9th International Conference on
               Data Science and Information Technology (DSIT)},
  year      = {2026},
  address   = {Taicang, China},
  note      = {Accepted subject to revision}
}
```

## License

Code released under the MIT License. The 311 data is published by NYC Open Data under its own terms.
