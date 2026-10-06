"""검토 계획은 만료되며 한 번만 사용할 수 있다."""
import mcp_plans


def test_plan_expiry_and_replay(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(mcp_plans.time, 'time', lambda: now[0])
    issued = mcp_plans.issue('test', {'id': 'sample'})
    assert mcp_plans.take(issued['token'], 'test') == {'id': 'sample'}
    assert mcp_plans.take(issued['token'], 'test') is None
    expired = mcp_plans.issue('test', {'id': 'sample'})
    now[0] += mcp_plans.TTL + 1
    assert mcp_plans.take(expired['token'], 'test') is None
