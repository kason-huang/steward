"""Check for contradictory puzzle occupancy in the robot's final Ego State."""

from __future__ import annotations

import math
import re
from itertools import combinations

from steward.net import HttpError, call


# Recognize explicit status clauses at the start of a line or after sentence punctuation.
# Check both kinds so a later success claim cannot hide behind an earlier failure report.
_STATUS_PREFIX = r"(?:^|[。！？；：，、.!?;:,\n])\s*[*_#> \-]*(?:(?:拼图任务|任务|拼图)(?:完成状态)?\s*[:：]?\s*)?[*_ ]*"
_NONCOMPLETION = re.compile(_STATUS_PREFIX + r"(?:未(?:能|确认)?完成|尚未完成|未确认|无法确认|失败|没有完成)")
_COMPLETION = re.compile(_STATUS_PREFIX + r"(?:任务完成|拼图完成|已(?:经)?(?:成功)?(?:完成|拼好)|成功完成|拼好|完成)(?=$|[\s。！？；：，.!?;:,*_])")


def puzzle_reply_override(ego_url: str, expected: int, reply: str) -> str | None:
    """Return an unconfirmed reply when the robot cannot resolve puzzle occupancy.

    This check uses robot observations only. Passing it leaves the harness's own
    conclusion intact because occupancy alone cannot verify the printed picture.
    An explicit noncompletion report already conveys the uncertainty and keeps its diagnostics.
    """
    if _NONCOMPLETION.search(reply) and not _COMPLETION.search(reply):
        return None
    try:
        state = call("GET", f"{ego_url}/ego/summary", timeout=2.0)
        pieces = [obj for obj in state["objects"] if obj["category"] == "puzzle_piece"]
        collisions = [(a["id"], b["id"]) for a, b in combinations(pieces, 2) if _share_slot(a, b)]
    except (HttpError, OSError, KeyError, IndexError, TypeError, ValueError):
        # A missing or unreadable observation cannot support a completion report.
        return "拼图完成状态：未确认。无法读取或核对机器人自我状态中的拼图格位。"
    reasons = []
    if len(pieces) != expected:
        reasons.append(f"预期 {expected} 块，机器人自我状态记录了 {len(pieces)} 条拼图块轨迹")
    if collisions:
        pairs = "、".join(f"{a} / {b}" for a, b in collisions)
        reasons.append(f"疑似同格冲突（{pairs}）")
    return f"拼图完成状态：未确认。{'，'.join(reasons)}。" if reasons else None


def _share_slot(a: dict, b: dict) -> bool:
    """Flag substantial planar overlap of two square piece estimates on the same support.

    In a piece-aligned frame, require overlap of at least one third of a piece
    along both axes. This tolerates small overlaps at a normal grid seam.
    """
    if not a["on"] or a["on"] != b["on"]:
        return False
    dx = a["position"][0] - b["position"][0]
    dy = a["position"][1] - b["position"][1]
    yaw = math.radians(a["yawDeg"])
    c, s = math.cos(yaw), math.sin(yaw)
    side = min(*a["sizeM"][:2], *b["sizeM"][:2])
    return max(abs(c * dx + s * dy), abs(c * dy - s * dx)) < 2 * side / 3
