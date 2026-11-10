"""Audit the full OpenPI LIBERO run and write comparison tables."""
import argparse
import collections
import json
import math
from pathlib import Path

PUBLISHED = {"libero_spatial": 98.8, "libero_object": 98.2, "libero_goal": 98.0, "libero_10": 92.4}
BUDGETS = dict(zip(PUBLISHED, [220, 280, 300, 520]))


def summarize(run):
    assert (run / "completed-suites.txt").read_text().splitlines() == list(PUBLISHED)
    assert (run / "finished.txt").stat().st_size
    videos = Path((run / "video-root.txt").read_text().strip())
    suites = {}
    for suite, published_rate in PUBLISHED.items():
        rows = [json.loads(line) for line in (run / f"{suite}.jsonl").read_text().splitlines()]
        expected = [(task, initial) for task in range(10) for initial in range(50)]
        assert [(r["task_id"], r["initial_state_index"]) for r in rows] == expected, suite
        assert set(p.name for p in (videos / suite).glob("*.mp4")) == {r["video"] for r in rows}, suite
        results = collections.defaultdict(list)
        for row in rows:
            assert row["suite"] == suite and row["seed"] == 7
            assert 0 < row["policy_steps"] <= BUDGETS[suite]
            assert math.isfinite(row["wall_seconds"]) and row["wall_seconds"] > 0
            assert len(row["initial_state_sha256"]) == 64
            assert type(row["official_done"]) is bool and type(row["libero_success"]) is bool
            video = videos / suite / row["video"]
            with video.open("rb") as stream:
                assert stream.read(8)[4:] == b"ftyp", video
            results[row["task_id"]].append(row)
        success = sum(r["official_done"] for r in rows)
        rate = 100 * success / len(rows)
        log = (run / f"{suite}.client.log").read_text()
        assert "Total episodes: 500" in log
        assert f"Total success rate: {success / len(rows)}" in log
        suites[suite] = {
            "successes": success, "episodes": len(rows), "success_percent": rate,
            "official_reference_percent": published_rate, "difference_percentage_points": rate - published_rate,
            "done_success_disagreements": sum(r["official_done"] != r["libero_success"] for r in rows),
            "mujoco_numeric_warning_lines": sum("Nan, Inf or huge value" in line for line in log.splitlines()),
            "tasks": [
                {"task_id": task, "task_name": trials[0]["task_name"], "language": trials[0]["language"],
                 "successes": sum(r["official_done"] for r in trials), "episodes": len(trials)}
                for task, trials in sorted(results.items())
            ],
        }
    total_successes = sum(r["successes"] for r in suites.values())
    total_episodes = sum(r["episodes"] for r in suites.values())
    report = {
        "run_id": run.name, "configuration": "pi05_libero", "seed": 7, "task_order_index": 0,
        "started_utc": (run / "started.txt").read_text().strip(),
        "finished_utc": (run / "finished.txt").read_text().strip(),
        "video_root": str(videos), "successes": total_successes, "episodes": total_episodes,
        "success_percent": 100 * total_successes / total_episodes, "suites": suites,
    }
    (run / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    lines = [
        "# OpenPI π0.5 LIBERO 全量基线", "",
        f"运行 ID 为 `{run.name}`。使用官方 `pi05_libero`，每套件 10 项任务，每项 50 个官方初态，种子为 7。",
        "每套件独立启动策略服务。成功率沿用官方运行器的终止信号，并记录 LIBERO 独立成功信号用于交叉核对。", "",
        "| 套件 | 实测成功数 | 实测成功率 | 官方参考 | 差值（百分点） |", "| --- | ---: | ---: | ---: | ---: |",
    ]
    for suite, row in suites.items():
        lines.append(f"| {suite} | {row['successes']}/{row['episodes']} | {row['success_percent']:.2f}% | {row['official_reference_percent']:.2f}% | {row['difference_percentage_points']:+.2f} |")
    lines.extend([
        f"| 总计 | {total_successes}/{total_episodes} | {report['success_percent']:.2f}% | 96.85% | {report['success_percent'] - 96.85:+.2f} |",
        "", f"录像目录为 `{videos}`。逐回合结果保存在本目录各套件 JSONL 文件中，`video` 字段给出对应文件名。",
        "逐任务结果与成功信号差异见 [summary.json](summary.json)。", "",
    ])
    lines.extend(["## 完整性与仿真警告", "",
                  "| 套件 | 终止信号与独立成功信号不一致 | MuJoCo 数值警告行数 |",
                  "| --- | ---: | ---: |"])
    for suite, row in suites.items():
        lines.append(f"| {suite} | {row['done_success_disagreements']} | {row['mujoco_numeric_warning_lines']} |")
    lines.append("")
    (run / "README.md").write_text("\n".join(lines))
    print(f"Verified {total_episodes} episodes and videos; {total_successes} successful")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    summarize(parser.parse_args().run)
