# opml_loader.py
import os, time, threading, glob

def resolve_opml_dir():
    path = os.environ.get("OPML_DIR") or os.path.expanduser("~/Documents/AI_Navigator/OPML")
    os.makedirs(path, exist_ok=True)
    return path

def list_opml(dirpath):
    files = sorted(glob.glob(os.path.join(dirpath, "*.opml")))
    return [{"name": os.path.basename(f), "path": f} for f in files]

class OpmlWatcher:
    def __init__(self, on_change, interval=2.0):
        self.on_change = on_change
        self.interval = interval
        self.dir = resolve_opml_dir()
        self._stop = threading.Event()
        self._last = set()

    def start(self):
        # initial fire
        self._tick()
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            self._tick()
            time.sleep(self.interval)

    def _tick(self):
        files = set(glob.glob(os.path.join(self.dir, "*.opml")))
        if files != self._last:
            self._last = files
            self.on_change(list_opml(self.dir))

