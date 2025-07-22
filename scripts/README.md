# Scripts (`/scripts`)

This directory contains all the scripts used to automate the experimental workflow.

- **`/experiments`**: Contains the core scripts for running the experiments, such as triggering the evaluation process (`auto_evaluation_wula_mu_ES1.py`).
- **`/data_processing`**: Contains post-experiment scripts for collecting and processing data (`collect_metrics_...`). They aggregate data from Prometheus and logs into the final `results/`.
- **`/utils`**: Contains helper utilities called by other scripts.