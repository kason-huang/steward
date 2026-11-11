"""Launch the verified LIBERO server in the installed OpenPI GPU environment."""

import argparse
import os
from pathlib import Path


def main():
    package = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18065)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=package.parents[1] / "pi05-step1")
    parser.add_argument("--openpi-root", type=Path, default=package.parents[1] / "openpi-step1")
    parser.add_argument("--server-python", type=Path)
    args = parser.parse_args()
    data, source = args.data_root.resolve(), args.openpi_root.resolve()
    python = args.server_python or Path(os.environ.get("PI05_SERVER_PYTHON", str(data / "server-mirror-venv/bin/python")))
    # Python locates pyvenv.cfg through this path; resolving the symlink loses the environment.
    python = python.absolute()
    if not python.is_file() or not (source / "src/openpi").is_dir() or not (data / "checkpoints/pi05_libero/params").is_dir():
        parser.error("Existing OpenPI source, GPU environment and pi05_libero checkpoint are required")
    env = os.environ | {"PYTHONPATH": str(source / "src"), "OPENPI_DATA_HOME": str(data / "openpi-cache"),
                        "JAX_PLATFORMS": "cuda", "XLA_PYTHON_CLIENT_PREALLOCATE": "false"}
    argv = [str(python), "-u", str(package / "scripts/serve_pi05_libero.py"),
            str(data / "checkpoints/pi05_libero"), "--port", str(args.port),
            "--evidence", str(args.evidence.resolve())]
    os.chdir(source)
    os.execve(str(python), argv, env)


if __name__ == "__main__":
    main()
