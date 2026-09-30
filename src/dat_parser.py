"""Read and write ``KEY = VALUE`` factor files (fortran_factors.dat format)."""
import re
from pathlib import Path


def read_dat(filepath: str) -> dict:
    data = {}
    with open(filepath) as f:
        for line in f:
            match = re.match(r"(\w+)\s*=\s*([\d\.\-Ee]+)", line)
            if match:
                key, val = match.groups()
                data[key] = float(val)
    return data


def write_dat(data: dict, filepath: str):
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w") as f:
        for k, v in data.items():
            f.write(f"{k} = {v}\n")
