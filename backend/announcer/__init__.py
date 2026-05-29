"""Announcer module for TTS, DJ announcements, talk segments, and personas."""

from announcer.tts import tts_say, get_tts_status, set_voice_mode, get_voice_mode
from announcer.announcements import (
    get_announcement,
    should_announce,
    play_announcement,
    AnnounceTrigger,
)
from announcer.voice_manager import (
    get_voice_manager,
    set_tts_engine,
    set_tts_voice,
)
from announcer.personas import PERSONAS, Persona, get_persona
from announcer.talk_segments import (
    TalkSegmentType,
    TalkSegment,
    generate_caller_segment,
    generate_dj_monologue,
    generate_guest_takeover_intro,
    get_next_talk_segment,
)

__all__ = [
    "tts_say",
    "get_tts_status",
    "set_voice_mode",
    "get_voice_mode",
    "get_announcement",
    "should_announce",
    "play_announcement",
    "AnnounceTrigger",
    "get_voice_manager",
    "set_tts_engine",
    "set_tts_voice",
    "PERSONAS",
    "Persona",
    "get_persona",
    "TalkSegmentType",
    "TalkSegment",
    "generate_caller_segment",
    "generate_dj_monologue",
    "generate_guest_takeover_intro",
    "get_next_talk_segment",
]
