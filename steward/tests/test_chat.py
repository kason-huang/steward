"""Operator chat: World Harness and Ego Harness turns, and exclusive robot control."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from steward.assets.generators import steward_bot
from steward.ego.monitor import MonitorConfig
from steward.ego.runtime import EgoRuntime, RobotLink, RuntimeConfig
from steward.ego.server import ego_api
from steward.net import HttpError, JsonServer, call
from steward.world.chat import ChatConfig, HarnessChat, ROBOT_BRIEF, capabilities_digest, robot_brief
from steward.world.server import StewardHost

EVENTS = [
    {"type": "session", "sessionId": "session-1"},
    {"type": "tool_call", "callId": "c1", "tool": "some_tool", "input": {"action": "x"}},
    {"type": "tool_result", "callId": "c1", "status": "completed", "result": "{\"ok\": true}"},
    {"type": "text", "text": "完成"},
    {"type": "final", "sessionId": "session-1", "text": "完成"},
]


def _launcher(tmp_path, body: str) -> Path:
    """An executable stand-in for the DSH launcher that runs the Python ``body``: a sh wrapper, or on
    Windows a cmd wrapper as npm generates there."""
    script = tmp_path / "fake_dsh.py"
    script.write_text("import os, shutil, sys, time\n" + body, encoding="utf-8")
    if os.name == "nt":
        launcher = tmp_path / "dsh.CMD"
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        launcher = tmp_path / "dsh"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    return launcher


def _fake_dsh(tmp_path):
    """A stand-in launcher that records argv, stdin and the patch overlay, then prints run events."""
    lines = "\n".join(json.dumps(e, ensure_ascii=False) for e in EVENTS)
    return _launcher(tmp_path, (
        "home = os.environ['DSH_HOME']\n"
        "args = sys.argv[1:]\n"
        "if '--patch' in args:\n"
        "    shutil.copy(args[args.index('--patch') + 1], os.path.join(home, 'patch.yml'))\n"
        "with open(os.path.join(home, 'argv'), 'a', encoding='utf-8') as fh:\n"
        "    fh.write(''.join(a + '\\n' for a in args) + '\\n')\n"
        "with open(os.path.join(home, 'stdin'), 'w', encoding='utf-8') as fh:\n"
        "    fh.write(sys.stdin.buffer.read().decode('utf-8'))\n"
        "with open(os.path.join(home, 'cwd'), 'w', encoding='utf-8') as fh:\n"
        "    fh.write(os.getcwd())\n"
        f"sys.stdout.buffer.write({lines!r}.encode('utf-8') + b'\\n')\n"))


def _home(tmp_path, name: str, profile: str):
    home = tmp_path / name
    (home / "profiles" / profile).mkdir(parents=True)
    return home


def _wait_idle(chat: HarnessChat, key: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while chat.agents[key].busy and time.time() < deadline:
        time.sleep(0.02)
    assert not chat.agents[key].busy


def test_a_robot_ego_api_answering_an_unexpected_shape_does_not_break_the_chat_state(tmp_path):
    """The console polls /console/chat continuously; a malformed robot answer must be absorbed.

    Only "active" is read from /ego/alerts, and any answer without it (an older or different
    Ego API, a proxy error page) raised out of the poll and took the whole chat endpoint down.
    """
    api = JsonServer({("GET", "/ego/alerts"): lambda q, b: {"alerts": []}}).start()
    try:
        robot = _home(tmp_path, "robot-1", "ego")
        chat = HarnessChat(ChatConfig(dsh=_fake_dsh(tmp_path), cwd=tmp_path, env_file=tmp_path / ".env",
                                      robot_home=robot, ego_api=api.url),
                           lambda: "http://127.0.0.1:7999")
        state = chat.state()
        assert state["agents"]["robot"]["alerts"] == []
    finally:
        api.stop()


def test_world_chat_keeps_its_session_and_targets_this_hosts_wca(tmp_path):
    world = _home(tmp_path, "world", "world-task")
    chat = HarnessChat(ChatConfig(dsh=_fake_dsh(tmp_path), cwd=tmp_path, env_file=tmp_path / ".env", world_home=world),
                       lambda: "http://127.0.0.1:7999")
    chat.send("world", "把杯子放到桌子上")
    _wait_idle(chat, "world")
    chat.send("world", "再放回去")
    _wait_idle(chat, "world")
    state = chat.state()
    assert [m["role"] for m in state["messages"]] == ["user", "tool", "assistant", "user", "tool", "assistant"]
    assert {m["agent"] for m in state["messages"]} == {"world"}
    assert state["messages"][1]["status"] == "completed" and state["agents"]["world"]["sessionId"] == "session-1"
    turns = (world / "argv").read_text(encoding="utf-8").split("\n\n")
    assert "--session-id" not in turns[0] and "--session-id\nsession-1" in turns[1]
    assert "http://127.0.0.1:7999" in (world / "patch.yml").read_text(encoding="utf-8")
    assert (world / "stdin").read_text(encoding="utf-8").startswith("操作员请求：")
    assert chat.state(since=state["messages"][-1]["id"])["messages"] == []
    assert not state["agents"]["robot"]["available"]


def test_console_skills_match_the_tools_reported_by_the_robot_session(tmp_path):
    plan = {"body": "test-body", "availableSkills": ["pick", "place", "look_around"],
            "availableS0Tools": {"body_stop": "control", "ego_motion_status": "read", "ego_gripper_pose": "read",
                                 "body_set_joints": "control", "body_set_gripper": "control"}}
    api = JsonServer({("GET", "/ego/plan"): lambda q, b: plan}).start()
    try:
        home = _home(tmp_path, "robot-1", "ego")
        event = {"type": "steward_tool_catalog", "sessionId": "session-1",
                 "tools": ["run_code", "ego_predict", "skill_run", "ego_explore",
                           "body_stop", "ego_motion_status", "body_set_joints", "body_set_gripper", "body_resume"],
                 "predictSkills": ["pick", "navigate"], "readOnly": True}
        events = [
            {"type": "session", "sessionId": "session-1"},
            event,
            {**event, "sessionId": "child-session", "tools": [], "readOnly": False},
        ]
        launcher = _launcher(tmp_path,
                             f"assert os.environ['STEWARD_CONSOLE_SESSION_TOOLS'] == '1'\n"
                             f"sys.stdin.read()\nprint({chr(10).join(map(json.dumps, events))!r})\n")
        chat = HarnessChat(ChatConfig(dsh=launcher, cwd=tmp_path, env_file=tmp_path / ".env",
                                      robot_home=home, ego_api=api.url), lambda: "unused")
        chat.agents["robot"].before_turn = None
        chat.agents["robot"].after_turn = None
        assert chat.state()["agents"]["robot"]["sessionSkills"] is None
        chat.send("robot", "观察")
        _wait_idle(chat, "robot")
        robot = chat.state()["agents"]["robot"]
        assert robot["sessionSkills"] == ["pick", "look_around"]
        assert robot["profile"] == "ego" and robot["readOnly"]
        assert robot["sessionS0Tools"] == [{"name": "body_stop", "enabled": False},
                                           {"name": "ego_motion_status", "enabled": True},
                                           {"name": "body_set_joints", "enabled": False},
                                           {"name": "body_set_gripper", "enabled": False}]

        event.update(tools=["run_code", "ego_predict", "body_stop", "ego_gripper_pose", "body_set_joints"], readOnly=False)
        _launcher(tmp_path, f"sys.stdin.read()\nprint({json.dumps(event)!r})\n")
        chat.send("robot", "再看一下")
        _wait_idle(chat, "robot")
        robot = chat.state()["agents"]["robot"]
        assert robot["sessionSkills"] == []
        assert robot["sessionS0Tools"] == [{"name": "body_stop", "enabled": True},
                                           {"name": "ego_gripper_pose", "enabled": True},
                                           {"name": "body_set_joints", "enabled": True}]
        chat.reset("robot")
        assert chat.state()["agents"]["robot"]["sessionSkills"] is None
        assert chat.state()["agents"]["robot"]["sessionS0Tools"] is None
    finally:
        api.stop()


def test_robot_chat_takes_control_for_the_turn_and_gives_it_back(tmp_path):
    robot = _home(tmp_path, "robot-1", "ego")
    library = robot / "steward-capabilities"
    library.mkdir()
    (library / "flip_flat_objects_face_up.json").write_text(json.dumps({
        "name": "flip_flat_objects_face_up", "layer": "skill", "status": "verified",
        "description": "Inspects flat objects and flips face-down ones picture side up.",
    }), encoding="utf-8")
    calls = []

    def control(q, b):
        calls.append(b["action"])
        assert b["owner"] == "operator"
        return {"owner": "operator" if b["action"] == "acquire" else None}

    ego = JsonServer({("POST", "/ego/control"): control, ("GET", "/ego/control"): lambda q, b: {"owner": None}}).start()
    try:
        chat = HarnessChat(ChatConfig(dsh=_fake_dsh(tmp_path), cwd=tmp_path, env_file=tmp_path / ".env",
                                      robot_home=robot, ego_api=ego.url), lambda: "unused")
        chat.send("robot", "把杯子放到餐桌上")
        _wait_idle(chat, "robot")
    finally:
        ego.stop()
    assert calls == ["acquire", "release"]
    patch = (robot / "patch.yml").read_text(encoding="utf-8")
    assert "steward-ego-runtime" in patch and ego.url in patch and "owner: operator" in patch
    cwd = Path((robot / "cwd").read_text(encoding="utf-8"))
    assert cwd.parts[-2:] == ("robot-1", "workspace"), "the robot runs in its empty workspace"
    stdin = (robot / "stdin").read_text(encoding="utf-8")
    assert stdin.startswith("你是家用服务机器人") and "World State" not in stdin
    assert "Steward 单手" in stdin and "充分利用这一只手" in stdin
    assert "flip_flat_objects_face_up（skill，verified）" in stdin
    assert stdin.endswith("\n\n操作员任务：把杯子放到餐桌上"), "the capability list precedes the instruction marker"
    assert "interiorSpread" in ROBOT_BRIEF and "相关系数" in ROBOT_BRIEF
    assert "双手" in robot_brief("cobot-magic") and "arm: \"L\"" in robot_brief("cobot-magic")
    assert "单手" in robot_brief("steward-bot") and robot_brief(None) == ROBOT_BRIEF
    assert capabilities_digest(None) == "" and capabilities_digest(tmp_path / "empty") == ""
    assert [m["agent"] for m in chat.state()["messages"]] == ["robot"] * 3


def test_stopping_a_robot_turn_ends_the_run_and_releases_control(tmp_path):
    robot = _home(tmp_path, "robot-1", "ego")
    script = _launcher(tmp_path, "sys.stdin.read()\nprint('{\"type\": \"session\", \"sessionId\": \"s-1\"}', flush=True)\ntime.sleep(30)\n")
    calls = []

    def control(q, b):
        calls.append(b["action"])
        return {"owner": None}

    ego = JsonServer({("POST", "/ego/control"): control, ("GET", "/ego/control"): lambda q, b: {"owner": None},
                      ("POST", "/skill/stop"): lambda q, b: calls.append("stop") or {"stopped": None}}).start()
    try:
        chat = HarnessChat(ChatConfig(dsh=script, cwd=tmp_path, env_file=tmp_path / ".env", robot_home=robot,
                                      ego_api=ego.url), lambda: "unused")
        chat.send("robot", "去拿椅子")
        deadline = time.time() + 5
        while chat.agents["robot"].session_id is None and time.time() < deadline:
            time.sleep(0.02)
        started = time.time()
        assert chat.cancel("robot") == {"stopped": True}
        _wait_idle(chat, "robot")
        assert time.time() - started < 5
    finally:
        ego.stop()
    assert calls == ["acquire", "stop", "release"]
    roles = [m["role"] for m in chat.state()["messages"]]
    assert "system" in roles and "error" not in roles
    assert chat.cancel("robot") == {"stopped": False}


def test_robot_wait_reports_llm_then_run_code_then_llm_again(tmp_path):
    """The console progress bar needs a live wait kind while the turn is busy."""
    robot = _home(tmp_path, "robot-1", "ego")
    gate = tmp_path / "gate"
    script = _launcher(tmp_path, (
        "import json\n"
        "sys.stdin.read()\n"
        "print(json.dumps({'type': 'session', 'sessionId': 's-1'}), flush=True)\n"
        "time.sleep(0.15)\n"
        "print(json.dumps({'type': 'tool_call', 'callId': 'c1', 'tool': 'run_code',"
        " 'input': {'description': 'inspect pieces'}}), flush=True)\n"
        f"open({str(gate)!r}, 'w').write('run')\n"
        "time.sleep(0.2)\n"
        "print(json.dumps({'type': 'tool_result', 'callId': 'c1', 'status': 'completed',"
        " 'result': 'ok'}), flush=True)\n"
        "time.sleep(0.15)\n"
        "print(json.dumps({'type': 'final', 'sessionId': 's-1', 'text': 'done'}), flush=True)\n"))
    ego = JsonServer({("POST", "/ego/control"): lambda q, b: {"owner": None},
                      ("GET", "/ego/control"): lambda q, b: {"owner": None}}).start()
    try:
        chat = HarnessChat(ChatConfig(dsh=script, cwd=tmp_path, env_file=tmp_path / ".env",
                                      robot_home=robot, ego_api=ego.url), lambda: "unused")
        chat.send("robot", "看一眼桌面")
        wait = chat.state()["agents"]["robot"]["wait"]
        assert wait and wait["kind"] == "llm" and isinstance(wait["since"], float)
        deadline = time.time() + 5
        while not gate.exists() and time.time() < deadline:
            time.sleep(0.02)
        assert gate.exists()
        wait = chat.state()["agents"]["robot"]["wait"]
        assert wait["kind"] == "run_code" and wait["tool"] == "run_code" and wait["detail"] == "inspect pieces"
        while (chat.state()["agents"]["robot"].get("wait") or {}).get("kind") != "llm" and time.time() < deadline:
            time.sleep(0.02)
        assert chat.state()["agents"]["robot"]["wait"]["kind"] == "llm"
        _wait_idle(chat, "robot")
    finally:
        ego.stop()
    assert chat.state()["agents"]["robot"]["wait"] is None


def _recording_dsh(tmp_path, *, wait_for_dispatch=False):
    """A stand-in launcher that records each turn (resumed or not, its stdin), prints a session and,
    for a task marked ⏳, keeps running until stopped."""
    return _launcher(tmp_path, (
        "import json\n"
        "args = sys.argv[1:]\n"
        "text = sys.stdin.buffer.read().decode('utf-8')\n"
        "with open(os.path.join(os.environ['DSH_HOME'], 'turns'), 'a', encoding='utf-8') as fh:\n"
        "    fh.write(json.dumps({'resumed': '--session-id' in args, 'text': text}) + '\\n')\n"
        "print(json.dumps({'type': 'session', 'sessionId': 's-1'}), flush=True)\n"
        "if text.endswith('⏳'):\n"
        "    time.sleep(30)\n"
        + ("if '--session-id' not in args:\n"
           "    while not os.path.exists(os.path.join(os.environ['DSH_HOME'], 'job-dispatched')):\n"
           "        time.sleep(0.01)\n" if wait_for_dispatch else "")))


def _turns(home: Path) -> list[dict]:
    return [json.loads(line) for line in (home / "turns").read_text(encoding="utf-8").splitlines()]


def _robot_ego(calls: list) -> JsonServer:
    return JsonServer({("POST", "/ego/control"): lambda q, b: calls.append(b["action"]) or {"owner": None},
                       ("GET", "/ego/control"): lambda q, b: {"owner": None},
                       ("POST", "/skill/stop"): lambda q, b: calls.append("stop") or {"stopped": None}}).start()


def test_a_task_given_anew_stops_the_running_turn_and_starts_a_new_conversation(tmp_path):
    robot = _home(tmp_path, "robot-1", "ego")
    calls = []
    ego = _robot_ego(calls)
    try:
        chat = HarnessChat(ChatConfig(dsh=_recording_dsh(tmp_path), cwd=tmp_path, env_file=tmp_path / ".env",
                                      robot_home=robot, ego_api=ego.url), lambda: "unused")
        chat.send("robot", "收拾餐桌⏳")
        deadline = time.time() + 5
        while chat.agents["robot"].session_id is None and time.time() < deadline:
            time.sleep(0.02)
        chat.assign("robot", "把拼图拼好")
        _wait_idle(chat, "robot")
    finally:
        ego.stop()
    turns = _turns(robot)
    assert [t["resumed"] for t in turns] == [False, False], "the new task opens a new conversation"
    assert turns[1]["text"].startswith("你是家用服务机器人") and turns[1]["text"].endswith("把拼图拼好")
    assert calls == ["acquire", "stop", "release", "acquire", "release"]

@pytest.mark.parametrize("moving", [False, True])
def test_intent_revision_resumes_the_same_ego_session_in_simulation(kitchen_root, tmp_path, moving):
    robot = _home(tmp_path, "robot-1", "ego")
    host = StewardHost(kitchen_root, scenario="phase0-kitchen", speed=6).start()
    link = RobotLink(host.robot_servers["w:robot-1"].url)
    runtime = EgoRuntime(link, steward_bot().to_dict())
    runtime.start()
    ego = ego_api(runtime).start()
    chat = HarnessChat(ChatConfig(dsh=_recording_dsh(tmp_path), cwd=tmp_path, env_file=tmp_path / ".env",
                                  robot_home=robot, ego_api=ego.url), lambda: "unused")
    try:
        chat.send("robot", "把杯子放到左边⏳")
        deadline = time.monotonic() + 5
        while chat.agents["robot"].session_id is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert chat.agents["robot"].session_id == "s-1"
        job = runtime.explore(owner="operator") if moving else None
        before = link.motion_status()
        updated = chat.revise_robot_intent("改放右边")
        with pytest.raises(HttpError, match="task intent is stale"):
            call("POST", f"{ego.url}/ego/explore", {"owner": "operator"})
        deadline = time.monotonic() + 10
        while (chat.agents["robot"].revising or chat.agents["robot"].busy) and time.monotonic() < deadline:
            time.sleep(0.02)
        turns = _turns(robot)
        assert [turn["resumed"] for turn in turns] == [False, True]
        assert "改放右边" in turns[1]["text"]
        assert "重新观察" in turns[1]["text"]
        assert updated["intentRevision"] == chat.agents["robot"].intent_revision
        after = link.motion_status()
        assert after["state"] == "ready"
        if moving:
            assert runtime.job(job["id"])["state"] == "stopped"
            assert before["epoch"] != after["epoch"]
        else:
            assert before["epoch"] == after["epoch"], "an idle body needs no stop"
    finally:
        chat.stop()
        ego.stop()
        runtime.stop()
        host.stop()


def test_independent_monitor_holds_the_robot_and_wakes_ego_in_simulation(kitchen_root, tmp_path):
    robot = _home(tmp_path, "robot-1", "ego")
    host = StewardHost(kitchen_root, scenario="phase0-kitchen", speed=2).start()
    link = RobotLink(host.robot_servers["w:robot-1"].url)
    config = RuntimeConfig(monitor=MonitorConfig(poll_s=0.05, job_timeout_s=0.3))
    runtime = EgoRuntime(link, steward_bot().to_dict(), config)
    runtime.start()
    ego = ego_api(runtime).start()
    chat = HarnessChat(ChatConfig(dsh=_recording_dsh(tmp_path), cwd=tmp_path, env_file=tmp_path / ".env",
                                  robot_home=robot, ego_api=ego.url), lambda: "unused")
    try:
        chat.send("robot", "看看周围有什么⏳")
        deadline = time.monotonic() + 5
        while chat.agents["robot"].session_id is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert chat.agents["robot"].session_id == "s-1"
        job = runtime.explore(owner="operator", task_id=chat.agents["robot"].task.id)
        deadline = time.monotonic() + 10
        while (chat.agents["robot"].revising or chat.agents["robot"].busy) and time.monotonic() < deadline:
            time.sleep(0.02)
        turns = _turns(robot)
        assert [turn["resumed"] for turn in turns] == [False, True]
        assert "skill-deadline" in turns[1]["text"] and "保持暂停" in turns[1]["text"]
        assert runtime.wait(job["id"], timeout=3)["state"] == "stopped"
        motion = link.motion_status()
        assert motion["reason"] == "protection"
        assert motion["state"] in ("stopping", "held")
        assert motion["epoch"] != job["motionEpoch"]
        events = call("GET", f"{ego.url}/ego/events")["events"]
        deadline_events = [event for event in events if event["kind"] == "skill-deadline"]
        assert len(deadline_events) == 1
        assert deadline_events[0]["job"] == job["id"] and deadline_events[0]["holdAccepted"]
    finally:
        chat.stop()
        ego.stop()
        runtime.stop()
        host.stop()


@pytest.mark.parametrize("outcome", ["protection", "finished", "cancelled"])
def test_detached_job_routes_events_to_its_original_task(kitchen_root, tmp_path, outcome):
    robot = _home(tmp_path, "robot-1", "ego")
    host = StewardHost(kitchen_root, scenario="phase0-kitchen", speed=1).start()
    link = RobotLink(host.robot_servers["w:robot-1"].url)
    config = RuntimeConfig(monitor=MonitorConfig(poll_s=0.05, job_timeout_s=0.7 if outcome == "protection" else None))
    runtime = EgoRuntime(link, steward_bot().to_dict(), config)
    runtime.start()
    ego = ego_api(runtime).start()
    chat = HarnessChat(ChatConfig(dsh=_recording_dsh(tmp_path, wait_for_dispatch=True), cwd=tmp_path,
                                  env_file=tmp_path / ".env", robot_home=robot, ego_api=ego.url), lambda: "unused")
    try:
        chat.send("robot", "执行后继续核验")
        agent = chat.agents["robot"]
        deadline = time.monotonic() + 5
        while agent.session_id is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert agent.session_id == "s-1"
        task = agent.task
        if outcome == "finished":
            position = link.sensors()["odometry"]
            params = {key: position[key] for key in ("x", "y", "yaw")}
            receipt = runtime.predict("navigate", params)
            assert receipt["withinRiskLimit"], receipt["issues"]
            # Hold the physics clock until the fake model has yielded the turn. A zero-distance
            # navigation can otherwise finish before the process exits on a loaded host.
            with host.sim.lock:
                host.sim.paused = True
            job = call("POST", f"{ego.url}/skill/run", {"receipt": receipt["id"], "owner": "operator",
                                                       "taskId": task.id, "notifyCompletion": True})
        else:
            job = call("POST", f"{ego.url}/ego/explore", {"owner": "operator", "taskId": task.id})
        (robot / "job-dispatched").touch()
        _wait_idle(chat, "robot")
        assert runtime.job(job["id"])["state"] == "running"
        if outcome == "finished":
            with host.sim.lock:
                host.sim.paused = False
        if outcome == "cancelled":
            assert chat.cancel("robot") == {"stopped": True}
            assert runtime.wait(job["id"], timeout=3)["state"] == "stopped"
            assert task.ended.is_set()
            assert len(_turns(robot)) == 1
            with pytest.raises(HttpError, match="task has ended"):
                call("POST", f"{ego.url}/ego/explore", {"owner": "operator", "taskId": task.id})
        else:
            expected = "skill-deadline" if outcome == "protection" else "skill-finished"
            deadline = time.monotonic() + 8
            while len(_turns(robot)) < 2 and time.monotonic() < deadline:
                time.sleep(0.02)
            _wait_idle(chat, "robot")
            turns = _turns(robot)
            assert [turn["resumed"] for turn in turns] == [False, True]
            assert expected in turns[1]["text"] and job["id"] in turns[1]["text"]
            if outcome == "protection":
                assert link.motion_status()["reason"] == "protection"
                assert runtime.job(job["id"])["state"] == "stopped"
            else:
                assert runtime.job(job["id"])["state"] == "succeeded"
    finally:
        chat.stop()
        ego.stop()
        runtime.stop()
        host.stop()


def test_operator_preferences_have_an_auditable_lifetime_across_new_tasks(kitchen_root, tmp_path):
    robot = _home(tmp_path, "robot-1", "ego")
    ego = _robot_ego([])
    config = ChatConfig(dsh=_recording_dsh(tmp_path), cwd=tmp_path, env_file=tmp_path / ".env",
                        robot_home=robot, ego_api=ego.url)

    def start():
        return StewardHost(kitchen_root, scenario="phase0-kitchen", chat=config).start()

    def set_preference(host, value):
        return call("POST", f"{host.wca.url}/console/preferences", {"key": "默认落点", "value": value})

    try:
        host = start()
        try:
            original = set_preference(host, "蓝色托盘")
            assert original["source"] == "operator"
            with pytest.raises(HttpError) as denied:
                call("GET", f"{host.robot_servers['w:robot-1'].url}/console/preferences")
            assert denied.value.status == 404
            host.chat.assign("robot", "只观察工作区")
            _wait_idle(host.chat, "robot")
        finally:
            host.stop()

        host = start()
        try:
            assert call("GET", f"{host.wca.url}/console/preferences")["preferences"] == [original]
            updated = set_preference(host, "棕色托盘")
            host.chat.assign("robot", "收好物品")
            _wait_idle(host.chat, "robot")
            removed = set_preference(host, None)
            host.chat.assign("robot", "继续观察")
            _wait_idle(host.chat, "robot")
            assert call("GET", f"{host.wca.url}/console/preferences") == {"preferences": []}
            history = call("GET", f"{host.wca.url}/console/preferences?history=true")
            assert history["preferences"] == [removed, updated, original]
        finally:
            host.stop()
    finally:
        ego.stop()

    first, second, third = _turns(robot)
    assert all(not turn["resumed"] for turn in (first, second, third))
    assert "蓝色托盘" in first["text"]
    assert "棕色托盘" in second["text"] and "蓝色托盘" not in second["text"]
    assert '"source": "operator"' in second["text"] and "现场观测为先" in second["text"]
    assert "默认偏好" in third["text"] and "棕色托盘" not in third["text"]


def test_old_task_events_and_synchronous_completions_do_not_wake_a_new_turn(tmp_path):
    robot = _home(tmp_path, "robot-1", "ego")
    events = []
    polled = threading.Event()

    def packet(q, b):
        since = int(q.get("since", 0))
        batch = events.copy()
        if since == 2:
            polled.set()
        return {"boot": "sim", "last": len(batch), "events": [e for e in batch if e["id"] > since]}

    ego = JsonServer({("GET", "/ego/events"): packet,
                      ("POST", "/ego/control"): lambda q, b: {"owner": None},
                      ("POST", "/skill/stop"): lambda q, b: {"stopped": None}}).start()
    chat = HarnessChat(ChatConfig(dsh=_recording_dsh(tmp_path), cwd=tmp_path, env_file=tmp_path / ".env",
                                  robot_home=robot, ego_api=ego.url), lambda: "unused")
    try:
        chat.send("robot", "旧任务")
        _wait_idle(chat, "robot")
        old = chat.agents["robot"].task.id
        chat.assign("robot", "新任务")
        _wait_idle(chat, "robot")
        current = chat.agents["robot"].task.id
        events.extend([{"id": 1, "kind": "target-position-changed", "job": "job-old", "taskId": old},
                       {"id": 2, "kind": "skill-finished", "job": "job-sync", "taskId": current,
                        "state": "succeeded", "notifyCompletion": False}])
        assert polled.wait(3)
        assert not chat.agents["robot"].busy
        assert len(_turns(robot)) == 2
    finally:
        chat.stop()
        ego.stop()


def test_the_host_gives_the_scenario_task_to_the_robot_whenever_an_episode_loads_or_resets(kitchen_root, tmp_path):
    import threading

    from steward.net import call

    robot = _home(tmp_path, "robot-1", "ego")
    calls = []
    ego = _robot_ego(calls)

    def over_wca(host: StewardHost, body: dict) -> dict:
        out: dict = {}
        worker = threading.Thread(target=lambda: out.update(call("POST", f"{host.wca.url}/wca/episode", body)))
        worker.start()
        while worker.is_alive():
            host.pump(0.05)
        _wait_idle(host.chat, "robot")
        return out

    def host(dispatch: bool) -> StewardHost:
        config = ChatConfig(dsh=_recording_dsh(tmp_path), cwd=tmp_path, env_file=tmp_path / ".env",
                            robot_home=robot, ego_api=ego.url)
        return StewardHost(kitchen_root, scenario="phase0-kitchen", run_dir=tmp_path, chat=config,
                           dispatch_task=dispatch).start()

    try:
        quiet = host(False)
        try:
            over_wca(quiet, {"action": "load", "scenario": "phase0-kitchen"})
        finally:
            quiet.stop()
        assert not (robot / "turns").exists(), "without dispatch_task the robot waits for the operator"
        steward = host(True)
        try:
            task = steward.scenario["task"]
            over_wca(steward, {"action": "load", "scenario": "phase0-kitchen"})
            before = [m["text"] for m in steward.chat.state()["messages"]]
            over_wca(steward, {"action": "reset"})
            after = [m["text"] for m in steward.chat.state()["messages"]]
        finally:
            steward.stop()
    finally:
        ego.stop()
    turns = _turns(robot)
    assert [t["resumed"] for t in turns] == [False, False], "each episode starts a new conversation"
    assert all(t["text"].endswith(task) for t in turns)
    assert calls == ["acquire", "release"] * 2
    assert after.count(task) == 1 and len(after) < len(before) + 2, "a reset empties the transcript before the new task"


def test_a_turn_ended_by_the_budget_reports_a_designed_stop_not_a_failure(tmp_path):
    """The wall-clock budget ends long turns by design: ``_terminate`` kills the harness process, and
    on Windows that exit is non-zero with a stderr tail holding only the node startup warning, which
    used to be reported as a run failure and alarmed the operator. A budget timeout (like an operator
    cancel) must read as a designed end: no error entry, no failure alert."""
    script = _launcher(tmp_path, "time.sleep(30)\n")
    calls = []

    def control(q, b):
        calls.append(b["action"])
        return {"owner": None}

    ego = JsonServer({("POST", "/ego/control"): control, ("GET", "/ego/control"): lambda q, b: {"owner": None}}).start()
    try:
        chat = HarnessChat(ChatConfig(dsh=script, cwd=tmp_path, env_file=tmp_path / ".env", robot_home=_home(tmp_path, "robot-1", "ego"),
                                      ego_api=ego.url, timeout_s=0.4), lambda: "unused")
        chat.send("robot", "慢慢来")
        _wait_idle(chat, "robot")
    finally:
        ego.stop()
    roles = [m["role"] for m in chat.state()["messages"]]
    assert "error" not in roles
    assert any("回合预算" in m["text"] for m in chat.state()["messages"])


def test_shuttle_pauses_while_an_operator_controls_the_robot():
    from steward.mission import _wait_for_control

    owners = iter(["operator", "operator", None])
    api = JsonServer({("GET", "/ego/control"): lambda q, b: {"owner": next(owners)}}).start()
    waits = []
    try:
        _wait_for_control(api.url, waits.append, poll_s=0.01)
    finally:
        api.stop()
    assert waits == ["operator"]


def test_ego_runtime_control_blocks_other_clients_until_released(kitchen_root, tmp_path):
    host = StewardHost(kitchen_root, scenario="phase0-kitchen", run_dir=tmp_path)
    runtime = EgoRuntime(RobotLink(host.robot_servers["w:robot-1"].url), steward_bot().to_dict())
    try:
        runtime._poll_once()
        assert runtime.acquire_control("operator") == {"owner": "operator"}
        with pytest.raises(PermissionError, match="controlled by operator"):
            runtime.explore()
        with pytest.raises(PermissionError, match="controlled by operator"):
            runtime.run("rcpt-x")
        with pytest.raises(KeyError):
            runtime.run("rcpt-x", owner="operator")
        with pytest.raises(PermissionError, match="already controlled"):
            runtime.acquire_control("someone-else")
        runtime.release_control("someone-else")
        assert runtime.control() == {"owner": "operator"}
        runtime.release_control("operator")
        assert runtime.control() == {"owner": None}
        with pytest.raises(KeyError):
            runtime.run("rcpt-x")
    finally:
        runtime.executor.cancel()
        host.stop()
