"""Frozen-slot counts for Step 8; invalid or missing slots never shrink the denominator."""


def aggregate(slots, rows):
    expected = {slot["runId"]: slot for slot in slots}
    if len(expected) != len(slots):
        raise ValueError("Frozen slots have duplicate runId values")
    actual = {}
    for row in rows:
        identifier = row["runId"]
        if identifier not in expected or identifier in actual:
            raise ValueError("Unexpected or duplicate Episode: " + identifier)
        actual[identifier] = row
    complete = set(actual) == set(expected) and all(row["valid"] for row in rows)
    result = {"planned": len(slots), "recorded": len(rows), "valid": sum(row["valid"] for row in rows),
              "complete": complete, "physicalSuccesses": sum(bool(row.get("physicalSuccess")) for row in rows),
              "functionalSuccesses": sum(bool(row.get("functionalSuccess")) for row in rows),
              "physicalRate": None, "functionalRate": None}
    if complete and slots:
        result["physicalRate"] = result["physicalSuccesses"] / len(slots)
        if all(slot["environment"] == "wsim" for slot in slots):
            result["functionalRate"] = result["functionalSuccesses"] / len(slots)
    return result


def episode(slot, result, *, capability_matches=True):
    world = result.get("world")
    valid = bool(world and world.get("traceComplete") and result.get("initialStateMatches", False)
                 and not result.get("verificationError"))
    job = result.get("job", {})
    command = result.get("command", {})
    command_ok = command.get("exitCode") == 0 and command.get("timedOut") is False
    autonomous = not result.get("interventions")
    physical = bool(valid and autonomous and world.get("success"))
    functional = bool(slot["environment"] == "wsim" and physical and capability_matches and command_ok
                      and job.get("state") == "succeeded" and job.get("goalStatus") == "achieved"
                      and job.get("stopStatus") == "confirmed" and result.get("cleanup", {}).get("pass"))
    return dict(slot, valid=valid, physicalSuccess=physical, functionalSuccess=functional,
                state=job.get("state"), goalStatus=job.get("goalStatus"), stopStatus=job.get("stopStatus"),
                reasonCode=job.get("reasonCode", result.get("reason")), capabilityMatches=capability_matches,
                commandCompleted=command_ok, commandExit=command.get("exitCode"), commandTimedOut=command.get("timedOut"))
