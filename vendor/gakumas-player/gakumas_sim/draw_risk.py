"""Two-pile automatic draw probability under an EXPLICIT uniform-order prior.

This does not estimate the game's seed distribution or reconstruct native
conditional posteriors. Current deck is consumed before the returned/grave
pile; cards drawn during this batch cannot enter that reshuffle.
"""
from fractions import Fraction
from math import comb
from .core import Unsupported


def draw_probability(*,deck_count,deck_targets,grave_count,grave_targets,
                     returned_count,returned_targets,draw_count,prior):
    if prior!='uniform-pile-order':raise Unsupported('Explicit uniform pile prior required; native seed posterior unknown')
    values=locals()
    for key in ['deck_count','deck_targets','grave_count','grave_targets','returned_count','returned_targets','draw_count']:
        if type(values[key]) is not int or not 0<=values[key]<=1000:raise Unsupported('Bounded integer '+key+' required')
    if deck_targets>deck_count or grave_targets>grave_count or returned_targets>returned_count:
        raise Unsupported('Targets exceed known pile sizes')
    first=min(draw_count,deck_count)
    no_first=Fraction(comb(deck_count-deck_targets,first),comb(deck_count,first))
    second=min(max(0,draw_count-first),grave_count+returned_count)
    n=grave_count+returned_count;k=grave_targets+returned_targets
    no_second=Fraction(comb(n-k,second),comb(n,second))
    probability=1-no_first*no_second
    return dict(probability=float(probability),fraction=dict(numerator=probability.numerator,denominator=probability.denominator),
        current_deck_draws=first,reshuffled_grave_draws=second,reshuffle_size=n if second else None,
        scope='Conditional uniform pile order; not a native redraw or win probability. No future card plays/removals modeled.')


def break_even_probability(commit,miss,hit):
    for value in [commit,miss,hit]:
        if type(value) is not int or value<0:raise Unsupported('Nonnegative integer branch scores required')
    if hit<=miss:raise Unsupported('Hit branch must score more than miss branch')
    threshold=Fraction(commit-miss,hit-miss)
    return dict(threshold=float(threshold),fraction=dict(numerator=threshold.numerator,denominator=threshold.denominator),
        criterion='Retain has strictly greater expected raw score only when hit probability exceeds threshold',
        scope='Two supplied terminal-score branches, not a full strategy, calibrated probability or resource objective')
