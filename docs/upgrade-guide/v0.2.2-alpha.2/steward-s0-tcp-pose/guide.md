---
kind: upgrade-guide
description: "Steward S0 pose commands take a base-relative TCP position and unit quaternion."
---

# Steward S0 tool-centre pose

English | [中文](guide.zh.md)

## Change

XLeRobot's `body_set_pose` takes `position: [x, y, z]` in metres and `orientation: [w, x, y, z]` as a unit quaternion. It previously took `x`, `z`, and `pitch`. Fixed-base PiPER accepts the same pose request. The HTTP command carries these fields inside `pose`. The command returns `pose` in the request format, with the measured timestamp, gripper state and command ID.

## Migration

1. Query `ego_capabilities` for the enabled S0 tools, the coordinate convention and the working arm's constraints. HTTP clients can read the descriptor from `GET /ego/control` under `capabilities`.
2. For an existing XLeRobot command, use `[x, fixedYM, z]` and the unit quaternion `[0, cos(pitch/2), 0, sin(pitch/2)]`. Read `fixedYM` from the arm descriptor. Get the current TCP pose in the same format from `ego_gripper_pose.tcp_pose`.
3. Confirm that the response state is `reached` and use its measured `pose`. PiPER setups that enable native joint commissioning can continue to send those targets through `body_set_joints`.
