"""hiro.sim.screen_source：不读存档，从画面局面 + 培育记账的牌组合成开局、按执行的动作跟踪、出预演。
「画面」用模拟器自己打的一局（SimGame.state()），牌组用同一份合成开局的牌组。"""

import copy

import pytest

from hiro.brain.state import CAMPUS_HIRO

pytestmark = pytest.mark.sim_master


class _Session:
    def __init__(self, deck):
        self.state = type("S", (), {"produce": {"deck": [{"card_id": c, "upgrade": u} for c, u in deck]}})()
        self.notes = []

    def note(self, t):
        self.notes.append(t)


def _truth():
    from hiro.brain.battle import BattleBrain
    from hiro.sim import interval as iv
    from hiro.sim import resource as rv
    from hiro.sim.env import SimGame

    deck = list(rv.REFERENCE_DECK)
    setup, _ = rv.setup_for("r1", deck=deck, drinks=[], hp=30, max_hp=33, permil=None)
    game = SimGame(iv._env(setup, seed=7), "r1", CAMPUS_HIRO)
    game.memory = BattleBrain(CAMPUS_HIRO, None).new_memory("r1")
    return deck, game


def test_tracks_and_previews_along_a_sim_game():
    from hiro.contracts import Action
    from hiro.sim.screen_source import SimScreenExamStateSource

    deck, truth = _truth()
    src = SimScreenExamStateSource()
    src.begin({"exam": "r1", "profile": CAMPUS_HIRO, "session": _Session(deck)})
    mem = copy.deepcopy(truth.memory)
    with_preview = 0
    for _ in range(12):
        if truth.ended:
            break
        state = truth.state()  # 「画面」读数
        src.game = None
        src.screen_drinks = [d.drink_id for d in state.drinks]
        src._sync(state, mem)
        assert src.game is not None, src.lost
        pv = src.previews(state)
        if pv:
            with_preview += 1
            legal = {a for a in state.legal_actions()}
            assert all(p.action.legal_key() in legal for p in pv)
        legal = state.legal_actions()
        act = next((a for a in legal if a.startswith("play:")), "skip")
        slot = int(act.split(":")[1]) if act != "skip" else None
        truth.step(dict(kind="play", id=truth._hand[slot]) if slot is not None else dict(kind="end_turn"))
        src.executed(Action.from_legal(act), None)
    assert with_preview >= 8
    assert src.stats["lost"] == 0


def test_rounds_switch(monkeypatch):
    from hiro.sim import screen_source as ss

    monkeypatch.setattr(ss, "ROUNDS", ("r2",))
    src = ss.SimScreenExamStateSource()
    src.begin({"exam": "r1", "profile": CAMPUS_HIRO})
    assert src.label is None  # R1 不给预演
    src.begin({"exam": "r2", "profile": CAMPUS_HIRO})
    assert src.label == "r2"
