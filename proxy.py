# proxy.py
import asyncio
import websockets

clients = set()

async def handler(ws):
    clients.add(ws)
    try:
        async for msg in ws:
            for c in clients:
                if c != ws:
                    print(msg)
                    await c.send(msg)
    finally:
        clients.remove(ws)

async def main():
    async with websockets.serve(handler, "0.0.0.0", 8765):
        await asyncio.Future()

asyncio.run(main())