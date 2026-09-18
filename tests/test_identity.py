import pytest

from beamkit import identity


@pytest.fixture(autouse=True)
def user(monkeypatch):
    monkeypatch.setattr(identity, "_username", lambda: "u")


def test_self_is_the_user_and_needs_no_confirm():
    i = identity.resolve("self")
    assert i.run_as == "self" and i.owner == "u" and i.mine is True and i.production is False
    assert i.default_publish_location == "scratch" and i.dev_dir is None


def test_mu2epro_is_production_and_needs_confirm():
    with pytest.raises(identity.IdentityError, match="confirm=True"):
        identity.resolve("mu2epro")
    i = identity.resolve("mu2epro", confirm=True)
    assert i.owner == "mu2e" and i.mine is False and i.production is True
    assert i.default_publish_location == "tape"


def test_a_read_only_use_of_mu2epro_needs_no_confirm():
    assert identity.resolve("mu2epro", writes=False).owner == "mu2e"


@pytest.mark.parametrize("bad", ["root", "", None, 7, "Self"])
def test_unknown_run_as_refused(bad):
    with pytest.raises(identity.IdentityError, match="run_as must be one of"):
        identity.resolve(bad)


def test_dev_dir_comes_from_the_environment_once(monkeypatch):
    monkeypatch.setenv("BEAMKIT_PRODTOOLS_DIR", "/exp/mu2e/app/users/u/prodtools")
    i = identity.resolve("self")
    assert i.dev_dir == "/exp/mu2e/app/users/u/prodtools"
    assert i.dev_dir_for_shipping() == "/exp/mu2e/app/users/u/prodtools"
    monkeypatch.setenv("BEAMKIT_PRODTOOLS_DIR", "")
    assert identity.resolve("self").dev_dir is None


def test_production_may_not_ship_a_dev_checkout(monkeypatch):
    monkeypatch.setenv("BEAMKIT_PRODTOOLS_DIR", "/exp/mu2e/app/users/u/prodtools")
    i = identity.resolve("mu2epro", confirm=True)
    with pytest.raises(identity.IdentityError, match="BEAMKIT_PRODTOOLS_DIR"):
        i.dev_dir_for_shipping()
    # the rule is about SHIPPING code; a production identity that ships none is fine
    assert i.production is True


def test_for_record_reads_the_record_not_the_environment(monkeypatch):
    class Rec:
        run_as, owner = "mu2epro", "mu2e"
    monkeypatch.setenv("BEAMKIT_PRODTOOLS_DIR", "/x")
    i = identity.for_record(Rec)
    assert i.mine is False and i.owner == "mu2e" and i.dev_dir is None


def test_nersc_site_takes_the_configured_owner():
    i = identity.resolve("self", site="nersc", owner="nersc_login")
    assert i.owner == "nersc_login" and i.dev_dir is None and i.mine is True


def test_nersc_site_refuses_mu2epro_even_confirmed():
    with pytest.raises(identity.IdentityError, match="site='nersc' accepts run_as='self' only"):
        identity.resolve("mu2epro", confirm=True, site="nersc", owner="x")


def test_nersc_site_needs_an_owner():
    with pytest.raises(identity.IdentityError, match="owner"):
        identity.resolve("self", site="nersc")


def test_unknown_site_refused():
    with pytest.raises(identity.IdentityError, match="site must be one of"):
        identity.resolve("self", site="ornl")


def test_fermilab_site_ignores_owner_argument():
    assert identity.resolve("self", owner="ignored").owner == "u"

