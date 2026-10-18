from familiar_brain.agent.textflow import SentenceChunker, TagStripper, speakable


def feed_all(stripper, tokens):
    out = "".join(stripper.feed(t) for t in tokens) + stripper.flush()
    return out, stripper.tags


def test_tag_split_across_tokens():
    s = TagStripper({"happy", "sad"})
    out, tags = feed_all(s, ["[ha", "ppy] Done", ", Spotify", " is open."])
    assert out == " Done, Spotify is open."
    assert tags == [(0, "happy")]


def test_unknown_bracket_text_is_kept():
    s = TagStripper({"happy"})
    out, tags = feed_all(s, ["See [1] and [citation needed]."])
    assert out == "See [1] and [citation needed]."
    assert tags == []


def test_lone_bracket_passes_through():
    s = TagStripper({"happy"})
    out, _ = feed_all(s, ["array[", "0] = 1"])
    assert out == "array[0] = 1"


def test_first_sentence_cut_at_clause():
    c = SentenceChunker()
    out = c.feed("Sure thing, I can help with that, let me open your downloads folder and look for the file.")
    assert out[0].startswith("Sure thing, I can help with that,")
    assert c.emitted == 1


def test_sentences_and_flush():
    c = SentenceChunker()
    out = c.feed("Hello there. How are you today? I am")
    assert out == ["Hello there.", "How are you today?"]
    assert c.flush() == ["I am"]


def test_decimals_and_abbrev_do_not_break_too_early():
    c = SentenceChunker()
    out = c.feed("It costs 3.50 dollars today. Fine.")
    assert out == ["It costs 3.50 dollars today.", "Fine."]


def test_speakable_strips_markdown():
    assert speakable("**Bold** and `code` at https://example.com") == "Bold and code at example.com"
