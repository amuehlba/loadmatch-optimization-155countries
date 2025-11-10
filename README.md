# LoadMatch Python–Fortran Workflow

This repository links a Python-based linear optimisation model with the legacy
LoadMatch Fortran simulator. The Python model sizes generation and storage
technologies to minimise annualised cost in hourly resolution. The resulting
capacity targets are converted into the multiplier factors expected by the
Fortran executable, which then performs the detailed 30-second dispatch check.

The project is organised as follows:

- `src/io/` – parsers for the legacy `.dat` inputs and the conversion utilities
  that read Python optimisation results.
- `src/optimization/` – Pyomo model definition (`model_builder.py`) that sizes
  capacities from scratch subject to technology-specific upper bounds.
- `scripts/` – runnable entry points for the Python optimisation and the
  factor-export tool.
- `fortran/` – original LoadMatch source and build artifacts.
- `data/raw/` – canonical input files (`countrystats.dat`,
  `wwssupworld.<REGION>`, `loadreg.COUNTRY2030GW`, etc.).
- `data/results_python/` – outputs from the Python model and the derived
  Fortran multiplier file.

## Getting Started

### Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> **Note**: `pyomo` requires a MILP solver. The scripts assume that the HiGHS
> executables shipped with Pyomo are available. Install alternative solvers
> if required.

### End-to-end workflow

To run the full pipeline—optimise capacities, translate them to Fortran
factors, run LoadMatch, and trigger the Hooke–Jeeves fallback if the LP
solution violates feasibility—use:

```bash
source .venv/bin/activate
python -m scripts.run_full_workflow
```

This script performs the following steps automatically:

1. Executes the Pyomo LP (`scripts.run_python_model`), producing
   `data/results_python/summary.dat`.
2. Converts the optimised capacities into the factor file
   (`scripts.export_fortran_factors`), saving it to
   `data/results_python/fortran_factors.dat`.
3. Copies the factor file to `fortran/fortran_factors.dat`, which
   `powerworld.f` now reads at start-up.
4. Runs the Fortran executable (`fortran/bin/powerworld`).
5. Inspects the Fortran log for “UNMET”/“UNSERVED”. If no violations are
   found, the workflow ends.
6. Otherwise inflates all factors by 10% and uses a Hooke–Jeeves search to
   increase capacities (and re-run Fortran) until a feasible result is found.

The exported factors that pass verification remain in
`fortran/fortran_factors.dat` for downstream use.

### Running components individually

If you prefer to inspect each step manually, the legacy split still works:

1. Solve the LP for a given region (default is `UNITED-STATES`):

   ```bash
   source .venv/bin/activate
   python -m scripts.run_python_model
   ```

   This reads the raw data under `data/raw/`, builds the Pyomo model, and
   produces `data/results_python/summary.dat` with the optimised capacities,
   storage additions, and a cost summary.

2. Translate the optimised capacities into the factors used by the Fortran
   model:

   ```bash
   python -m scripts.export_fortran_factors \
       --region UNITED-STATES \
       --summary data/results_python/summary.dat \
       --output data/results_python/fortran_factors.dat
   ```

   The generated `.dat` file contains the `FAC*` and `CSPTURBFAC` values that
   multiply the legacy capacities.

### Fortran workflow (manual factor injection)

The simplest approach keeps the Fortran source untouched:

1. Open `fortran/src/powerworld.f` and locate the block for the target region
   (e.g. CONUS section around `FACONWIN`, `FACOFFWIN`, …, `FACSHT`).
2. Replace the hard-coded constants with the factor values from
   `data/results_python/fortran_factors.dat`.
3. Ensure the path variables near line ~1050 point to the data directory.
   For a relative layout, set:

   ```fortran
         PATHHOME = './data/raw/'
         PATHTEMP = './data/raw/'
         PATHTEM1 = './data/raw/'
         PATHLOAD = './data/raw/'
         PTH2LOAD = './data/raw/'
   ```

4. Build the Fortran executable. On Linux/x86_64:

   ```bash
   mkdir -p fortran/build fortran/bin
   gfortran -O2 -mcmodel=large \
       -fdefault-real-8 -fdefault-double-8 -fno-automatic \
       -J fortran/build \
       fortran/src/powerworld.f \
       -o fortran/bin/powerworld
   ```

   > **Apple Silicon note**: Arm gfortran does not support `-mcmodel=large`.
   > Compile under Rosetta with an x86_64 toolchain or use Intel’s oneAPI
   > `ifort`.

5. Run the executable from the repository root so the relative paths resolve:

   ```bash
   ./fortran/bin/powerworld
   ```

   Outputs (e.g. `countrydata.out`, `wwsmonthly.*`) are written under
   `data/raw/` following the legacy conventions.

## Model Notes

- The Python LP constructs capacities directly (`capacity[tech]`), rather than
  treating capacities as a base plus new build. Factors are therefore computed
  by comparing optimised totals against the base values stored in
  `countrystats.dat`.
- Each technology is subject to an upper bound: defaults are set to
  5 000 000 MW and can be overridden by passing a `capacity_limits_mw`
  dictionary into `build_model`.
- Solar thermal is handled similarly with `solar_capacity_limit_mw`.
- Storage can expand beyond the existing power rating and energy duration,
  and the metadata exported by the Python model contains the base and
  additional components for reference.

## Troubleshooting

- **`ModuleNotFoundError: No module named 'src'`** – run scripts from the repo
  root with `python -m …`, or export `PYTHONPATH=$(pwd)`.
- **Fortran linker error (`ADRP out of range`)** – compile with
  `-mcmodel=large` on an x86_64 compiler (Rosetta on macOS or a regular GNU
  toolchain on Linux).
- **End-of-file on `loadreg.COUNTRY2030GW`** – ensure path variables in
  `powerworld.f` do not contain trailing blanks. Using `CHARACTER(11)` and
  short relative paths prevents this.
- **Solver failures** – check that HiGHS is installed; alternatively, point the
  Pyomo `SolverFactory` to another LP/MILP solver.

## Contributing

- Keep edits to the Fortran source minimal and well-documented to ease syncing
  with upstream versions.
- When adjusting optimisation defaults (costs, bounds, storage parameters),
  reflect the changes in this README and update the Python scripts so the
  factor export remains compatible.

## License

Respect the licensing terms that accompany the original LoadMatch Fortran code
and the associated datasets. Consult the project maintainers for publication
or redistribution permissions.
