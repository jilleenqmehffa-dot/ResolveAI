"""Proxy observations record active state without modifying the proxy."""

from evals.adapter import toxiproxy_adapter


def test_capture_proxy_selects_target_and_keeps_fault_details(monkeypatch):
    seen = []

    def fake_call(api, path):
        seen.append((api, path))
        return {
            "name": "agent_rabbitmq_tcp",
            "enabled": False,
            "upstream": "rabbitmq:5672",
            "toxics": [{
                "name": "timeout_downstream", "type": "timeout",
                "stream": "downstream", "attributes": {"timeout": 500},
                "toxicity": 1.0,
            }],
        }

    monkeypatch.setattr(toxiproxy_adapter, "call", fake_call)
    observed = toxiproxy_adapter.capture_proxy("http://proxy.test", "rabbitmq")

    assert seen == [("http://proxy.test", "/proxies/agent_rabbitmq_tcp")]
    assert observed.enabled is False
    assert observed.toxics == [{
        "name": "timeout_downstream", "type": "timeout",
        "stream": "downstream", "attributes": {"timeout": 500},
    }]
