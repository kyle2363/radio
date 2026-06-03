"""TTS engine with dynamic engine switching support.

This module is a thin compatibility layer over VoiceManager.
tts_say() delegates to the active engine (default: OmniVoice with Piper fallback).
Engines can be swapped at runtime via set_tts_engine().

Usage:
    # Backward-compatible
    wav = tts_say("Hello world")
    
    # With voice selection
    wav = tts_say("Hello", voice_id="ryan")
    
    # With context-based voice selection
    wav = tts_say("Great track!", trigger_type="mood_change")
    
    # Switch engine
    set_tts_engine("piper", voice_id="amy")
    set_tts_engine("omnivoice", voice_id="The Trusted Morning Host")
    
    # Switch voice mode
    set_voice_mode("random")
    
    # Check status
    status = get_tts_status()
"""

import logging
import os

import config
from announcer.voice_manager import (
    get_voice_manager,
    set_tts_engine,
    set_tts_voice,
    VOICE_MODE_USER_SELECT,
    VOICE_MODE_RANDOM,
    VOICE_MODE_CONTEXT,
)

logger = logging.getLogger(__name__)

TTS_CACHE_DIR = config.TTS_CACHE_DIR


def tts_say(text: str, voice_id: str | None = None, output_dir: str | None = None, song_id: int | None = None, trigger_type: str | None = None, speed: float | None = None) -> str | None:
    """Synthesize text to WAV file using the active TTS engine.
    
    This is the main public API. It delegates to whatever engine is 
    currently active (default: OmniVoice with Piper fallback).
    
    Args:
        text: Text to synthesize
        voice_id: Voice to use (e.g. "amy", "The Trusted Morning Host", "piper:kusal").
                  Defaults to the active voice on the active engine.
        output_dir: Directory to save WAV file (defaults to tts_cache)
        song_id: Optional song ID to include in the WAV filename for cache tracking
        trigger_type: Announcement trigger type for context voice mode
                     (e.g. "mood_change", "skip_streak", "recovery", "wildcard")
        speed: Speech speed baseline (0.75-1.05). Only used by OmniVoice.
               Randomized ±0.1 at synth time for natural variety.
        
    Returns:
        Path to WAV file, or None on failure
    """
    logger.debug(f"tts_say: text_len={len(text)}, voice={voice_id}, trigger={trigger_type}, speed={speed}")
    vm = get_voice_manager()
    result = vm.synthesize(text, voice_id=voice_id, output_dir=output_dir or TTS_CACHE_DIR, song_id=song_id, trigger_type=trigger_type, speed=speed)
    logger.debug(f"tts_say: returned {'path' if result else 'None'}")
    return result


def set_voice_mode(mode: str):
    """Set voice selection mode.
    
    Modes:
        "user_select" — use the user's chosen voice for all announcements
        "random" — pick a random voice per synthesis call
        "context" — map trigger context to voice
    """
    vm = get_voice_manager()
    vm.set_voice_mode(mode)


def get_voice_mode() -> str:
    """Get current voice selection mode."""
    vm = get_voice_manager()
    return vm.get_voice_mode()


def get_tts_status():
    """Check TTS engine status."""
    vm = get_voice_manager()
    return vm.get_status()
