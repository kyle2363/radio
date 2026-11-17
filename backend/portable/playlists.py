"""Reference playlist-sync rules shared with Android (checkpoint 7A).

Playlists travel as ordinary profile events. Name, deletion and each member are
last-writer-wins registers ordered by (Lamport clock, device id, event id).
Member order uses fractional position keys: lowercase base-36 strings compared
lexicographically, never ending in "0", so a key always fits between two others.
Ties between equal keys are broken by the member key.
"""
import re
import uuid

DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"
_KEY = re.compile(r"^[0-9a-z]{0,63}[1-9a-z]$")
NAME_LIMIT = 80


def valid_key(key):
    return isinstance(key, str) and bool(_KEY.match(key))


def valid_playlist_id(value):
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def valid_name(value):
    return (isinstance(value, str) and 1 <= len(value) <= NAME_LIMIT and value.strip() == value
            and not any(ord(c) < 32 for c in value))


def key_between(a, b):
    """Return a valid key strictly between ``a`` and ``b`` (either may be None)."""
    a = a or ""
    if (a and not valid_key(a)) or (b is not None and not valid_key(b)) or (b is not None and a >= b):
        raise ValueError("Invalid position bounds")
    result, i = "", 0
    while True:
        low = DIGITS.index(a[i]) if i < len(a) else 0
        high = DIGITS.index(b[i]) if b is not None and i < len(b) else len(DIGITS)
        if low == high:
            result += DIGITS[low]
        else:
            middle = (low + high) // 2
            if middle > low:
                return result + DIGITS[middle]
            result += DIGITS[low]
            b = None  # every continuation of this prefix already sorts before b
        i += 1


def ordered_members(members):
    """Sort present members as both platforms must: (position, member key)."""
    return sorted(members, key=lambda m: (m["position"], m["key"]))


def reposition(desired, current):
    """Assign position keys for ``desired`` member order, keeping ``current`` keys where possible.

    Returns {member_key: position}. Keys stay unchanged while they remain
    increasing, so a single move emits a single member change.
    """
    result, previous = {}, None
    for index, member in enumerate(desired):
        key = current.get(member)
        if key is not None and (previous is None or key > previous):
            result[member] = previous = key
            continue
        upper = next((current[m] for m in desired[index + 1:]
                      if m in current and (previous is None or current[m] > previous)), None)
        previous = result[member] = key_between(previous, upper)
    return result
