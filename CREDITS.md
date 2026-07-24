# Credits

## Data
All data in this platform is **synthetic** and generated deterministically at
runtime by `dip/data.py` (seeded NumPy). It represents no real company,
customer, or transaction. There is no third-party dataset dependency.

## Open-source components
- **Flask** — BSD-3-Clause
- **Jinja2** — BSD-3-Clause
- **NumPy** — BSD-3-Clause
- **SciPy** — BSD-3-Clause (`scipy.optimize.milp`, `linprog`, HiGHS backend)
- **OR-Tools** — Apache-2.0 (constraint programming / CVRP routing)
- **pandas** — BSD-3-Clause
- **matplotlib** — matplotlib license (BSD-compatible; used for PDF export)
- **openpyxl** — MIT (Excel export)

All front-end charts are hand-built (HTML Canvas + inline SVG). No charting
CDN or third-party JavaScript is used — this is deliberate, to keep the
platform self-contained and to showcase the data-visualisation work.

## Author
Dimitres Kisimov, 2026.
