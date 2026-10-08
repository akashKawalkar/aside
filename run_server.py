import asyncio
import os
import selectors

import uvicorn


async def serve():
    config = uvicorn.Config(
        "capture.server:app",
        host="127.0.0.1",
        port=int(os.environ.get("AGENT_PORT", 8787)),
        reload=False,
    )

    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(serve())
    finally:
        loop.close()