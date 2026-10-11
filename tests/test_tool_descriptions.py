"""Tool descriptions: the first line is what a tool-retrieval index sees
(it truncates around 110 characters), so it must be short and carry the
English and Spanish words a request would use."""

from vulcan.agent_tools import TOOLS


def test_first_line_fits_the_tool_index():
    for tool in TOOLS:
        first = tool.description.split("\n", 1)[0]
        assert len(first) <= 110, (tool.name, len(first), first)
        assert first.strip(), tool.name


def test_every_tool_keeps_its_full_description_and_spanish_words():
    import re
    for tool in TOOLS:
        assert len(tool.description) > len(tool.description.split("\n", 1)[0]), tool.name
        assert re.search(r"[áéíóúñ¿]", tool.description), tool.name


def test_descriptions_stay_under_250_tokens():
    """Hosts pay for every token of every description on every turn: 750 characters is about 250 tokens with the
    conservative chars/3 estimate. Argument details belong in the parameter descriptions."""
    for tool in TOOLS:
        assert len(tool.description) <= 750, (tool.name, len(tool.description))
