"""主循环层（lead 维护）：observe → reduce → decide → act → verify。

- transitions.py：页面转移图、瞬态页面、mode 推断；
- reduce.py：reduce(state, obs) 纯函数（防抖、来源优先级、字段归属）；
- replay.py：离线重放 Observation 序列，校验确定性与 page 不抖；
- run.py（M-C）：实机主循环与 trace；recovery.py（M-C）：五类故障恢复。

reduce 本应在 hiro.state（决策会话目录）；该目录尚未提交，先放这里，提交后由 hiro.state.reduce 转发。
"""
