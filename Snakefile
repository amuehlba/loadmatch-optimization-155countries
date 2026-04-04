# ============================================================================
# Snakefile — LoadMatch GA workflow
#
# Rule execution order (per region):
#
#   compile_fortran
#         │
#   check_inputs          ← validates all data files exist
#         │
#   preprocess_supply     ← runs IFREWRITE=1,2 if wwssupworld.REGION missing
#         │
#   [run_lp]              ← optional LP warm-start (lp_warmstart: true)
#         │
#   run_ga                ← GA optimisation (--no-plots)
#         │
#   plot_region           ← per-region figures (fig1–fig13)
#         │ (all regions)
#   plot_overview         ← cross-region figA1–A3
#         │
#        all
#
# Usage (local):
#   snakemake --cores 8 all
#
# Usage (Sherlock SLURM cluster):
#   snakemake --profile profiles/slurm --jobs 29 all
#
# Single region:
#   snakemake --cores 24 data/results_verification/EUROPE/.plots_done
#
# Regenerate DAG (3-region illustration):
#   snakemake --forceall --dag \
#       --config regions=[UNITED-STATES,EUROPE,JAPAN] \
#       | dot -Tpng -Grankdir=TB -Gsize="10,8" -Gdpi=200 -o docs/dag.png
# ============================================================================

from pathlib import Path

configfile: "config/workflow.yaml"

# ---------------------------------------------------------------------------
# Region list — override in config/workflow.yaml or via --config regions=[…]
# ---------------------------------------------------------------------------
REGIONS = [r.strip() for r in config.get("regions", [
    "AFRICA-EAST",   "AFRICA-NORTH", "AFRICA-SOUTH",  "AFRICA-WEST",
    "AUSTRALIA",     "CANADA",       "CENTRAL-AMERIC","CENTRAL-ASIA",
    "CHINA",         "CUBA",         "EUROPE",         "HAITI",
    "ICELAND",       "INDIA",        "ISRAEL",         "JAMAICA",
    "JAPAN",         "MADAGASCAR",   "MAURITIUS",      "MIDEAST",
    "NEW-ZEALAND",   "PHILIPPINES",  "RUSSIA",         "SOUTHAM-NW",
    "SOUTHAM-SE",    "SOUTHEAST-ASIA","SOUTH-KOREA",   "TAIWAN",
    "UNITED-STATES",
])]

# GA hyper-parameters (overridable in config/workflow.yaml → ga: section)
GA                 = config.get("ga", {})
GA_PARALLEL_EVALS  = GA.get("parallel_evals",   24)
GA_POPULATION      = GA.get("population",        37)
GA_GENERATIONS     = GA.get("generations",       50)
GA_MUTATION_RATE   = GA.get("mutation_rate",     0.15)
GA_MUTATION_SCALE  = GA.get("mutation_scale",    0.2)
GA_ELITE_FRAC      = GA.get("elite_frac",        0.2)
GA_MUTATION_COOLING= GA.get("mutation_cooling",  0.985)

LP_WARMSTART       = config.get("lp_warmstart",  False)

# ---------------------------------------------------------------------------
# Input helpers
# ---------------------------------------------------------------------------


def _baseline_start(wildcards):
    """Return --baseline-start argument for run_ga.

    Priority order:
      1. LP factors file  (when lp_warmstart: true)
      2. Pre-computed baseline results file  (data/raw/baseline_results.REGION.dat)
      3. 'defaults'       (use Fortran hardcoded region constants)
    """
    region = str(wildcards.region).strip()
    if LP_WARMSTART:
        return f"data/results_python/{region}/fortran_factors.dat"
    candidate = Path(f"data/raw/baseline_results.{region}.dat")
    return str(candidate) if candidate.exists() else "defaults"


def _lp_factors(wildcards):
    """Return LP factors file as a dependency only when lp_warmstart is enabled."""
    region = str(wildcards.region).strip()
    if LP_WARMSTART:
        return [f"data/results_python/{region}/fortran_factors.dat"]
    return []

# ---------------------------------------------------------------------------
# Rule: all — top-level target
# ---------------------------------------------------------------------------

def _all_targets():
    targets = (
        expand("data/results_verification/{region}/.plots_done", region=REGIONS)
        + [
            "data/results_verification/figA1_all_regions_convergence.pdf",
            "data/results_verification/figA2_all_regions_wind_solar.pdf",
            "data/results_verification/figA3_all_regions_ternary.pdf",
        ]
    )
    if LP_WARMSTART:
        targets += expand(
            "data/results_verification/{region}/.lp_plots_done",
            region=REGIONS,
        )
    return targets

rule all:
    input: _all_targets()


# ---------------------------------------------------------------------------
# Rule: compile_fortran
# ---------------------------------------------------------------------------

rule compile_fortran:
    """Compile powerworld.f → fortran/bin/powerworld (x86_64 Linux only).

    Set fortran.compile: true in config/workflow.yaml to recompile.
    When compile: false (default) and the binary already exists, the rule
    is a no-op (Snakemake touches the output to mark it up-to-date).
    """
    input:
        src = "fortran/src/powerworld.f",
    output:
        binary = "fortran/bin/powerworld",
    params:
        compile = config.get("fortran", {}).get("compile", True),
    resources:
        mem_mb  = 4000,
        runtime = 30,
    threads: 1
    run:
        if params.compile:
            shell("""
                mkdir -p fortran/build fortran/bin
                gfortran -O2 -mcmodel=medium \
                    -fdefault-real-8 -fdefault-double-8 -fno-automatic \
                    -J fortran/build \
                    {input.src} \
                    -o {output.binary}
            """)
        elif not Path(output.binary).exists():
            raise FileNotFoundError(
                f"{output.binary} does not exist and fortran.compile is false. "
                "Either set compile: true in config/workflow.yaml or provide the binary."
            )
        else:
            shell("touch {output.binary}")


# ---------------------------------------------------------------------------
# Rule: check_inputs — validate all data files before computation
# ---------------------------------------------------------------------------

rule check_inputs:
    """Validate required input files for one region before any compute starts.

    Checks:
      - Shared required: countrystats.dat, loadreg.COUNTRY2030GW,
                         heatcooldd.dat, heatfrac.dat
      - Fortran binary  (fortran/bin/powerworld)
      - Supply file:    data/raw/wwssupworld.{region}  OR  data/raw/wwssupworld.dat
      - Optional (warn): data/raw/xxEGS.{region}, data/raw/baseline_results.{region}.dat

    Exits non-zero (fails the rule) if any required file is missing, so the
    workflow stops immediately with an actionable error message.
    """
    input:
        binary         = "fortran/bin/powerworld",
        countrystats   = "data/raw/countrystats.dat",
        loadreg        = "data/raw/loadreg.COUNTRY2030GW",
        heatcooldd     = "data/raw/heatcooldd.dat",
        heatfrac       = "data/raw/heatfrac.dat",
    output:
        sentinel = touch("data/results_verification/{region}/.check_inputs_done"),
    log:
        "logs/check_inputs_{region}.log",
    resources:
        mem_mb  = 512,
        runtime = 5,
    threads: 1
    shell:
        """
        mkdir -p logs data/results_verification/{wildcards.region}
        python -m scripts.check_inputs \
            --region   {wildcards.region} \
            --sentinel {output.sentinel} \
            2>&1 | tee {log}
        """


# ---------------------------------------------------------------------------
# Rule: preprocess_supply — IFREWRITE=1,2 for new regions
# ---------------------------------------------------------------------------

rule preprocess_supply:
    """Ensure wwssupworld.{region} exists in data/raw/.

    If the aggregated supply file already exists, this rule is a no-op
    (preprocess_region() returns immediately).  If only wwssupworld.dat
    (the raw GATOR-GCMOM output) is present, runs IFREWRITE=1 then
    IFREWRITE=2 to produce the compressed regional form.

    The IFREWRITE=3 step (creates wwsmonthly.{region}) happens automatically
    during the first Fortran call in run_ga and is not handled here.
    """
    input:
        binary       = "fortran/bin/powerworld",
        checked      = "data/results_verification/{region}/.check_inputs_done",
    output:
        sentinel = touch("data/results_verification/{region}/.preprocess_done"),
    log:
        "logs/preprocess_{region}.log",
    resources:
        mem_mb  = 8000,
        runtime = 60,
    threads: 1
    shell:
        """
        python -m scripts.run_full_workflow \
            --region           {wildcards.region} \
            --preprocess-only \
            2>&1 | tee {log}
        """


# ---------------------------------------------------------------------------
# Rule: run_lp — optional LP warm-start (lp_warmstart: true)
# ---------------------------------------------------------------------------

rule run_lp:
    """Run the LP optimisation and export capacity factors.

    Only incorporated into the DAG when lp_warmstart: true in config.
    Requires a working LP solver — set the solver in your Pyomo environment
    (Gurobi recommended; HiGHS also works via pip install highspy).

    Produces:
      data/results_python/{region}/fortran_factors.dat
    which run_ga then uses as --baseline-start.
    """
    input:
        checked = "data/results_verification/{region}/.check_inputs_done",
    output:
        factors  = "data/results_python/{region}/fortran_factors.dat",
        summary  = "data/results_python/{region}/summary.dat",
    log:
        "logs/run_lp_{region}.log",
    resources:
        mem_mb  = 16000,
        runtime = 120,
    threads: 1
    shell:
        """
        mkdir -p data/results_python/{wildcards.region}
        python -m scripts.run_full_workflow \
            --region       {wildcards.region} \
            --run-lp-only \
            2>&1 | tee {log}
        """


# ---------------------------------------------------------------------------
# Rule: run_ga — GA optimisation for one region
# ---------------------------------------------------------------------------

rule run_ga:
    """Run the full GA optimisation for one region.

    Depends on check_inputs and preprocess_supply sentinels so it only runs
    after data validation and supply preprocessing have succeeded.
    When lp_warmstart: true, also depends on the LP factors file.

    Produces optimal_summary.json, baseline_summary.json, factor_history.log,
    and fortran_optimal_run.out.  Figures are generated by the separate
    plot_region rule.
    """
    input:
        binary       = "fortran/bin/powerworld",
        checked      = "data/results_verification/{region}/.check_inputs_done",
        preprocessed = "data/results_verification/{region}/.preprocess_done",
        lp_factors   = _lp_factors,
    output:
        summary     = "data/results_verification/{region}/optimal_summary.json",
        bl_summary  = "data/results_verification/{region}/baseline_summary.json",
        history     = "data/results_verification/{region}/factor_history.log",
        optimal_out = "data/results_verification/{region}/fortran_optimal_run.out",
    params:
        baseline_start   = _baseline_start,
        population       = GA_POPULATION,
        generations      = GA_GENERATIONS,
        mutation_rate    = GA_MUTATION_RATE,
        mutation_scale   = GA_MUTATION_SCALE,
        elite_frac       = GA_ELITE_FRAC,
        mutation_cooling = GA_MUTATION_COOLING,
    log:
        "logs/run_ga_{region}.log",
    resources:
        mem_mb  = 200000,
        runtime = 2880,
    threads: GA_PARALLEL_EVALS
    shell:
        """
        mkdir -p logs/snakemake
        python -m scripts.run_full_workflow \
            --region              {wildcards.region} \
            --optimizer           ga \
            --parallel-evals      {threads} \
            --ga-population       {params.population} \
            --ga-generations      {params.generations} \
            --ga-mutation-rate    {params.mutation_rate} \
            --ga-mutation-scale   {params.mutation_scale} \
            --ga-elite-frac       {params.elite_frac} \
            --ga-mutation-cooling {params.mutation_cooling} \
            --baseline-start      {params.baseline_start} \
            --no-plots \
            2>&1 | tee {log}
        """


# ---------------------------------------------------------------------------
# Rule: plot_region — per-region publication figures
# ---------------------------------------------------------------------------

rule plot_region:
    """Generate publication figures (fig1–fig13) for one completed region.

    Depends on optimal_summary.json so it runs only after run_ga succeeds.
    Uses a sentinel file (.plots_done) to track completion.
    """
    input:
        summary = "data/results_verification/{region}/optimal_summary.json",
        history = "data/results_verification/{region}/factor_history.log",
    output:
        sentinel = touch("data/results_verification/{region}/.plots_done"),
    log:
        "logs/plot_region_{region}.log",
    resources:
        mem_mb  = 8000,
        runtime = 30,
    threads: 1
    shell:
        """
        python -m scripts.plot_results \
            --region {wildcards.region} \
            2>&1 | tee {log}
        """


# ---------------------------------------------------------------------------
# Rule: plot_overview — cross-region overview figures (figA1–A3)
# ---------------------------------------------------------------------------

rule plot_overview:
    """Regenerate cross-region overview figures from all completed runs.

    Waits for every region's optimal_summary.json before running.
    Can also be invoked standalone after partial runs:
        snakemake --cores 1 plot_overview
    """
    input:
        expand(
            "data/results_verification/{region}/optimal_summary.json",
            region=REGIONS,
        ),
    output:
        figA1 = "data/results_verification/figA1_all_regions_convergence.pdf",
        figA2 = "data/results_verification/figA2_all_regions_wind_solar.pdf",
        figA3 = "data/results_verification/figA3_all_regions_ternary.pdf",
    log:
        "logs/plot_overview.log",
    resources:
        mem_mb  = 8000,
        runtime = 15,
    threads: 1
    shell:
        """
        python -m scripts.plot_results \
            --all-regions \
            2>&1 | tee {log}
        """


# ---------------------------------------------------------------------------
# Rule: run_ga_from_lp — GA optimisation warm-started from LP solution
# ---------------------------------------------------------------------------

rule run_ga_from_lp:
    """Run GA starting from LP factors → lp_summary.json + lp_ga_summary.json.

    Does NOT overwrite baseline_summary.json or optimal_summary.json, so all
    four cases (baseline, LP-eval, GA-from-baseline, GA-from-LP) coexist.

    Only included in the DAG when lp_warmstart: true in config.
    """
    input:
        binary       = "fortran/bin/powerworld",
        checked      = "data/results_verification/{region}/.check_inputs_done",
        preprocessed = "data/results_verification/{region}/.preprocess_done",
        lp_factors   = "data/results_python/{region}/fortran_factors.dat",
    output:
        lp_eval  = "data/results_verification/{region}/lp_summary.json",
        lp_ga    = "data/results_verification/{region}/lp_ga_summary.json",
    params:
        population       = GA_POPULATION,
        generations      = GA_GENERATIONS,
        mutation_rate    = GA_MUTATION_RATE,
        mutation_scale   = GA_MUTATION_SCALE,
        elite_frac       = GA_ELITE_FRAC,
        mutation_cooling = GA_MUTATION_COOLING,
    log:
        "logs/run_ga_from_lp_{region}.log",
    resources:
        mem_mb  = 200000,
        runtime = 2880,
    threads: GA_PARALLEL_EVALS
    shell:
        """
        mkdir -p logs/snakemake
        python -m scripts.run_full_workflow \
            --region              {wildcards.region} \
            --run-ga-from-lp \
            --optimizer           ga \
            --parallel-evals      {threads} \
            --ga-population       {params.population} \
            --ga-generations      {params.generations} \
            --ga-mutation-rate    {params.mutation_rate} \
            --ga-mutation-scale   {params.mutation_scale} \
            --ga-elite-frac       {params.elite_frac} \
            --ga-mutation-cooling {params.mutation_cooling} \
            --no-plots \
            2>&1 | tee {log}
        """


# ---------------------------------------------------------------------------
# Rule: plot_four_cases — input data + LP diagram + four-case comparison
# ---------------------------------------------------------------------------

rule plot_four_cases:
    """Generate LP input data figure, LP system diagram, and four-case comparison.

    Requires all four result files to exist.  Only active when lp_warmstart: true.
    """
    input:
        baseline = "data/results_verification/{region}/baseline_summary.json",
        lp_eval  = "data/results_verification/{region}/lp_summary.json",
        ga_bl    = "data/results_verification/{region}/optimal_summary.json",
        lp_ga    = "data/results_verification/{region}/lp_ga_summary.json",
    output:
        sentinel = touch("data/results_verification/{region}/.lp_plots_done"),
    log:
        "logs/plot_four_cases_{region}.log",
    resources:
        mem_mb  = 8000,
        runtime = 30,
    threads: 1
    shell:
        """
        python -m scripts.plot_results \
            --region      {wildcards.region} \
            --lp-figures \
            2>&1 | tee {log}
        """
