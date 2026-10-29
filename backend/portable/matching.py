"""Conservative recording matching, independent of filesystem names and services."""

import hashlib
from pathlib import Path
import re
import unicodedata
import wave

VERSION = 1


def normalize(value):
    """Unicode/case/punctuation normalization; recording-version words are retained."""
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", str(value or "")).casefold()))


def inspect_local_file(path, local_id, **metadata):
    """Read-only disposable/reference file adapter; hashes BYTES, never the path.

    Future Android scanners supply equivalent metadata/URI identities themselves.
    This helper does not decode audio or generate acoustic fingerprints.
    """
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if Path(path).suffix.casefold() == ".wav" and "duration" not in metadata:
        try:
            with wave.open(str(path), "rb") as audio:
                metadata["duration"] = audio.getnframes() / audio.getframerate()
        except (wave.Error, EOFError):
            pass
    return {**metadata, "local_id": str(local_id), "filename": Path(path).name,
            "content_sha256": digest.hexdigest(), "playable": True}


def _versions(row):
    text = normalize(str(row.get("title") or "") + " " + str(row.get("album") or ""))
    # Distinguish named mixes, live/studio, edits, and numbered remasters.
    markers = set(re.findall(r"\b(?:live|remaster\w*|remix\w*|acoustic|instrumental|karaoke|"
                             r"cover|edit|extended|demo|mono|stereo|radio|studio)\b", text))
    if markers:
        markers.update(re.findall(r"\b(?:19|20)\d{2}\b", text))
    return markers


def _duration(a, b):
    try:
        left, right = float(a.get("duration") or 0), float(b.get("duration") or 0)
        return abs(left - right) if left > 0 and right > 0 else None
    except (ValueError, TypeError):
        return None


def _recording_token(value, field):
    token = str(value or "").strip().casefold()
    if field == "isrc":
        token = re.sub(r"[-\s]", "", token)
        return token if re.fullmatch(r"[a-z]{2}[a-z0-9]{3}\d{7}", token) else None
    # Bare database integers are not recording identities. Require an explicit
    # namespace or the standard MusicBrainz recording UUID form.
    return token if ":" in token or re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", token) else None


def _evidence(local, song):
    reasons, confidence = [], 0.0
    duration = _duration(local, song)
    version_mismatch = _versions(local) != _versions(song)
    content = local.get("content_sha256")
    if isinstance(content, str) and re.fullmatch(r"[a-fA-F0-9]{64}", content) and \
            content.lower() == str(song.get("content_sha256") or "").lower():
        return 1.0, ["exact_content_sha256"], True
    # Fingerprints must include their algorithm/version/sample duration, not bare strings.
    fp, other_fp = local.get("fingerprint"), song.get("fingerprint")
    if isinstance(fp, dict) and isinstance(other_fp, dict) and fp.get("algorithm") and \
            fp.get("version") and fp.get("value") and fp == other_fp:
        reasons.append("exact_fingerprint")
        confidence = 0.98 if duration is not None and duration <= 2 else 0.84
    for field in ("recording_id", "isrc"):
        identity = _recording_token(local.get(field), field)
        if identity and identity == _recording_token(song.get(field), field):
            reasons.append(f"same_{field}")
            confidence = max(confidence, 0.97 if duration is not None and duration <= 2 else 0.86)
    artist = normalize(local.get("artist"))
    title = normalize(local.get("title"))
    artist_equal = bool(artist) and artist == normalize(song.get("artist"))
    title_equal = bool(title) and title == normalize(song.get("title"))
    if artist_equal and title_equal:
        reasons.extend(["same_artist", "same_title"])
        album_equal = bool(normalize(local.get("album"))) and \
            normalize(local.get("album")) == normalize(song.get("album"))
        if album_equal:
            reasons.append("same_album")
        if duration is not None and duration <= 2:
            reasons.append("duration_within_2s")
        confidence = max(confidence, 0.94 if album_equal and duration is not None and duration <= 2 else 0.79)
    elif title_equal:
        reasons.append("same_title_only")
        confidence = max(confidence, 0.45)
    if local.get("filename"):
        stem = normalize(Path(local["filename"]).stem)
        full = normalize(f"{song.get('artist') or ''} {song.get('title') or ''}")
        if full and stem == full:
            reasons.append("filename_artist_title")
            confidence = max(confidence, 0.65)
    if not reasons:
        return 0.0, [], False
    if duration is not None and duration > max(5, float(song.get("duration") or 0) * 0.03):
        reasons.append("duration_conflict")
        confidence = min(confidence, 0.6)
    if version_mismatch:
        reasons.append("recording_version_conflict")
        confidence = min(confidence, 0.6)
    return confidence, reasons, confidence >= 0.94


def match_catalog(catalog_songs, local_tracks, decisions=()):
    """Return radio.portable.matches v1, one result per local_id.

    decisions: {local_id, song_id, action:'confirm'|'reject', content_sha256?}.
    Conflicting confirmations are ambiguous, never resolved by arrival order.
    Persist decisions separately; file replacement invalidates an anchored decision.
    Duplicate best candidates always require review. No audio is moved or written.
    """
    songs = list(catalog_songs.values()) if isinstance(catalog_songs, dict) else list(catalog_songs)
    by_id = {str(song["id"]): song for song in songs}
    results, seen = [], set()
    decisions = list(decisions)
    for local in local_tracks:
        lid = str(local["local_id"])
        if lid in seen:
            raise ValueError("Duplicate local_id")
        seen.add(lid)
        relevant = [d for d in decisions if str(d["local_id"]) == lid and
                    (not d.get("content_sha256") or d["content_sha256"] == local.get("content_sha256"))]
        rejected = {str(d["song_id"]) for d in relevant if d["action"] == "reject"}
        all_confirmed = {str(d["song_id"]) for d in relevant if d["action"] == "confirm"}
        confirmed = all_confirmed & set(by_id)
        result = {"local_id": lid, "song_id": None, "status": "unmatched", "candidates": []}
        if len(confirmed) == 1 and not confirmed & rejected:
            result.update(song_id=next(iter(confirmed)), status="confirmed")
            result["candidates"] = [{"song_id": result["song_id"], "confidence": 1.0,
                                      "reasons": ["persistent_user_confirmation"]}]
        elif all_confirmed - set(by_id):
            result["warning"] = "confirmed_catalog_record_missing"
        elif confirmed:
            result.update(status="ambiguous", warning="conflicting_user_decisions")
        else:
            candidates = []
            for sid, song in by_id.items():
                if sid in rejected:
                    continue
                score, reasons, automatic = _evidence(local, song)
                if score:
                    candidates.append({"song_id": sid, "confidence": score,
                                       "reasons": reasons, "automatic": automatic})
            candidates.sort(key=lambda c: (-c["confidence"], c["song_id"]))
            result["candidates"] = candidates[:10]
            if candidates:
                best = candidates[0]
                unique = len(candidates) == 1 or best["confidence"] - candidates[1]["confidence"] >= 0.05
                if best["automatic"] and unique:
                    result.update(status="high_confidence", song_id=best["song_id"])
                else:
                    result["status"] = "ambiguous"
        results.append(result)
    return {"schema": "radio.portable.matches", "version": VERSION,
            "results": results, "counts": {status: sum(r["status"] == status for r in results)
                                           for status in ("confirmed", "high_confidence", "ambiguous", "unmatched")}}
