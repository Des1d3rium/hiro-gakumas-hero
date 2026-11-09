"""自动模式离线回放：按停点理由序列走一遍 next_step / begin / finish，不连设备、不写文件。

python tools/replay_batch_driver.py --cases examples/cases/batch-driver.json
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from batch_driver import begin, finish, next_step
from batch_rules import DEFAULTS
from hiro.strategies.profiles import code_defaults


def replay_cases(path):
    results = []
    for case in json.loads(Path(path).read_text(encoding="utf-8")):
        state = copy.deepcopy(case["state"])
        rules = copy.deepcopy(DEFAULTS)
        rules["interval"]["customize"] = code_defaults()["customize"]  # 回忆档案代码缺省（不读本机档案，回放可重复）
        rules["driver"]["enabled"] = True
        for key, value in (case.get("rules") or {}).items():
            rules[key].update(value)
        commands = []
        for step in case["steps"]:
            action = next_step(state, step["hold"], rules)
            got = action["command"] if action else "agent"
            if got != step["expected"]:
                raise AssertionError((case["name"], step, action))
            commands.append(got)
            if action is not None and "result" in step:
                begin(state, action, lambda: None)
                finish(state, action, step["result"], lambda: None)
        results.append({"name": case["name"], "commands": commands, "ok": True})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({"cases": replay_cases(args.cases)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
