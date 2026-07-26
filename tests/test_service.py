import service


def test_run_calls_poll_and_process_each_iteration(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "poll_and_process", lambda batch_size: calls.append(batch_size) or 0)
    monkeypatch.setattr(service.db, "sweep_stale_processing", lambda timeout_minutes: 0)
    monkeypatch.setattr(service.time, "sleep", lambda _: None)
    monkeypatch.setattr(service.time, "monotonic", lambda: 0.0)

    svc = service.PollerService(poll_interval=0, sweep_interval=999, batch_size=7)
    svc.run(max_iterations=3)

    assert calls == [7, 7, 7]


def test_sweep_runs_immediately_then_waits_for_interval(monkeypatch):
    sweep_calls = []
    monkeypatch.setattr(service, "poll_and_process", lambda batch_size: 0)
    monkeypatch.setattr(service.db, "sweep_stale_processing", lambda timeout_minutes: sweep_calls.append(1) or 0)
    monkeypatch.setattr(service.time, "sleep", lambda _: None)

    # time.monotonic() is called twice per iteration (cycle_start, then elapsed).
    # last_sweep starts at 0.0, so the first real cycle_start (however large, like
    # real uptime-based monotonic()) always triggers an immediate first sweep --
    # simulated here with a nonzero base, not 0, to match that real behavior.
    clock = iter([1000.0, 1000.0, 1010.0, 1010.0, 1020.0, 1020.0, 1060.0, 1060.0])
    monkeypatch.setattr(service.time, "monotonic", lambda: next(clock))

    svc = service.PollerService(poll_interval=0, sweep_interval=50, batch_size=1)
    svc.run(max_iterations=4)

    # t=1000 (immediate first sweep), t=1010 (no, only 10 elapsed), t=1020 (no, only
    # 20 elapsed since t=1000), t=1060 (yes, 60 elapsed since t=1000) -- 2 sweeps.
    assert len(sweep_calls) == 2


def test_poll_exception_does_not_crash_the_loop(monkeypatch):
    calls = {"n": 0}

    def counting_poll(batch_size):
        calls["n"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(service, "poll_and_process", counting_poll)
    monkeypatch.setattr(service.db, "sweep_stale_processing", lambda timeout_minutes: 0)
    monkeypatch.setattr(service.time, "sleep", lambda _: None)
    monkeypatch.setattr(service.time, "monotonic", lambda: 0.0)

    svc = service.PollerService(poll_interval=0, sweep_interval=999)
    svc.run(max_iterations=3)  # would raise and abort here if the exception weren't caught

    assert calls["n"] == 3


def test_sweep_exception_does_not_crash_the_loop(monkeypatch):
    def failing_sweep(timeout_minutes):
        raise RuntimeError("boom")

    monkeypatch.setattr(service, "poll_and_process", lambda batch_size: 0)
    monkeypatch.setattr(service.db, "sweep_stale_processing", failing_sweep)
    monkeypatch.setattr(service.time, "sleep", lambda _: None)
    monkeypatch.setattr(service.time, "monotonic", lambda: 0.0)

    svc = service.PollerService(poll_interval=0, sweep_interval=0)
    svc.run(max_iterations=2)  # would raise and abort here if the exception weren't caught


def test_signal_handler_stops_the_loop_before_next_iteration(monkeypatch):
    calls = []

    def poll_then_signal(batch_size):
        calls.append(1)
        if len(calls) == 1:
            svc._handle_signal(2, None)  # SIGINT's value, simulated -- no real OS signal needed
        return 0

    monkeypatch.setattr(service, "poll_and_process", poll_then_signal)
    monkeypatch.setattr(service.db, "sweep_stale_processing", lambda timeout_minutes: 0)
    monkeypatch.setattr(service.time, "sleep", lambda _: None)
    monkeypatch.setattr(service.time, "monotonic", lambda: 0.0)

    svc = service.PollerService(poll_interval=0, sweep_interval=999)
    svc.run()  # no max_iterations -- relies entirely on the shutdown flag to stop

    assert len(calls) == 1
