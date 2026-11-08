"""The bounded Int32 resource-dependent lesson arithmetic from 3.4.1."""
import math
from .core import Unsupported,f32


def calculate(value,permil,consume_permil):
    for name,number,maximum in [('resource',value,2**31-1),('coefficient',permil,2**31-1),('consumption',consume_permil,1000)]:
        if type(number) is not int or not 0<=number<=maximum:
            raise Unsupported('Invalid resource score '+name)
    scale=f32(f32(permil)/f32(1000))
    scaled=f32(f32(scale*f32(value))+f32(-0.0001))
    retained=f32(f32(1-f32(f32(consume_permil)/f32(1000)))*f32(value))
    if not math.isfinite(scaled) or scaled>=2**31 or not math.isfinite(retained) or retained>=2**31:
        raise Unsupported('Resource-dependent Int32 result overflow not ported')
    return math.ceil(scaled),math.ceil(retained)
