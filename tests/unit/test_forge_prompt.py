"""Forge turns a FLUX image into noise when the prompt carries a ratio such as "vertical 9:16"."""
import pytest
from studio.pipeline import forge_prompt


@pytest.mark.parametrize("prompt, expected", [
    ("A wolf pack running through the forest, vertical 9:16", "A wolf pack running through the forest"),
    ("Two wolves, 16:9, at night", "Two wolves, at night"),
    ("square 1:1 photo of a dog", "photo of a dog"),
    ("A clock showing 10:30 am, vertical 9:16", "A clock showing 10:30 am"),
    ("A cat", "A cat"),
])
def test_forge_prompt_drops_ratios(prompt, expected):
    assert forge_prompt(prompt) == expected
