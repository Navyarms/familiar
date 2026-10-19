"""The user's name: in the system prompt as written, respelled for TTS only."""

from familiar_brain.agent.turn import USER_NAME_NOTE, build_system_prompt
from familiar_brain.config import Paths, Settings
from familiar_brain.persona.loader import load_persona
from familiar_brain.tts.kokoro import respell


def test_respell_whole_words_case_insensitive():
    p = {"Dyllon": "Dillon"}
    assert respell("Hi Dyllon, how are you?", p) == "Hi Dillon, how are you?"
    assert respell("dyllon's turn", p) == "Dillon's turn"
    assert respell("Dyllonville", p) == "Dyllonville"  # not a whole word
    assert respell("No name here", p) == "No name here"
    assert respell("Hi Dyllon", {"": "x", "Dyllon": ""}) == "Hi Dyllon"


def test_system_prompt_mentions_name_only_when_set(tmp_path):
    persona = load_persona(Paths(tmp_path).ensure(), "wisp")
    plain = build_system_prompt(persona)
    named = build_system_prompt(persona, user_name="Dyllon")
    assert USER_NAME_NOTE.format(name="Dyllon") in named
    assert "Dyllon" not in plain
    assert build_system_prompt(persona, user_name="  ") == plain


def test_settings_round_trip(tmp_path):
    s = Settings()
    s.update({"user_name": "Dyllon", "user_name_spoken": "Dillon"})
    s.save(tmp_path / "settings.json")
    back = Settings.load(tmp_path / "settings.json")
    assert (back.user_name, back.user_name_spoken) == ("Dyllon", "Dillon")
