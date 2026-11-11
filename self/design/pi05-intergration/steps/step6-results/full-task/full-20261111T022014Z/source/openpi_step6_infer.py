"""Call the pinned OpenPI policy without access to a robot endpoint."""

import argparse
import base64
import io
import json
from pathlib import Path
import time

import numpy as np
from openpi_client import websocket_client_policy
from PIL import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", type=int)
    parser.add_argument("observation", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    sample = json.loads(args.observation.read_text())
    state = np.asarray(sample["observation/state"], dtype=np.float64)
    if state.shape != (8,) or not np.isfinite(state).all():
        raise ValueError("Expected eight finite state components")
    payload = {"observation/state": state, "prompt": sample["prompt"]}
    for key in ("observation/image", "observation/wrist_image"):
        raw = base64.b64decode(sample[key], validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            if image.mode != "RGB" or image.size != (224, 224):
                raise ValueError(f"Invalid policy image {key}")
            payload[key] = np.asarray(image)
    start = time.monotonic()
    client = websocket_client_policy.WebsocketClientPolicy("127.0.0.1", args.port)
    connected = time.monotonic()
    reply = client.infer(payload)
    elapsed = time.monotonic() - connected
    actions = np.asarray(reply["actions"])
    if (actions.ndim != 2 or actions.shape[0] == 0 or actions.shape[1] != 7
            or actions.dtype.kind not in "fiu" or not np.isfinite(actions).all()):
        raise ValueError("Policy response must contain a nonempty finite seven-dimensional action sequence")
    result = {"shape": list(actions.shape), "dtype": str(actions.dtype), "actions": actions.tolist(),
              "connect_s": connected - start, "round_trip_s": elapsed,
              "policy_timing": reply.get("policy_timing"), "server_timing": reply.get("server_timing"),
              "metadata": client.get_server_metadata()}
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(f"Real policy returned {actions.shape} in {elapsed:.3f} s")


if __name__ == "__main__":
    main()
