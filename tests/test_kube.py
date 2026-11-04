from oncall_agent.kube import reachable_host


def test_wildcard_address_from_k3d_becomes_loopback():
    # k3d writes https://0.0.0.0:<port>; Go's kubectl accepts it, Windows sockets refuse it.
    assert reachable_host("https://0.0.0.0:59166") == "https://127.0.0.1:59166"


def test_other_hosts_are_left_alone():
    assert reachable_host("https://127.0.0.1:6443") == "https://127.0.0.1:6443"
    assert reachable_host("https://k8s.example.com") == "https://k8s.example.com"
