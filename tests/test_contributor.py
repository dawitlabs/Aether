import pytest
from pydantic import ValidationError

from aether.core.models import Contributor


def test_agents_may_only_propose():
    assert Contributor(type="agent", display_name="bot").permissions == ["propose"]
    for permission in ("review", "admin"):
        with pytest.raises(ValidationError):
            Contributor(type="agent", display_name="bot", permissions=["propose", permission])


def test_humans_may_review_and_administer():
    human = Contributor(type="human", display_name="h", permissions=["review", "admin"])
    assert human.permissions == ["review", "admin"]
