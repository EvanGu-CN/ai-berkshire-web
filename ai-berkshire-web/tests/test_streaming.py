import asyncio
import threading
from types import SimpleNamespace

import pytest

from server import streaming
from starlette.requests import ClientDisconnect


def test_silent_source_emits_heartbeats_without_waiting_for_first_result():
    ready = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    def source():
        try:
            ready.set()
            assert release.wait(2)
            yield '{"type":"done"}\n'
        finally:
            closed.set()

    events = streaming.keepalive_events(source(), interval=0.01)
    try:
        assert next(events) == '\n'
        assert ready.wait(1)
        for _ in range(3):
            assert next(events) == '\n'
        release.set()
        assert list(events) == ['{"type":"done"}\n']
        assert closed.wait(1)
    finally:
        release.set()
        events.close()


def test_source_error_is_not_a_success_event():
    def source():
        yield '{"type":"delta","text":"partial"}\n'
        raise ValueError('source failed')

    events = streaming.keepalive_events(source(), interval=0.01)
    assert next(events) == '\n'
    assert 'partial' in next(events)
    with pytest.raises(ValueError, match='source failed'):
        list(events)


def test_disconnect_unblocks_bounded_queue_and_closes_source():
    closed = threading.Event()
    produced = []

    def source():
        try:
            for index in range(100):
                produced.append(index)
                yield str(index)
        finally:
            closed.set()

    events = streaming.keepalive_events(source(), interval=0.01)
    assert next(events) == '\n'
    assert next(events) == '0'
    events.close()
    assert closed.wait(1)
    assert len(produced) < 100


@pytest.mark.parametrize('spec', ['2.0', '2.4'])
def test_http_disconnect_closes_adapter_while_model_is_silent(monkeypatch, spec):
    monkeypatch.setattr(streaming, 'HEARTBEAT_SECONDS', 0.01)
    ready = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    def source():
        try:
            ready.set()
            assert release.wait(2)
            yield '{"type":"delta","text":"partial"}\n'
        finally:
            closed.set()

    async def run():
        disconnect = asyncio.Event()
        heartbeats = []

        async def send(message):
            if message['type'] == 'http.response.body':
                heartbeats.append(message.get('body'))
                if len(heartbeats) == 3:
                    disconnect.set()
                    if spec == '2.4':
                        raise OSError('connection closed')

        async def receive():
            await disconnect.wait()
            return {'type': 'http.disconnect'}

        response = streaming.KeepaliveStreamingResponse(source())
        request = response({'type': 'http', 'asgi': {'spec_version': spec}}, receive, send)
        if spec == '2.4':
            with pytest.raises(ClientDisconnect):
                await asyncio.wait_for(request, timeout=1)
        else:
            await asyncio.wait_for(request, timeout=1)
        assert ready.is_set()
        assert all(body == b'\n' for body in heartbeats)
        release.set()
        # Assert before asyncio.run closes any leftover async generators.
        assert await asyncio.to_thread(closed.wait, 1)

    try:
        asyncio.run(run())
    finally:
        release.set()
    assert closed.wait(1)


def test_close_before_worker_starts_still_runs_reservation_finalizer(monkeypatch):
    targets = []
    finalized = threading.Event()

    class DeferredThread:
        def __init__(self, *, target, daemon):
            self.target = target

        def start(self):
            targets.append(self.target)

    monkeypatch.setattr(streaming, 'threading', SimpleNamespace(Thread=DeferredThread, Event=threading.Event))

    def source():
        pytest.fail('cancelled request must not call the model')
        yield ''

    events = streaming.keepalive_events(source(), finalize=finalized.set)
    assert next(events) == '\n'
    events.close()
    targets[0]()
    assert finalized.is_set()


def test_header_send_failure_releases_reservation_without_starting_model():
    finalized = threading.Event()

    def source():
        pytest.fail('disconnected request must not call the model')
        yield ''

    async def run():
        async def send(message):
            raise OSError('disconnected before body')

        async def receive():
            await asyncio.sleep(1)
            return {'type': 'http.disconnect'}

        response = streaming.KeepaliveStreamingResponse(source(), finalize=finalized.set)
        with pytest.raises(ClientDisconnect):
            await response({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send)
        assert finalized.is_set()

    asyncio.run(run())
