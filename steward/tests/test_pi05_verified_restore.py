"""Checkpoint corruption must be rejected before policy actions become available."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pi05_verified_restore import restore_verified, verify_params


def restore(arrays):
    return lambda *args, **kwargs: arrays


def test_changed_device_parameter_is_rejected():
    host = {"vision": {"kernel": np.arange(24, dtype=np.float32).reshape(3, 8)}}
    def corrupt(array):
        result = array.copy()
        result[1, 4] += 1
        return result
    with pytest.raises(ValueError, match="device transfer mismatch: vision/kernel"):
        restore_verified("checkpoint", restore=restore(host), put=corrupt, fetch=np.asarray, dtype=np.float32)


def test_original_bytes_survive_noncontiguous_input_and_later_mutation_is_rejected():
    host = {"vision": {"kernel": np.arange(24, dtype=np.float32).reshape(3, 8)[:, ::2]}}
    device, manifest = restore_verified("checkpoint", restore=restore(host), put=lambda x: x.copy(), fetch=np.asarray, dtype=np.float32)
    np.testing.assert_array_equal(device["vision"]["kernel"], host["vision"]["kernel"])
    verify_params(device, manifest, fetch=np.asarray)
    device["vision"]["kernel"][0, 0] = 123
    with pytest.raises(ValueError, match="Loaded model parameter mismatch: vision/kernel"):
        verify_params(device, manifest, fetch=np.asarray)


def test_nonfinite_checkpoint_never_reaches_device():
    def forbidden(_):
        pytest.fail("Non-finite host tensor reached the device")
    with pytest.raises(ValueError, match="Non-finite checkpoint tensor: bad"):
        restore_verified("checkpoint", restore=restore({"bad": np.array([np.nan])}), put=forbidden, fetch=np.asarray, dtype=np.float32)


def test_launcher_keeps_the_venv_interpreter_path(monkeypatch, tmp_path):
    import launch_pi05_libero as launcher
    data, source = tmp_path / "data", tmp_path / "source"
    (data / "checkpoints/pi05_libero/params").mkdir(parents=True)
    (source / "src/openpi").mkdir(parents=True)
    base = tmp_path / "python-base"
    base.write_text("base interpreter")
    python = data / "server-mirror-venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(base)
    calls = []
    monkeypatch.setattr(sys, "argv", ["launcher", "--data-root", str(data), "--openpi-root", str(source),
                                      "--evidence", str(tmp_path / "evidence")])
    monkeypatch.setattr(launcher.os, "chdir", lambda path: None)
    def execute(executable, argv, env):
        calls.append((executable, argv, env))
        raise SystemExit(0)
    monkeypatch.setattr(launcher.os, "execve", execute)
    with pytest.raises(SystemExit):
        launcher.main()
    executable, argv, env = calls[0]
    assert executable == str(python)
    assert argv[2].endswith("/scripts/serve_pi05_libero.py")
    assert env["PYTHONPATH"] == str(source / "src")
    assert env["JAX_PLATFORMS"] == "cuda"
