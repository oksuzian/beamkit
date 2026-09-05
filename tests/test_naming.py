import pytest

from beamkit import naming

SHA = "e470313" + "0" * 33


def test_validate_tag_accepts_alnum():
    assert naming.validate_tag("MuBeam2") == "MuBeam2"


@pytest.mark.parametrize("bad", ["Mu-Beam", "mu.beam", "", "Mu Beam", 7])
def test_validate_tag_refuses(bad):
    with pytest.raises(naming.NamingError):
        naming.validate_tag(bad)


def test_dsconf_base_is_seven_hex():
    assert naming.dsconf_base(SHA) == "e470313"


def test_dsconf_base_refuses_short_sha():
    with pytest.raises(naming.NamingError):
        naming.dsconf_base("e470313")


def test_cnf_name():
    assert naming.cnf_name("oksuzian", "MuBeam", "e470313") == "cnf.oksuzian.MuBeam.e470313.0.tar"


def test_allocate_free_base_unsuffixed():
    probed = []
    def taken(name):
        probed.append(name)
        return False
    assert naming.allocate_dsconf("u", "T", "e470313", taken) == "e470313"
    assert probed == ["cnf.u.T.e470313.0.tar"]


def test_allocate_one_collision_gives_001():
    taken = lambda name: name == "cnf.u.T.e470313.0.tar"
    assert naming.allocate_dsconf("u", "T", "e470313", taken) == "e470313-001"


def test_allocate_two_collisions_gives_002():
    busy = {"cnf.u.T.e470313.0.tar", "cnf.u.T.e470313-001.0.tar"}
    assert naming.allocate_dsconf("u", "T", "e470313", lambda n: n in busy) == "e470313-002"


def test_allocate_explicit_free():
    assert naming.allocate_dsconf("u", "T", "e470313", lambda n: False, explicit="MCPTest007") == "MCPTest007"


def test_allocate_explicit_taken_is_error():
    with pytest.raises(naming.NamingError, match="never reused"):
        naming.allocate_dsconf("u", "T", "e470313", lambda n: True, explicit="MCPTest007")


def test_allocate_explicit_bad_token():
    with pytest.raises(naming.NamingError):
        naming.allocate_dsconf("u", "T", "e470313", lambda n: False, explicit="a.b")



def test_every_name_beamkit_produces_lives_here():
    """Four names, one home. tools used to hold three of them as f-strings."""
    assert naming.run_id("MuBeam", "e470313-001") == "MuBeam.e470313-001"
    assert naming.dataset("oksuzian", "MuBeam", "e470313") == "nts.oksuzian.MuBeam.e470313.root"
    assert naming.beamfile_name("mu2e", "MuBeam", "bm", "e470313") == "etc.mu2e.MuBeamBeam-bm.e470313.txt"
    assert naming.cnf_name("mu2e", "MuBeam", "e470313") == "cnf.mu2e.MuBeam.e470313.0.tar"
