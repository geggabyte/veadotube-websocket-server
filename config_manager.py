import json
import time
import threading
import os


class ConfigManager:
    def __init__(self, path="config.json"):
        self.path = path
        self.data = {}
        self.callbacks = []
        self.last_mtime = 0
        self.lock = threading.Lock()

        self.load()

        threading.Thread(target=self._watch, daemon=True).start()

    # ------------------------
    def register_callback(self, cb):
        self.callbacks.append(cb)

    # ------------------------
    def load(self):
        with open(self.path, "r") as f:
            data = json.load(f)

        with self.lock:
            self.data = data

        print("[CONFIG] Reloaded")

        # notify listeners
        for cb in self.callbacks:
            cb(self.data)

    # ------------------------
    def get(self):
        with self.lock:
            return self.data.copy()
    
    # ------------------------
    def read(self, name):
        with self.lock:
            return self.data[name]

    # ------------------------
    def _watch(self):
        while True:
            try:
                mtime = os.path.getmtime(self.path)

                if mtime != self.last_mtime:
                    self.last_mtime = mtime
                    self.load()

            except Exception as e:
                print("[CONFIG ERROR]", e)

            time.sleep(1)