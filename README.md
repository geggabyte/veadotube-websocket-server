# Veadotube Bridge

NOTE: THIS PROJECT IS NOT RELATED TO THE DEVELOPERS OF [Veadotube](https://veado.tube/) IN ANY SHAPE OR CAPACITY.
Go support them and their beautiful tool! Thanks [olmewe](https://olmewe.com/) & [BELLA!](https://bellaexclamation.art/)!

Sync Veadotube avatars between machines. You stream with two avatars, a friend
somewhere else controls one of them, and their toggles show up on your screen.

Tested against **Veadotube 0.6** (the full app), not Veadotube mini.
"State Events" nodes are still untested - booleans and numbers work fine.
Suggestions and issues are very welcome.

## What changed in 2.0

The proxy server, the bridge client and the config editor used to be three
separate executables with three separate windows. They are now **one app, one
window, one log**:

- a single `VeadotubeBridge.exe` - no install, no Python needed
- the proxy server and the bridge client are started and stopped from a
  dashboard, and both write to the same log pane
- the configuration editor is a tab in the same window, and changes apply
  without restarting the app
- a crash or a force-kill can no longer leave the proxy port stuck - see
  [Ports](#ports-and-why-the-port-is-busy-should-not-happen-anymore)

## How it fits together

```
   YOUR PC                                        FRIEND'S PC
   ┌───────────────┐                              ┌───────────────┐
   │   Veadotube   │                              │   Veadotube   │
   └───────┬───────┘                              └───────┬───────┘
           │ ws://127.0.0.1:2424                          │
   ┌───────┴───────────────────┐                  ┌───────┴───────┐
   │  Veadotube Bridge         │◄────────────────►│ Veadotube     │
   │  · proxy server  :8765    │   ws://you:8765  │ Bridge        │
   │  · bridge client          │                  │ · client only │
   └───────────────────────────┘                  └───────────────┘
```

One machine runs the **proxy server** (everyone connects to it) and usually a
**bridge client** as well. Every other machine runs only a bridge client
pointed at the proxy. Each bridge client:

- subscribes to the Veadotube nodes in its **listen map**
- translates them through its **send map** and pushes them to the proxy
- applies anything the proxy relays from *other* clients to its own Veadotube

Updates are collapsed per node and flushed 60 times a second, so hammering a
toggle costs one message per tick, not one per twitch.

## Getting started

### Run it

Download the release and run `VeadotubeBridge.exe`. That is the whole install.

From source instead:

```
pip install -r requirements.txt
python main.py
```

### Set it up

1. In Veadotube, enable the WebSocket server and note its port (2424 by default).
2. Open the **Configuration** tab.
   - **Client ID** - a name for this machine. It **must be different on each
     side**; it is how the app knows not to echo your own changes back at you.
   - **Veadotube host / port** - where Veadotube is listening. *Find Veadotube*
     tries to fill this in for you.
   - **Proxy URL** - where the bridge client connects. On the machine hosting
     the proxy that is `ws://localhost:8765`; on the other machine it is
     `ws://<host-machine-address>:8765`.
   - **Proxy port** - the port the proxy server listens on. Only matters on the
     machine that hosts it.
3. Add the nodes you want to share to the **listen map**, as `type:id` - for
   example `boolean:IMaxMouth`. The id is the name you gave the node in
   Veadotube.
4. Add a **send map** row for each one, mapping your node onto the node it
   should drive on the other machine.
5. Tick the services this machine should run - *Start the proxy server on
   launch* on the machine hosting the proxy, *Start the bridge client on launch*
   on every machine. Both are **off by default**, so a fresh copy does not grab
   a port or dial out before you have told it what to do.
6. **Save & apply**. The services pick the change up immediately.

On the machine that only receives, leave the listen and send maps empty and
leave *Start the proxy server on launch* unticked.

### Watch it work

Until you tick the boxes above, start the two services by hand from the
**Dashboard** - it shows both of them, and the log shows everything they do. Set
the log level to `DEBUG` to see every individual message going in and out - very
handy when a mapping is not firing.

## Command line

```
VeadotubeBridge.exe [--config PATH] [--debug] [--version]
```

`--config` lets you run several copies side by side, each with its own settings
and its own proxy port. Without it, `config.json` next to the executable is
used.

## Configuration reference

`config.json`, next to the executable:

| Key | Meaning |
| --- | --- |
| `client_id` | Name of this machine. Must be unique across the bridge. |
| `proxy_url` | Where the bridge client connects, e.g. `ws://192.168.1.10:8765`. |
| `proxy_host` | Address the proxy server binds to. `0.0.0.0` accepts remote connections. |
| `proxy_port` | Port the proxy server listens on. |
| `veado_host` | Veadotube's address, usually `127.0.0.1`. |
| `veado_port` | Veadotube's WebSocket port. |
| `run_proxy` | Start the proxy server on launch. Off by default. |
| `run_client` | Start the bridge client on launch. Off by default. |
| `reconnect_delay` | Seconds between reconnection attempts. |
| `send_rate` | How many times a second queued updates are flushed (1-240). |
| `log_level` | Which messages the log window shows by default. The log *file* always keeps everything. |
| `listen_map` | Nodes to subscribe to: `[{"type": "boolean", "id": "IMaxMouth"}]`. |
| `send_map` | Translations: `[{"from": {...}, "to": {...}}]`. |

The file is written atomically, so a crash mid-save cannot corrupt it. Unusable
values are replaced with the defaults and the substitution is written to the
log rather than failing the launch. Editing the file by hand while the app is
running works too - it is reloaded automatically.

## Ports, and why "port is busy" should not happen anymore

- The listening socket is bound **before** anything else starts, so a problem is
  reported immediately instead of half-way through startup.
- Shutting the window closes every connection, closes the server and releases
  the port - and a watchdog force-exits the process if any of that wedges, so
  the port is never held by a zombie.
- On Windows the port is bound with `SO_EXCLUSIVEADDRUSE`, so no other program
  can quietly bind the same port and steal half your traffic.
- If the port really is taken, the app names the program holding it (with its
  PID) and offers to stop it. If that program looks like a leftover copy of this
  app, it says so.
- **Check port** on the Dashboard runs the same diagnosis on demand.
- Instance locks are OS-level file locks, so a killed app releases them
  instantly. Leftover lock files from earlier crashes are swept on startup.

Two copies of the app cannot share one config file - the second one says so and
exits instead of fighting over the port. Two copies with *different* `--config`
files are fine.

## Troubleshooting

| What you see | What it usually means |
| --- | --- |
| `Cannot reach Veadotube at 127.0.0.1:2424` | Veadotube is not running, or its WebSocket server is off, or on a different port. Try **Find Veadotube**. |
| `Cannot reach the proxy at ws://...` | The proxy server is not running on that machine, the address is wrong, or a firewall/router is in the way. |
| Both connected, but nothing moves | Check the send map. Set the log level to `DEBUG` - `Veadotube -> proxy` lines mean your side is sending, `Proxy -> Veadotube` lines mean the other side is arriving. |
| Changes bounce back and forth | Both machines are using the same **Client ID**. Give them different names. |
| `Port 8765 is already in use` | Use **Check port** to see who has it, or pick another port. |
| Nothing at all in the log window | The level filter is set too high. Everything is in the log file regardless - **Open log folder**. |

## Files the app creates

Next to the executable (or in `%APPDATA%\VeadotubeBridge` if that folder is not
writable):

```
config.json          your settings
logs/                veadobridge.log, rotated at 2 MB, 3 kept
runtime/             instance locks, removed on exit
```

Nothing is written to the registry and nothing is installed. Deleting the
folder removes every trace.

## Building

```
build.bat
```

Installs the dependencies, runs the self-test, and produces
`bin\VeadotubeBridge.exe` - one file, nothing else to ship.

Run the self-test on its own with `python smoketest.py`. It stands up a fake
Veadotube, a proxy and two bridge clients on temporary ports and checks that a
node change comes out the other side, that the port is released afterwards, and
that a killed instance does not block the next one.

## Project layout

```
veadotube-websocket-server/
├── main.py               entry point (also what PyInstaller builds)
├── smoketest.py          headless end-to-end self-test
├── build.bat             one-command build
└── veadobridge/
    ├── app.py            wiring, lifecycle, shutdown, port conflicts
    ├── gui.py            the window: dashboard + shared log
    ├── configview.py     the Configuration tab
    ├── widgets.py        small Tk helpers
    ├── proxy.py          the relay server
    ├── client.py         the bridge client
    ├── veado.py          the Veadotube connection
    ├── nodes.py          node list lookup
    ├── config.py         load, validate, save, watch
    ├── service.py        thread + event loop lifecycle
    ├── netutil.py        port binding and process inspection
    ├── singleton.py      single-instance lock
    ├── logbus.py         the one log everything writes to
    └── paths.py          where files live
```

## License

MIT License

Copyright (c) 2026 geggabyte

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
