# Veadotube WebSocket Server

NOTE THIS PROJECT IS NOT RELATED TO DEVELOPERS OF [Veadotube](https://veado.tube/) IN ANY SHAPE OR CAPACITY
Go support them and their beautifull tool! Thanks [olmewe](https://olmewe.com/) & [BELLA!](https://bellaexclamation.art/)!

This project was tested only on Veadotube NOT Veadotube mini. 
"State Events" websocket has not been tested yet (Both bool and numbers are working fine)! In case of any suggestions don't hessitate to create an issue.

This is a server and a client for synchronizing Veadotube avatars between remote machines. (e.g. you are planning to stream with two avatars one of which is controlled by your friend remotely)

## Features

- WebSocket proxy server with GUI interface
- Bridge client for connecting to Veadotube system
- Configuration management with auto-reload functionality
- Node mapping for forwarding messages between systems
- Cross-platform compatibility

## Project Structure

```
veadotube-websocket-server/
├── proxy.py          # Main WebSocket proxy server with GUI
├── client.py         # Bridge client for connecting to Veadotube system
├── config_manager.py # Configuration manager with auto-reload functionality
├── config.py         # Configuration editor GUI
├── requirements.txt  # Python dependencies
└── README.md         # This file
```

## Installation

1. Install Python 3.7 or higher
2. Install required packages:
   ```
   pip install -r requirements.txt
   ```
Or just download released package ;)

## Usage

### Running the Proxy Server
Main bridge between clients. Recieves commands from client and translates to all connected instances.
`python proxy.py` OR `VeadotubeProxy.exe`

### Running the Bridge Client
Bridge between [Veadotube](https://veado.tube/) instance and Proxy server. Based on configuration:
* Listens to [Veadotube](https://veado.tube/) websockets nodes (based on configuration) and sends them to proxy server as configured
* Listens to proxy server and translates that to Veadoo

`python client.py` OR `VeadotubeProxyClient.exe`

### Editing Configuration
Configuration GUI for Bridge and Client.
`python config.py` OR `VeadotubeProxyClientConfig.exe`

## Configuration

The configuration file (`config.json`) contains:
- `veado_host`: Host for the Veadotube system
- `veado_port`: Port for the Veadotube system
- `client_id`: Unique identifier for this client
- `proxy_url`: URL for client to connect to Proxy
- `proxy_port`: Port for Proxy to run on
- `listen_mappings`: List of node mappings to listen for
- `send_mappings`: List of node mappings to send to

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