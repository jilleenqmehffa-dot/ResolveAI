"""A network scenario restores a previously disabled proxy."""

from infra.toxiproxy import control


def test_reset_reenables_and_disconnect_uses_reset_peer(monkeypatch):
    calls = []

    def call(api, path, payload=None):
        calls.append((api, path, payload))
        return {}

    monkeypatch.setattr(control, "call", call)
    control.set_fault("http://proxy.test", "disconnect")
    assert calls == [
        ("http://proxy.test", "/reset", {}),
        ("http://proxy.test", "/proxies/agent_commerce_http", {"enabled": True}),
        (
            "http://proxy.test", "/proxies/agent_commerce_http/toxics",
            {
                "name": "reset_peer_downstream",
                "type": "reset_peer",
                "stream": "downstream",
                "attributes": {"timeout": 0},
            },
        ),
    ]


def test_fault_can_target_rabbitmq_without_changing_commerce(monkeypatch):
    calls = []

    def call(api, path, payload=None):
        calls.append((path, payload))
        return {}

    monkeypatch.setattr(control, "call", call)
    control.set_fault("http://proxy.test", "down", target="rabbitmq")
    assert calls == [
        ("/reset", {}),
        ("/proxies/agent_rabbitmq_tcp", {"enabled": True}),
        ("/proxies/agent_rabbitmq_tcp", {"enabled": False}),
    ]
