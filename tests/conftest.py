# Fail the test run if we see any warnings
def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if terminalreporter.stats.get("warnings"):  # pragma: no cover
        print("\nWARNINGS DETECTED: Exiting with error")
        if terminalreporter._session.exitstatus == 0:
            terminalreporter._session.exitstatus = 13
