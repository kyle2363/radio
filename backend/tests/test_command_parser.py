"""Music requests must reach the tool-calling parser intact, not a keyword shortcut."""
import pytest

from commands import parser
from llm import ChatResponse, ToolCall


@pytest.mark.parametrize("text", [
    "queue up more i'm geist songs",
    "queue up more I’m Geist songs",
    "play more Radiohead",
    "play more jazz",
    "queue 3 chill songs for studying",
    "play something upbeat",
    "play Blue by I'm Geist",
    "queue songs by Heart",
    "play sounds like midnight driving",
    "pause and queue some jazz",
    "don't skip this song",
])
def test_music_and_compound_requests_reach_llm_unchanged(monkeypatch, text):
    calls = []
    expected = {"actions": [{"action": "add_to_queue", "song_ids": [1, 2]}], "explanation": "Queued two songs"}
    context = {"mood_state": "neutral"}

    def llm(received, received_context):
        calls.append((received, received_context))
        return expected

    monkeypatch.setattr(parser, "_call_llm_with_tools", llm)
    assert parser.parse_command(text, context) == expected
    assert calls == [(text, context)]


@pytest.mark.parametrize("text, action", [
    ("pause", "pause"), ("skip", "skip"), ("play", "resume"),
    ("more energetic", "mood_shift"), ("chill", "mood_shift"),
    ("volume to 50", "set_volume"), ("sleep in 15 minutes", "sleep_timer"),
    ("vibe lock", "vibe_lock"), ("remove vibe lock", "vibe_unlock"),
    ("clear the queue", "clear_queue"),
])
def test_complete_simple_controls_keep_fast_path(monkeypatch, text, action):
    def unexpected(*args):
        pytest.fail("Simple control should not need an LLM")
    monkeypatch.setattr(parser, "_call_llm_with_tools", unexpected)
    result = parser.parse_command(text, {})
    assert result["action"] == action


def test_queue_intent_is_explicit_in_tool_prompt():
    prompt = parser._get_tool_system_prompt({})
    assert 'queue' in prompt.lower()
    assert 'without interrupting' in prompt.lower()


def test_original_artist_request_is_sent_to_model_then_search_results_become_queue_actions(monkeypatch):
    text = "queue up more i'm geist songs"
    searches = []
    def search(args):
        searches.append(args)
        return '[{"id": 1, "artist": "I\'m Geist", "title": "Example"}]'
    monkeypatch.setattr(parser, '_search_songs_tool', search)
    class Client:
        calls = 0
        def chat(self, **kwargs):
            messages = kwargs['messages']
            assert messages[1] == {'role': 'user', 'content': text}
            self.calls += 1
            if self.calls == 1:
                return ChatResponse(tool_calls=[ToolCall('search-1', 'search_songs', {'artist': "I'm Geist"})])
            if self.calls == 2:
                return ChatResponse(tool_calls=[ToolCall('queue-1', 'add_to_queue', {'song_ids': [1], 'position': 'back'})])
            return ChatResponse(content='Added one song to the queue.')
    client = Client()
    monkeypatch.setattr(parser, 'llm_chat', lambda: client)
    result = parser.parse_command(text, {})
    assert searches == [{'artist': "I'm Geist"}]
    assert result['actions'] == [{'action': 'add_to_queue', 'song_ids': [1], 'position': 'back'}]
    assert client.calls == 3
