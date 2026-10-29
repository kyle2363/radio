"""Playback-library-neutral connected/offline media-plan contract, version 1.

This is a contract/reference resolver, not a second desktop playback engine.
Speech production remains in the existing server announcer/media services.
"""
import hashlib
import math
import re


def make_media_plan(context_id, local_song_id, *, speech=None):
    if not isinstance(context_id, str) or not context_id or not isinstance(local_song_id, str) or not local_song_id:
        raise ValueError("Playback context and local song identity are required")
    segments = []
    if speech:
        required = {"resource_id", "sha256", "byte_length", "expires_at", "context_id"}
        if not required <= set(speech) or speech["context_id"] != context_id:
            raise ValueError("Speech must belong to this playback context")
        if not re.fullmatch(r"[0-9a-f]{64}", speech["sha256"]) or not isinstance(speech["resource_id"], str):
            raise ValueError("Invalid speech identity or checksum")
        if type(speech["byte_length"]) is not int or not 0 < speech["byte_length"] <= 64 * 1024 * 1024:
            raise ValueError("Invalid speech length")
        expires = speech["expires_at"]
        if type(expires) not in (int, float) or not math.isfinite(expires):
            raise ValueError("Invalid speech expiration")
        # A future client resolves the resource id using its authorized media
        # adapter. The plan contains no private URL grants, paths or credentials.
        segments.append({"type": "speech", "optional": True, "on_unavailable": "skip",
                         **{key: speech[key] for key in required}})
    segments.append({"type": "local_song", "local_id": local_song_id})
    return {"schema": "radio.portable.media_plan", "version": 1,
            "context_id": context_id, "segments": segments}


def resolve_media_plan(plan, *, now, online, cached_bytes=None, context_id=None):
    if plan.get("schema") != "radio.portable.media_plan" or plan.get("version") != 1:
        raise ValueError("Unsupported media plan")
    context_id = context_id or plan["context_id"]
    if context_id != plan["context_id"]:
        raise ValueError("Obsolete playback context")
    cache = cached_bytes or {}
    result = []
    for segment in plan["segments"]:
        if segment["type"] == "local_song":
            result.append(dict(segment))
            continue
        if segment.get("type") != "speech" or segment.get("context_id") != context_id or segment.get("expires_at", 0) <= now:
            continue
        data = cache.get(segment["resource_id"])
        complete = isinstance(data, bytes) and len(data) == segment["byte_length"] and hashlib.sha256(data).hexdigest() == segment["sha256"]
        if complete:
            result.append({**segment, "source": "verified_cache"})
        elif online:
            result.append({**segment, "source": "authorized_remote", "failure_action": "skip"})
    return result
