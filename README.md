# Veadotube WebSocket Proxy Server

This is a WebSocket proxy server that forwards messages between connected clients. It can be run as a standalone server or with a GUI interface.

## Features

- WebSocket proxy server with client management
- GUI interface for starting/stopping the server
- Real-time message logging
- Cross-platform compatibility

## Requirements

- Python 3.7+
- Required packages (install with: `pip install -r requirements.txt`)

## Usage

### Running with GUI

1. Run `proxy_gui.bat` to start the server with GUI interface
2. Click "Start Server" to begin
3. Use "Stop Server" to stop the server
4. All messages are displayed in the log window

### Running as command-line server

1. Run `python proxy.py` to start the server
2. The server will listen on `ws://0.0.0.0:8765`

### Building as executable

1. Run `python build_exe.py` to create a standalone executable
2. The executable will be created in the `dist` folder

## Files

- `proxy.py` - Main proxy server with GUI
- `proxy_gui.bat` - Batch file to run the GUI version
- `client_test.py` - Test client to verify server functionality
- `build_exe.py` - Script to build executable version
- `requirements.txt` - Required Python packages