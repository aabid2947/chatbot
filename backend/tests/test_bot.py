import pytest

from bot import clean_title


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Jaipur fort trip", "Jaipur fort trip"),
        ("Jaipur Trip Title: Amber Fort Visit Plan", "Amber Fort Visit Plan"),
        ("Title: Weekly meal plan.", "Weekly meal plan"),
        ('"Fixing a Python import error"', "Fixing a Python import error"),
        ("**Budget travel tips**", "Budget travel tips"),
        ("\n\nFirst line\nsecond line", "First line"),
        ("   ", None),
    ],
)
def test_clean_title(raw, expected):
    assert clean_title(raw) == expected
