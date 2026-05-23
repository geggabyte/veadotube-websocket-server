import json
import time
import threading
import os


class ConfigManager:
    """Configuration manager for reading and watching configuration files.
    
    This class handles loading configuration from a JSON file, watching for
    changes, and providing thread-safe access to configuration data.
    
    Attributes:
        path: Path to the configuration file
        data: Current configuration data
        callbacks: List of callback functions to notify on config changes
        last_mtime: Last modification time of the config file
        lock: Threading lock for thread-safe access to config data
    """
    
    def __init__(self, path="config.json"):
        """Initialize the configuration manager.
        
        Args:
            path: Path to the configuration file (default: "config.json")
        """
        self.path = path
        self.data = {}
        self.callbacks = []
        self.last_mtime = 0
        self.lock = threading.Lock()

        self.load()

        threading.Thread(target=self._watch, daemon=True).start()

    def register_callback(self, cb):
        """Register a callback function to be notified of configuration changes.
        
        Args:
            cb: Callback function to be called when config changes
        """
        self.callbacks.append(cb)

    def load(self):
        """Load configuration from the file."""
        with open(self.path, "r") as f:
            data = json.load(f)

        with self.lock:
            self.data = data

        print("[CONFIG] Reloaded")

        # notify listeners
        for cb in self.callbacks:
            cb(self.data)

    def get(self):
        """Get a copy of the current configuration data.
        
        Returns:
            dict: Copy of the current configuration data
        """
        with self.lock:
            return self.data.copy()
    
    def read(self, name):
        """Read a specific configuration value.
        
        Args:
            name: Name of the configuration value to read
            
        Returns:
            The configuration value
        """
        with self.lock:
            return self.data[name]

    def _watch(self):
        """Internal method to watch for configuration file changes."""
        while True:
            try:
                mtime = os.path.getmtime(self.path)

                if mtime != self.last_mtime:
                    self.last_mtime = mtime
                    self.load()

            except Exception as e:
                print("[CONFIG ERROR]", e)

            time.sleep(1)