"""Pure five-pick slot policy shared by desktop and portable ranking."""


def allocate_ranked_slots(candidates, current_song=None, *, vibe_lock=False,
                          exploration_level=.3, archetype=None):
    ordered = sorted(candidates, key=lambda c: c.get('score', .5), reverse=True)
    result = {name: [] for name in ('exploit', 'explore', 'wildcard', 'context')}
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
    exploit = 5 - explore - wildcard - context
    take('exploit', exploit)
    cluster = current_song.get('cluster_id') if current_song else None
    for slot, count in (('explore', explore), ('wildcard', wildcard)):
        pool = sorted(ordered, key=lambda c: c.get('_' + slot + '_weight', c.get('score', .5)), reverse=True)
        take(slot, count, pool, lambda c: c.get('cluster_id') != cluster)
        # Fill short or same-cluster pools without enabling a disabled slot.
        take(slot, count, pool)
    if current_song:
        target = current_song.get('energy', .5)
        take('context', context, predicate=lambda c: abs(c.get('energy', .5) - target) < .2)
    take('context', context)
    return result
