"""Keep sync model streams alive while their next event is blocked on upstream I/O."""
from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable, Iterable, Iterator

import anyio
from starlette.concurrency import iterate_in_threadpool
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)
HEARTBEAT_SECONDS = 10.0


class KeepaliveStreamingResponse(StreamingResponse):
    def __init__(self, events, *, finalize=None, **kwargs):
        self._source = events
        self._finalize = finalize
        self._worker_scheduled = threading.Event()
        super().__init__(keepalive_stream(events, finalize=finalize,
                                         scheduled=self._worker_scheduled), **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # ASGI 2.4 signals disconnect through a failed send, while older
            # servers cancel the stream task. Both must close the adapter.
            with anyio.CancelScope(shield=True):
                await self.body_iterator.aclose()
                if not self._worker_scheduled.is_set():
                    # The connection may fail while sending response headers,
                    # before the body iterator ever starts its producer.
                    await anyio.to_thread.run_sync(self._close_unstarted)

    def _close_unstarted(self):
        try:
            close = getattr(self._source, "close", None)
            if close:
                close()
        finally:
            if self._finalize:
                self._finalize()


async def keepalive_stream(events: Iterable[str], *, finalize=None, scheduled=None):
    stream = keepalive_events(events, finalize=finalize, scheduled=scheduled)
    try:
        async for event in iterate_in_threadpool(stream):
            yield event
    finally:
        # StreamingResponse may stop on http.disconnect. Explicitly close the
        # adapter so its producer is signalled even if the upstream is silent.
        stream.close()


def keepalive_events(events: Iterable[str], *, interval: float | None = None,
                     finalize: Callable[[], None] | None = None,
                     scheduled: threading.Event | None = None) -> Iterator[str]:
    """Emit blank NDJSON lines during silence; one worker owns and closes the source.

    A bounded queue retains backpressure. Closing the response signals the worker,
    which closes the source as soon as an in-flight upstream read returns, allowing
    its history/quota finalizers to run without closing a running generator.
    """
    interval = HEARTBEAT_SECONDS if interval is None else interval
    pending = queue.Queue(maxsize=1)
    stopped = threading.Event()
    done = object()

    def send(item):
        while not stopped.is_set():
            try:
                pending.put(item, timeout=interval)
                return True
            except queue.Full:
                continue
        return False

    def produce():
        source = iter(events)
        try:
            while not stopped.is_set():
                try:
                    item = next(source)
                except StopIteration:
                    break
                if not send(item):
                    break
        except Exception as exc:
            send(exc)
        finally:
            try:
                close = getattr(source, "close", None)
                if close:
                    close()
            except Exception:
                logger.exception("Stream cleanup failed")
            finally:
                # A generator closed before its first next() never enters its
                # finally block. Release a reservation even in this startup race.
                try:
                    if finalize:
                        finalize()
                except Exception as exc:
                    send(exc)
                send(done)

    threading.Thread(target=produce, daemon=True).start()
    if scheduled is not None:
        scheduled.set()
    try:
        # Flush the response before the first model token or team result arrives.
        yield "\n"
        while True:
            try:
                item = pending.get(timeout=interval)
            except queue.Empty:
                yield "\n"
                continue
            if item is done:
                return
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        stopped.set()
