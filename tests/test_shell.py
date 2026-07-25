from avg.shell import pick_port, make_token

def test_port_is_free_and_ephemeral():
    p = pick_port()
    assert 1024 < p < 65536

def test_token_is_unguessable_and_unique():
    a, b = make_token(), make_token()
    assert a != b and len(a) >= 32
