"""Verify the project installs and imports correctly."""


def test_agent_package_is_importable():
    import agent

    assert agent is not None
