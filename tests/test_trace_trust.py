"""可信 trace 库（使用者 2026-11-07）：run.json 记开关 / 提交号，context 记决策用的牌面，整段自检能不能接上。"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "audit"))
import trace_trust  # noqa: E402

from hiro.contracts.trace import run_config


def test_run_config_records_flags_without_secrets(tmp_path):
    env = {"VEIL_DRAW_LAST": "0", "HIRO_DRINK_INSITU": "1", "HIRO_OPENAI_API_KEY": "x", "PATH": "/bin"}
    cfg = run_config(env)
    assert cfg["env"] == {"HIRO_DRINK_INSITU": "1", "VEIL_DRAW_LAST": "0"}
    assert "commit" in cfg and "dirty" in cfg
    assert run_config({}, root=tmp_path)["commit"] is None  # 不是 git 仓库：不报错，记 None


def test_trace_writer_puts_config_in_run_json(tmp_path, monkeypatch):
    from hiro.contracts import TraceWriter

    monkeypatch.setenv("VEIL_CLEAR_FIRST", "1")
    w = TraceWriter(tmp_path, "r", {"mode": "main"})
    run = json.loads((w.dir / "run.json").read_text(encoding="utf-8"))
    assert run["mode"] == "main" and run["config"]["env"]["VEIL_CLEAR_FIRST"] == "1"


def test_trace_context_has_board(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_veil import REBOUND, card, case

    from hiro.produce.veil_exam import VeilScreen

    monkeypatch.setenv("VEIL_CLEAR_FIRST", "1")
    state, mem = case(card(0, REBOUND), card(1, "頂点へ+", once=True), deck=["眠気"], grave=["教えてあげる+"])
    mem.strategy_data.update(veil_initial={REBOUND: 1, "頂点へ+": 1, "教えてあげる+": 1, "第一印象+": 1},
                             veil_deck={REBOUND: 1, "頂点へ+": 1, "教えてあげる+": 1, "眠気": 1})
    mem.this_turn = ["打 第一印象+"]
    ctx = VeilScreen({}, set()).trace_context(state, mem)
    assert ctx["hand"][1] == [1, "頂点へ+", "頂点へ+", 1, True, True]
    assert ctx["piles"] == {"deck": {"眠気": 1}, "grave": {"教えてあげる+": 1}, "unlocated": {}}
    assert ctx["excluded"] == {"第一印象+": 1} and ctx["played"] == ["第一印象+"]
    assert ctx["flags"]["CLEAR_FIRST"] is True and ctx["flags"]["DRAW_LAST"] is True and ctx["undone"] == 0
    json.dumps(ctx, ensure_ascii=False)


CLEAN = {"commit": "abc", "dirty": False, "env": {}}


def _trace(tmp_path, contexts, config=CLEAN):
    d = tmp_path / "t"
    d.mkdir(exist_ok=True)
    (d / "run.json").write_text(json.dumps({"mode": "main", **({"config": config} if config else {})}))
    with (d / "steps.jsonl").open("w") as f:
        for i, ctx in enumerate(contexts, 1):
            f.write(json.dumps({"seq": i, "decision": {"decider": "veil"}, "notes": ["context " + json.dumps(ctx)]}) + "\n")
    return d


def _ctx(turn, hand, deck, grave=(), unlocated=None, **kw):
    piles = {"deck": dict.fromkeys(deck, 1), "grave": dict.fromkeys(grave, 1), "unlocated": unlocated or {}}
    ledger = dict.fromkeys([*hand, *deck, *grave, *(unlocated or {})], 1)
    return {"turn": turn, "uncertain": False, "hand": [[i, n, n, 1, True, False] for i, n in enumerate(hand)],
            "piles": piles, "deck": ledger, "undone": 0, **kw}


def test_trust_passes_clean_trace(tmp_path):
    d = _trace(tmp_path, [_ctx(1, ["A", "B"], ["C", "D"]), _ctx(1, ["B"], ["C", "D"], ["A"]),
                          _ctx(2, ["C", "D"], [], ["A", "B"])])
    r = trace_trust.check(d)
    assert r["trusted"] and r["steps"] == 3 and (d / "trust.json").exists()


def test_trust_reshuffle_and_generated_sleep_ok(tmp_path):
    # 牌堆只剩 C，新回合抽到 C + A（弃牌堆洗回）；上回合末生成的眠気被抽到
    t1 = _ctx(1, ["B"], ["C"], ["A"])
    t2 = _ctx(2, ["C", "A", "眠気"], [], ["B"])
    r = trace_trust.check(_trace(tmp_path, [t1, t2]))
    assert r["trusted"], r["problems"]


@pytest.mark.parametrize("bad, why", [
    ({"uncertain": True, "uncertain_why": {"why": "x"}}, "账本不可信"),
    ({"undone": 1}, "撤回"),
    ({"piles": None}, "没有牌堆账"),
])
def test_trust_flags_bad_steps(tmp_path, bad, why):
    r = trace_trust.check(_trace(tmp_path, [_ctx(1, ["A"], ["B"], **bad)]))
    assert not r["trusted"] and why in r["problems"][0]["why"]


def test_trust_conservation_and_draw_checks(tmp_path):
    lost = _ctx(1, ["A"], ["B"])
    lost["deck"]["Z"] = 1  # 账本里有、哪个区都没有
    r = trace_trust.check(_trace(tmp_path, [lost]))
    assert "牌不守恒" in r["problems"][0]["why"]
    t2 = _ctx(2, ["X"], ["B"])  # 上回合牌堆 / 弃牌堆都没有 X
    r = trace_trust.check(_trace(tmp_path, [_ctx(1, ["A"], ["B"]), t2]))
    assert not r["trusted"] and "上回合牌堆里没有" in r["problems"][0]["why"]
    # 洗牌规则：牌堆还剩 B 没抽到，却抽到了弃牌堆的 A → 不可信
    r = trace_trust.check(_trace(tmp_path, [_ctx(1, ["C"], ["B", "D"], ["A"]), _ctx(2, ["D", "A", "C"], ["B"])]))
    assert not r["trusted"] and "牌堆却没抽完" in r["problems"][0]["why"]
    r = trace_trust.check(_trace(tmp_path, [_ctx(1, ["A"], ["B"], unlocated={"C": 1})]))
    assert "位置不明" in r["problems"][0]["why"]


def test_trust_rejects_old_or_dirty_runs(tmp_path):
    ctx = [_ctx(1, ["A"], ["B"])]
    assert "没记开关" in trace_trust.check(_trace(tmp_path, ctx, config=None))["problems"][0]["why"]
    dirty = trace_trust.check(_trace(tmp_path, ctx, config={**CLEAN, "dirty": True}))
    assert "未提交" in dirty["problems"][0]["why"]
    old = _ctx(1, ["A"], ["B"])
    del old["hand"]
    assert "没有牌面" in trace_trust.check(_trace(tmp_path, [old]))["problems"][0]["why"]


def test_trust_last_play_of_turn_left_hand(tmp_path):
    # 2026-11-07 第 1 局 R1：T8 最后一步打出眠気（打出即除外），T9 从山札抽到另一张眠気、再洗弃牌堆抽 2 张。
    # context 是出牌前的局面，原来把打出的眠気当成还在手里，T9 的眠気被减掉，误判「牌堆却没抽完」
    t8 = _ctx(8, ["眠気"], ["眠気"], ["A", "B"])
    t8["piles"]["deck"] = {"眠気": 1}
    t8["deck"] = {"眠気": 2, "A": 1, "B": 1}
    t9 = _ctx(9, ["眠気", "A", "B"], [])
    t9["deck"] = {"眠気": 1, "A": 1, "B": 1}
    t9["excluded"] = {"眠気": 1}
    d = _trace(tmp_path, [t8, t9])
    lines = (d / "steps.jsonl").read_text().splitlines()
    first = json.loads(lines[0])
    first["execution"] = {"action": {"kind": "PlayCard", "args": {"slot": 0}}, "ok": True}
    (d / "steps.jsonl").write_text("\n".join([json.dumps(first), lines[1]]) + "\n")
    assert trace_trust.check(d)["trusted"]
    first["execution"]["ok"] = False  # 没打出去：眠気还在手里，山札的眠気确实没抽到
    (d / "steps.jsonl").write_text("\n".join([json.dumps(first), lines[1]]) + "\n")
    assert not trace_trust.check(d)["trusted"]
