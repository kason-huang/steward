"""Restore checkpoint arrays on the host and verify every device transfer byte for byte."""

import hashlib

import numpy as np


def restore_verified(path, *, restore, put, fetch, dtype):
    """Keep each host buffer alive through transfer and readback; reject changed weights."""
    host = restore(path, restore_type=np.ndarray, dtype=dtype)
    manifest = []

    def transfer(tree, keys=()):
        if isinstance(tree, dict):
            return {key: transfer(value, keys + (key,)) for key, value in sorted(tree.items())}
        source = np.array(tree, copy=True, order="C")
        if not np.isfinite(source).all():
            raise ValueError("Non-finite checkpoint tensor: " + "/".join(keys))
        expected = hashlib.sha256(source.tobytes()).hexdigest()
        device = put(source)
        actual = np.asarray(fetch(device))
        if (actual.shape != source.shape or actual.dtype != source.dtype
                or hashlib.sha256(actual.tobytes()).hexdigest() != expected):
            raise ValueError("Checkpoint device transfer mismatch: " + "/".join(keys))
        manifest.append({"path": list(keys), "shape": list(source.shape),
                         "dtype": str(source.dtype), "sha256": expected})
        return device

    return transfer(host), manifest


def verify_params(params, manifest, *, fetch):
    """Reject parameter mutation after model construction or the first compiled inference."""
    for item in manifest:
        value = params
        for key in item["path"]:
            value = value[key]
        array = np.asarray(fetch(value))
        if (list(array.shape) != item["shape"] or str(array.dtype) != item["dtype"]
                or hashlib.sha256(array.tobytes()).hexdigest() != item["sha256"]):
            raise ValueError("Loaded model parameter mismatch: " + "/".join(item["path"]))
