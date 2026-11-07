"""Pure slot policy shared by desktop and portable ranking.

The default set is five picks. Connected phone requests may ask for 5–8 picks
and a fixed number of ``exploit`` picks; ``slot_counts`` turns that into a
per-slot budget. Vibe lock always keeps 4 ``exploit`` + 1 ``explore``.
"""

SLOT_NAMES = ('exploit', 'explore', 'wildcard', 'context')
MIN_TOTAL, MAX_TOTAL, DEFAULT_TOTAL = 5, 8, 5
VIBE_COUNTS = {'exploit': 4, 'explore': 1, 'wildcard': 0, 'context': 0}


def _default_counts(exploration_level, archetype):
    explore = 1 + int(exploration_level > .6)
    wildcard = int(exploration_level > .4)
    context = 1
    if archetype == 'explorer':
        explore, wildcard = 2, 1
    elif archetype == 'comfort_listener':
        explore, wildcard = 0, 0
    elif archetype == 'deep_listener':
        context = 2
        explore = min(explore, 1)
    return {'exploit': 5 - explore - wildcard - context, 'explore': explore,
            'wildcard': wildcard, 'context': context}


def slot_counts(total=DEFAULT_TOTAL, exploit=None, *, vibe_lock=False, exploration_level=.3, archetype=None):
    """Per-slot budget for ``total`` picks with an optional fixed ``exploit`` count.

    With the defaults this is exactly the historical five-pick policy. Extra or
    fewer option picks are spread round-robin over the option slots the policy
    enables (explore, wildcard, context), so a disabled slot stays disabled.
    """
    if isinstance(total, bool) or not isinstance(total, int) or not MIN_TOTAL <= total <= MAX_TOTAL:
        raise ValueError("total must be 5-8")
    if exploit is not None and (isinstance(exploit, bool) or not isinstance(exploit, int) or not 1 <= exploit <= 4):
        raise ValueError("exploit must be 1-4")
    if vibe_lock:
        return dict(VIBE_COUNTS)
    counts = _default_counts(exploration_level, archetype)
    if exploit is None:
        exploit = counts['exploit']
    remaining = total - exploit
    options = ('explore', 'wildcard', 'context')
    if remaining == sum(counts[s] for s in options):
        counts['exploit'] = exploit
        return counts
    enabled = [s for s in options if counts[s] > 0] or ['context']
    result = {'exploit': exploit, 'explore': 0, 'wildcard': 0, 'context': 0}
    for i in range(remaining):
        result[enabled[i % len(enabled)]] += 1
    return result


def allocate_ranked_slots(candidates, current_song=None, *, vibe_lock=False,
                          exploration_level=.3, archetype=None, counts=None):
    ordered = sorted(candidates, key=lambda c: c.get('score', .5), reverse=True)
    result = {name: [] for name in SLOT_NAMES}
    used = set()
    identity = lambda c: c.get('song_id') if c.get('song_id') is not None else c.get('id')

    def take(slot, count, pool=ordered, predicate=lambda c: True):
        for candidate in pool:
            if len(result[slot]) >= count:
                break
            sid = identity(candidate)
            if sid not in used and predicate(candidate):
                result[slot].append(candidate)
                used.add(sid)

    if vibe_lock:
        take('exploit', 4)
        take('explore', 1)
        return result

    if counts is None:
        counts = slot_counts(exploration_level=exploration_level, archetype=archetype)
    take('exploit', counts['exploit'])
    cluster = current_song.get('cluster_id') if current_song else None
    for slot in ('explore', 'wildcard'):
        count = counts[slot]
        pool = sorted(ordered, key=lambda c: c.get('_' + slot + '_weight', c.get('score', .5)), reverse=True)
        take(slot, count, pool, lambda c: c.get('cluster_id') != cluster)
        # Fill short or same-cluster pools without enabling a disabled slot.
        take(slot, count, pool)
    context = counts['context']
    if current_song:
        target = current_song.get('energy', .5)
        take('context', context, predicate=lambda c: abs(c.get('energy', .5) - target) < .2)
    take('context', context)
    return result
