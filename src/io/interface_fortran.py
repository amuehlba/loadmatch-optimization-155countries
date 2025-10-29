import subprocess
from pathlib import Path

def run_fortran_model(exe_path: str, input_file: str, output_dir: str):
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [exe_path, input_file],
        cwd=output_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Fortran model failed:\n{result.stderr}")
    return result.stdout
