# Veadotube WebSocket Proxy Server

This is a WebSocket proxy server that forwards messages between connected clients. It can be run as a standalone server or with a GUI interface. The system also includes a client that bridges Veadotube with the proxy server, enabling message forwarding between the two systems.

## Features

- WebSocket proxy server with client management
- GUI interface for starting/stopping the server
- Real-time message logging
- Cross-platform compatibility
- Configuration management with auto-reload
- Bridge client for connecting to Veadotube system
- Message mapping between Veadotube nodes and proxy nodes

## Requirements

- Python 3.7+
- Required packages (install with: `pip install -r requirements.txt`)

## Usage

### Running with GUI

1. Run `proxy.bat` to start the server with GUI interface
2. Click "Start Server" to begin
3. Use "Stop Server" to stop the server
4. All messages are displayed in the log window

### Running as command-line server

1. Run `python proxy.py` to start the server
2. The server will listen on `ws://0.0.0.0:8765`

### Running the Bridge Client

1. Run `client.bat` to start the bridge client
2. The client will connect to both Veadotube and the proxy server
3. Messages will be forwarded between the systems based on the configuration

### Building as executable

1. Run `build.bat` to create standalone executables
2. The executables will be created in the `bin` folder

## Files

- `proxy.py` - Main proxy server with GUI
- `proxy.bat` - Batch file to run the GUI version
- `client.py` - Bridge client for connecting to Veadotube
- `client.bat` - Batch file to run the client
- `config_manager.py` - Configuration manager with auto-reload
- `config.json` - Configuration file for the client
- `build.bat` - Script to build executable version
- `requirements.txt` - Required Python packages

## Configuration

The system uses a `config.json` file for configuration. The configuration includes:
- `client_id`: Unique identifier for this client
- `proxy_url`: URL of the proxy server to connect to
- `veado_host`: Host of the Veadotube server
- `veado_port`: Port of the Veadotube server
- `listen_map`: List of Veadotube nodes to listen to
- `send_map`: List of mappings for forwarding messages between systems

## Changelog

### v1.0.0 - Initial Release
- Added WebSocket proxy server with GUI interface
- Implemented client for connecting to Veadotube system
- Added configuration management with auto-reload
- Added build scripts for creating executables