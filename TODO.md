# TODO

Questions I could not answer on my own, and things worth doing next.
Written while merging the three apps into one - the numbered questions are the
ones where your answer actually changes the code.

## Questions for you

### 1. What does Veadotube send for a listened node? (most important)

The old client forwarded `data["payload"]` from Veadotube straight through as
the `value`, and the receiving side wrapped it back up as
`{"event": "set", "value": <that>}`. I kept that exactly as it was, because you
said booleans and numbers work, and I had no way to trigger a real node change
while testing.

But if Veadotube actually sends `payload: {"event": "push", "value": true}`
rather than a bare `true`, then the far side is receiving
`{"event": "set", "value": {"event": "push", "value": true}}` - which would be
wrong even if it happens to work today.

**Could you check?** Run the app, set the log level to `DEBUG`, toggle a node in
Veadotube and look for a line like:

```
Veadotube -> nodes: {"event":"payload","type":"boolean","id":"IMaxMouth","payload":...}
```

Paste me what `payload` looks like. If it is wrapped, I will unwrap it (and I
would want to know whether existing setups depend on the current shape before
changing it).

- [ ] Confirm the payload shape for a boolean node
- [ ] Confirm the payload shape for a number node
- [ ] Confirm the payload shape for a "state events" node

### 2. Node listing works on Veadotube 0.6 - RESOLVED

I previously concluded 0.6 does not answer node list requests. That was wrong.
The request was always correct; the **reply was being thrown away**. Per the
[nodes channel docs](https://veado.tube/docs/tech/api/nodes/) the answer comes
back in an `entries` array, but `nodes.py` was reading a `nodes` key, so every
reply parsed to nothing and the UI reported "Veadotube did not send a list".

Probing your running instance confirms it answers straight away:

```
instance:{"name":"veadotube - Playthrough-Scene.veadoscene","version":"0.6",...}
nodes:{"event":"list","entries":[
  {"type":"boolean","id":"MaxKeyboardPress","name":"MaxKeyboardPress"},
  {"type":"boolean","id":"MaxMousePress","name":"MaxMousePress"},
  {"type":"number","id":"MaxMousePosition","name":"MaxMousePosition"},
  {"type":"stateEvents","id":"MaxMouthState","name":"MaxMouthState"}]}
```

Fixed, and the list is now read live rather than fetched once: the bridge client
sends `nodes: {"event":"listen"}`, so Veadotube pushes a new list whenever it
changes (switching scene or avatar), and the Configuration tab follows it.

**This needs your eyes:** the node ids in `config.json` (`IMaxBodyCard`,
`IMaxBodyAudience`, `IMaxMouth`) do not match any node in the instance above.
As it stands the listen map subscribes to three nodes that do not exist and
nothing will ever be forwarded.

- [ ] Do those `I…`/`O…` nodes live in a different scene than
      `Playthrough-Scene.veadoscene`, or is the config simply out of date?

### 3. Should a leftover copy be stopped automatically?

If the proxy port is busy, the app names the process holding it and **asks**
before stopping it - even when it is obviously an old copy of this app.

You asked for "relaunch after a crash without port is busy", which works today
because Windows releases the port the moment a process dies. The ask-first
dialog only appears in the rarer case where the old process is still alive but
wedged.

- [ ] Should it stop a **leftover copy of this app** silently, and only ask when
      the port is held by an unrelated program? (I would rather ask you than
      have the app kill things on its own initiative.)

### 4. How do the two machines actually reach each other?

The proxy binds `0.0.0.0` and speaks plain `ws://` with **no authentication**.
Anyone who can reach that port can drive your avatar, and everything crossing
the internet is unencrypted.

That is fine over a LAN or a private network. It is not fine on a
port-forwarded public IP.

- [ ] How do you and your friend connect - LAN, port forwarding, Tailscale /
      ZeroTier / Hamachi, something else?

Depending on the answer I would add either a shared-secret token (simple,
enough to stop drive-by nonsense) or `wss://` with a certificate (more work,
proper encryption). See suggestion S2.

### 5. Old builds in `bin/`

`bin/` still has `VeadotubeProxy.exe`, `VeadotubeProxyClient.exe` and
`VeadotubeProxyClientConfig.exe` from May. Their source is gone (it lives on in
git history), and `build.bat` deletes them the next time you build.

- [ ] Fine to let the next build remove them, or do you want them kept somewhere?

### 6. Veadotube instance discovery

**Find Veadotube** scans for the instance files Veadotube writes, and I guessed
three likely locations (`~/.veadotube/instances`, `%APPDATA%/veadotube/instances`,
`~/.config/veadotube/instances`). I could not confirm which one 0.6 uses because
I did not want to go rummaging through your user folders uninvited.

- [ ] Does the button find your running instance? If not, where does 0.6 put
      those files?

## Suggestions

### S1. Test it for real, end to end
The self-test (`python smoketest.py`) covers the whole message path against a
fake Veadotube, and I verified the real build connects to your actual Veadotube,
subscribes to your three nodes, relays through the proxy, shuts down cleanly and
restarts after a force-kill. What I could **not** test is two real machines with
two real Veadotubes. Worth one session with your friend before you rely on it
mid-stream.

### S2. Authentication on the proxy
A `shared_secret` in the config; clients send it on connect, the proxy drops
connections that do not match. Roughly 30 lines, and it turns "anyone who finds
my port can move my avatar" into "nobody can". This is the one thing I would do
next if it were my call.

### S3. Value transforms in the send map
Right now a mapping is a rename: `boolean:A` becomes `boolean:B`. Useful
additions: invert a boolean, scale/offset a number, clamp a range, or map one
node onto several. Fits the existing send map as an optional `transform` key.

### S4. Minimise to the tray
The window is not something you want on screen while streaming. A tray icon
with the status (green/amber/red) and a restore option would suit this better
than a taskbar window. Needs `pystray` + `Pillow`, which adds a few MB to the
exe.

### S5. Start with Windows
A checkbox that registers the app under `HKCU\...\Run`. Easy, and it means one
less thing to remember before going live.

### S6. A "send test value" button
Pick a node, send a value, see if the other side reacts - without having to
touch Veadotube. Would make diagnosing a broken mapping much faster than
reading DEBUG logs.

### S7. Headless mode
`--headless` to run the proxy with no window at all, for hosting it on a VPS or
a spare machine. The services are already GUI-free; it is mostly argument
parsing and console logging.

### S8. Per-instance log files
Two copies running side by side (different `--config`) both write to
`logs/veadobridge.log`. They interleave fine, but the 2 MB rotation can trip
over itself when two processes rotate at once. If you ever run two, I would name
the log after the config file.

### S9. Sign the executable
The unsigned exe gets a SmartScreen warning, which is a rough first impression
for anyone downloading a release. A cheap code-signing certificate fixes it.

### S10. Latency and health display
The dashboard shows message counts. Round-trip time to the proxy and time since
the last message from the other side would tell you at a glance whether your
friend's machine is still alive.

## Carried over from the previous TODO

- [x] ~~More comprehensive error handling~~ - every connection now retries with
      backoff, every failure is logged with a plain-language explanation, and
      unhandled exceptions in any thread or event loop reach the log instead of
      vanishing.
- [x] ~~Logging instead of print statements~~ - one `logging` setup, one window,
      one rotating file.
- [x] ~~Connection retry logic~~ - both the Veadotube and proxy connections
      reconnect on their own.
- [x] ~~Document all configuration options~~ - table in the README.
- [x] ~~Troubleshooting section~~ - in the README.
- [ ] Test "State Events" nodes (see question 1).
- [ ] Usage examples for all components.
- [ ] User authentication - see S2; the proxy needs it more than the GUI does.

## Known rough edges

Things I chose not to fix, so they are not surprises later:

- **Restarting a service briefly freezes the window.** Stop waits up to 8
  seconds for the background thread; in practice it takes milliseconds, but a
  hung connection could make the UI unresponsive for a moment. Fixing it
  properly means making stop asynchronous, which complicates the shutdown path
  for very little gain.
- **Messages are dropped, not buffered, while Veadotube is disconnected.** Stale
  avatar state seemed worse than none, and everything re-subscribes on
  reconnect. Say the word if you would rather they queue.
- **The proxy relays blindly.** It does not parse or validate what it forwards,
  which keeps it simple and version-proof, but also means a malformed client
  cannot be told off.
