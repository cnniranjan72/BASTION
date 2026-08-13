import bastion


def test_package_exports_client_and_errors():
    assert bastion.BastionClient is not None
    assert bastion.BastionBlockedError is not None
    assert bastion.current_span() is None
