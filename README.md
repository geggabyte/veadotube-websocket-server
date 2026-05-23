# Veadotube WebSocket Proxy Server

A WebSocket proxy server that forwards messages between connected clients with a graphical user interface.

## Features

- WebSocket proxy server with client management
- GUI interface for starting/stopping the server
- Real-time message logging
- Cross-platform compatibility

## Requirements

- Python 3.7+
- websockets library

## Installation

1. Clone or download this repository
2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

Run the proxy server:
```bash
python proxy.py
```

The GUI will appear with controls to start and stop the server.

## How It Works

1. The server listens on `ws://0.0.0.0:8765`
2. When clients connect, they are added to a set of connected clients
3. When a message is received from any client, it is forwarded to all other connected clients
4. The GUI provides real-time logging of all server activities

## Project Structure

```
veadotube-websocket-server/
├── proxy.py          # Main WebSocket proxy server with GUI
├── requirements.txt  # Python dependencies
└── README.md         # This file
```

## License

MIT License