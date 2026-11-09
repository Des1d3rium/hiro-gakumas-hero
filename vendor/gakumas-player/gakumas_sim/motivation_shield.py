"""Bounded 3.4.1 CalculateAddBlock arithmetic with an extra motivation ratio.

Caller supplies the already-read motivation. Block restriction/down states are
outside this neutral helper and remain outside admitted simulator snapshots.
"""
import math
from .core import Unsupported,f32


def calculate(base,motivation,extra_permil):
    for name,value,minimum,maximum in [('base',base,1,2**31-1),
            ('motivation',motivation,0,2**31-1),('extra permil',extra_permil,0,1000000)]:
        if type(value) is not int or not minimum<=value<=maximum:
            raise Unsupported('Invalid scaled motivation shield '+name)
    scale=f32(f32(f32(extra_permil)/f32(1000))+f32(1))
    scaled=f32(f32(motivation)*scale)
    if not math.isfinite(scaled) or scaled>=2**31:
        raise Unsupported('Scaled motivation shield Int32 overflow not ported')
    result=base+math.ceil(scaled)
    if result>=2**31:raise Unsupported('Scaled motivation shield Int32 sum overflow not ported')
    return result
