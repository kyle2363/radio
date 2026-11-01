"""Deterministic query construction; starting a song is not a taste signal."""
import math


def unit(vector):
    if vector is None:
        return None
    norm = math.sqrt(sum(x*x for x in vector))
    return [float(x / norm) for x in vector] if norm > 1e-12 else None


def context_query(profile, anchor, exploration):
    profile, anchor = unit(profile), unit(anchor)
    if profile is None:
        return anchor
    if anchor is None or len(anchor) != len(profile):
        return profile
    weight = .75 - .4 * max(0., min(1., exploration))
    return unit([(1-weight)*p + weight*a for p, a in zip(profile, anchor)])
