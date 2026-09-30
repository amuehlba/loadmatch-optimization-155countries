# ============================================================================
# dc_cases.sh: data-center case definitions, sourced (from the repo root) by
# run_all_regions_dc_slurm.sh, reoptimize_fromseed_slurm.sh and
# regen_wwshourly_us_dc.sh.
#
# dc_case <CASE> sets DATACENTER (Fortran IFDATCEN), LABEL (--dc-label),
# LOCKS (design variables held at the seed) and EXTRA (extra flags):
#
#   case     code  results dir    free design variables
#   case1    EGS   <REGION>_dc1     none: EGS supplies the data centers, one
#                                   evaluation of the seed (IFDATCEN=1)
#   case2    WSBH  <REGION>_dc2     onshore/offshore wind, utility PV,
#                                   batteries, hydrogen (IFDATCEN=2)
#   case3    RBH   <REGION>_dc2rc   residential/commercial rooftop PV,
#                                   batteries, hydrogen (IFDATCEN=2)
#   case2bat WSB   <REGION>_dc2bat  case2 with hydrogen locked
#   case2h2  WSH   <REGION>_dc2h2   case2 with batteries locked
#
# Batteries = BATDISCH + STORHBAT; hydrogen = FCDISCH + FCCHARG + DAYH2STOR.
# ============================================================================

LOCK_CASE2="FACRESPV FACCOMPV FACSHT STORHCOLD STORHHWAT STORHPHS STORUGDYS"
LOCK_CASE3="FACONWIN FACOFFWIN FACUTILPV FACSHT STORHCOLD STORHHWAT STORHPHS STORUGDYS"

dc_case () {
  EXTRA=""
  case "$1" in
    case1)    DATACENTER=1; LABEL="";    LOCKS=""; EXTRA="--evaluate-only" ;;
    case2)    DATACENTER=2; LABEL="";    LOCKS="$LOCK_CASE2" ;;
    case3)    DATACENTER=2; LABEL="rc";  LOCKS="$LOCK_CASE3" ;;
    case2bat) DATACENTER=2; LABEL="bat"; LOCKS="$LOCK_CASE2 FCDISCH FCCHARG DAYH2STOR" ;;
    case2h2)  DATACENTER=2; LABEL="h2";  LOCKS="$LOCK_CASE2 BATDISCH STORHBAT" ;;
    *) echo "Unknown data-center case '$1'"; return 1 ;;
  esac
}
