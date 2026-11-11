"""Call pi05_libero once with the retained original initial-state-0 sensor input."""

import argparse
import json
from pathlib import Path

import numpy as np
from openpi_client.websocket_client_policy import WebsocketClientPolicy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sample", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--port", type=int, default=18066)
    args = parser.parse_args()
    with np.load(args.sample) as source:
        observation = dict(source)
    observation["prompt"] = str(observation["prompt"])
    client = WebsocketClientPolicy("127.0.0.1", args.port)
    reply = client.infer(observation)
    actions = np.asarray(reply["actions"])
    assert actions.shape == (10, 7) and np.isfinite(actions).all()
    args.out.write_text(json.dumps({"sample": str(args.sample.resolve()), "port": args.port,
                                    "actions": actions.tolist()}, indent=2) + "\n")
    print(actions[0].tolist())


if __name__ == "__main__":
    main()
