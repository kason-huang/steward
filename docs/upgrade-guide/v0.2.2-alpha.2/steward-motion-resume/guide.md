---
kind: upgrade-guide
description: Steward clients must authorize motion again after a stop.
---

# Resume Steward motion after a stop

English | [中文](guide.zh.md)

## Change

`POST /skill/stop` previously cancelled the running skill. It now also holds the robot and invalidates the previous motion authorization. The Ego API refuses new skills while the hold is active.

## Migration

1. After stopping, read `GET /ego/motion` until it reports `state: "held"`. Inspect the robot and the hold reason before authorizing more motion.
2. Send `POST /ego/motion/resume` with the reported `epoch`. Include the current `owner` if a client holds exclusive control.
3. Check that the response reports `state: "ready"`. Predict the next skill again before dispatching it. Clients that send commands directly to the robot port must include the current `motionEpoch` in subsequent `POST /command` requests.
