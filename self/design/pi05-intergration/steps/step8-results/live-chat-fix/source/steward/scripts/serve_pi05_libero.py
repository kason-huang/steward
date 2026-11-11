"""Serve pinned pi05_libero with host checkpoint restore and verified GPU parameters."""

import argparse
import hashlib
import json
import logging
from pathlib import Path
import subprocess

import flax.nnx as nnx
import jax
import jax.numpy as jnp
import numpy as np

from openpi import transforms
from openpi.models import model as model_api
from openpi.policies import policy as policy_api
from openpi.serving.websocket_policy_server import WebsocketPolicyServer
from openpi.training import checkpoints
from openpi.training.config import get_config

from pi05_verified_restore import restore_verified, verify_params


class VerifiedPolicy:
    """Expose actions only after the first compilation leaves the verified parameters intact."""

    def __init__(self, policy, manifest, metadata):
        self.policy = policy
        self.manifest = manifest
        self.metadata = metadata
        self.compilation_verified = False

    def infer(self, observation):
        result = self.policy.infer(observation)
        if not self.compilation_verified:
            verify_params(nnx.state(self.policy._model).to_pure_dict(), self.manifest, fetch=jax.device_get)
            self.compilation_verified = True
        if not np.isfinite(np.asarray(result["actions"])).all():
            raise ValueError("pi05_libero produced non-finite actions")
        return result


def load(checkpoint, output):
    """Use the pinned official transforms, with explicit host restore before model construction."""
    source = Path(model_api.__file__).resolve().parents[3]
    pin = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    if pin != "215abfb217dbac7d5f1273282331b9b1866c0479":
        raise ValueError("Unexpected OpenPI source revision: " + pin)
    if not any(device.platform == "gpu" for device in jax.devices()):
        raise RuntimeError("pi05_libero requires the configured GPU")
    config = get_config("pi05_libero")
    params, manifest = restore_verified(checkpoint / "params", restore=model_api.restore_params,
        put=lambda array: jax.device_put(array).block_until_ready(), fetch=jax.device_get, dtype=jnp.bfloat16)
    model = config.model.load(params)
    verify_params(nnx.state(model).to_pure_dict(), manifest, fetch=jax.device_get)
    data = config.data.create(config.assets_dirs, config.model)
    norm = checkpoints.load_norm_stats(checkpoint / "assets", data.asset_id)
    # Same ordered transformations as the pinned create_trained_policy JAX path.
    policy = policy_api.Policy(model, transforms=[transforms.InjectDefaultPrompt(None),
        *data.data_transforms.inputs, transforms.Normalize(norm, use_quantiles=data.use_quantile_norm),
        *data.model_transforms.inputs], output_transforms=[*data.model_transforms.outputs,
        transforms.Unnormalize(norm, use_quantiles=data.use_quantile_norm), *data.data_transforms.outputs],
        metadata=config.policy_metadata)
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    metadata = dict(policy.metadata, restore="host_then_device_verified", paramsSHA256=digest, openpi=pin)
    output.mkdir(parents=True, exist_ok=True)
    (output / "parameter-integrity.json").write_text(json.dumps({"metadata": metadata, "tensors": manifest}, indent=2) + "\n")
    return VerifiedPolicy(policy, manifest, metadata)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--port", type=int, default=18065)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, force=True)
    policy = load(args.checkpoint.resolve(), args.evidence.resolve())
    logging.info("All %d checkpoint transfers verified; parameter SHA256 %s", len(policy.manifest), policy.metadata["paramsSHA256"])
    WebsocketPolicyServer(policy, host="127.0.0.1", port=args.port, metadata=policy.metadata).serve_forever()


if __name__ == "__main__":
    main()
