"""Regression: a sidebar rebuild must not cancel the session, and keepalive works."""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tor2 import proto
from tor2.app import Tor2App

async def _no_network(self):
    self.set_status("not connected")
Tor2App.start_network = _no_network
from tor2.clientserver import SERVER

async def main():
    # a real (loopback) session so the receive loop is genuinely running
    got_server = asyncio.get_event_loop().create_future()
    async def on_conn(r, w):
        got_server.set_result(await proto.handshake(r, w))
    srv = await asyncio.start_server(on_conn, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]

    app = Tor2App()
    async with app.run_test() as pilot:
        await pilot.pause()
        r, w = await asyncio.open_connection("127.0.0.1", port)
        client_session = await proto.handshake(r, w)
        server_session = await got_server

        app.session = client_session
        app.state = SERVER
        app.srv = {"onion":"x","name":"kairos","local":"k","channel":"general",
                   "channels":[],"admin":True,"online":[],"buffers":{},"download":None}
        app.run_worker(app.server_loop(client_session), group="net", exclusive=False)
        await pilot.pause(0.5)

        # server sends authok + several members updates (each triggers a rebuild)
        await server_session.send({"t":"authok","nick":"kairo","admin":True,
                                   "token":"t","channels":["general"],
                                   "channel":"general","server":"kairos"})
        await pilot.pause(0.5)
        for i in range(4):
            await server_session.send({"t":"members","online":[f"u{i}"],
                                       "channels":["general","random","music"][:i+1]})
            await pilot.pause(0.6)

        assert app.session is client_session, "SESSION WAS DROPPED by sidebar rebuild"
        assert app.state == SERVER, app.state
        print("survived", 4, "sidebar rebuilds — session intact")

        # a message still gets through afterwards
        await server_session.send({"t":"event","chan":"general","nick":"mia",
                                   "ts":0,"body":"still here","media":None,"id":1})
        await pilot.pause(0.8)
        assert any("still here" in str(l) for l in app.srv["buffers"]["general"])
        print("messages still flow after rebuilds")

        # keepalive: client pings, and answers a server ping with a pong
        await server_session.send({"t":"ping"})
        pong = await asyncio.wait_for(server_session.recv(), timeout=10)
        assert pong["t"] == "pong", pong
        print("ping -> pong OK")

        app.session = None
        await app.action_quit()
    srv.close()
    print("DISCONNECT REGRESSION PASSED")

def test_main():
    asyncio.run(main())