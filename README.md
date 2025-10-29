# LoadMatch-Python

Python linear-optimization front-end for the Fortran LoadMatch model.

## Overview
Identify least-cost renewable + storage configurations at hourly resolution,
then verify dispatch using the original 30-second Fortran rule-based model.

## Workflow
1. Place `.dat` inputs in `data/raw/`
2. Run optimization and verification scripts in `scripts/`
3. Results are saved in `data/results_*`

## Environment Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
