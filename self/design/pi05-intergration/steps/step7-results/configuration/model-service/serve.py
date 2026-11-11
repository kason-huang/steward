"""Serve the official policy on loopback for the local LIBERO client."""
import argparse
import logging

import jax
from openpi.policies.policy_config import create_trained_policy
from openpi.serving.websocket_policy_server import WebsocketPolicyServer
from openpi.training.config import get_config


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint")
    parser.add_argument("--port", default=18005, type=int)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, force=True)
    devices = jax.devices()
    if not any(device.platform == "gpu" for device in devices):
        raise RuntimeError(f"Expected an NVIDIA JAX device, got {devices}")
    logging.info("JAX devices: %s", devices)
    config = get_config("pi05_libero")
    logging.info("Loading %s (action horizon=%d) from %s", config.name, config.model.action_horizon, args.checkpoint)
    policy = create_trained_policy(config, args.checkpoint)
    WebsocketPolicyServer(policy, host="127.0.0.1", port=args.port, metadata=policy.metadata).serve_forever()
