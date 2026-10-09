import pytest

from aether.domains import available, load_pack


def test_known_packs_exist():
    assert {"curie-sample", "radioactivity"} <= set(available())


@pytest.mark.parametrize("slug", available())
def test_every_pack_is_consistent(slug):
    pack = load_pack(slug)
    assert pack.goldens, "a pack needs golden questions"
    assert all(path.read_text().strip() for path in pack.corpus())
    assert any(not g.answerable for g in pack.goldens), "include an unanswerable question"
