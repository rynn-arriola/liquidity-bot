import asyncio
import importlib.metadata
import json
import unittest
from types import SimpleNamespace

from polymarket_us.websocket.private import PrivateWebSocket

from liquidity_bot.errors import AdapterError
from liquidity_bot.streams import PrivateReadOnlyStream

SDK_MAJOR = int(importlib.metadata.version("polymarket-us").split(".")[0])


class GracefullyClosedConnection:
    """A server that sends one heartbeat and then closes normally (code 1000)."""

    def __init__(self):
        self._frames = [json.dumps({"heartbeat": {}})]

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._frames:
            return self._frames.pop(0)
        raise StopAsyncIteration

    async def close(self, *args):
        return None


def queued(stream):
    items = []
    while not stream._queue.empty():
        items.append(stream._queue.get_nowait())
    return items


class SdkGracefulCloseTests(unittest.IsolatedAsyncioTestCase):
    async def run_server_close(self):
        socket = PrivateWebSocket(key_id="k", secret_key="unused-no-connect")
        stream = PrivateReadOnlyStream(SimpleNamespace(ws=SimpleNamespace(private=lambda: socket)))
        socket._ws = GracefullyClosedConnection()
        await asyncio.wait_for(socket._message_loop(), timeout=5)
        return queued(stream)

    @unittest.skipIf(SDK_MAJOR < 1, "SDK 0.x does not report a graceful server close")
    async def test_graceful_server_close_now_reaches_bot_recovery(self):
        items = await self.run_server_close()
        errors = [item for item in items if isinstance(item, AdapterError)]
        self.assertEqual(len(errors), 1)
        self.assertIn("private WebSocket disconnected", str(errors[0]))

    @unittest.skipIf(SDK_MAJOR >= 1, "documents the old SDK 0.x silent close")
    async def test_old_sdk_graceful_close_is_silent(self):
        items = await self.run_server_close()
        self.assertFalse(any(isinstance(item, AdapterError) for item in items))
