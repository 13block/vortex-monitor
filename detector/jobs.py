# detector/jobs.py
import threading, queue, traceback

class JobQueue:
    def __init__(self, run_fn, max_entries=200):
        self.run_fn = run_fn
        self.max_entries = max_entries
        self._q = queue.Queue()
        self._state = {}            # ca -> dict
        self._lock = threading.Lock()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def submit(self, ca):
        with self._lock:
            cur = self._state.get(ca, {}).get("state")
            if cur in ("pending", "running"):
                return dict(self._state[ca])
            self._state[ca] = {"state": "pending", "result": None, "error": None}
            # Bound retained state: evict oldest done/error entries (FIFO insertion order),
            # never evicting pending/running jobs.
            while len(self._state) > self.max_entries:
                victim = None
                for k, v in self._state.items():
                    if v.get("state") in ("done", "error"):
                        victim = k
                        break
                if victim is None:
                    break
                del self._state[victim]
        self._q.put(ca)
        return dict(self._state[ca])

    def status(self, ca):
        with self._lock:
            return dict(self._state.get(ca, {"state": "idle", "result": None, "error": None}))

    def _set(self, ca, **kw):
        with self._lock:
            self._state.setdefault(ca, {})
            self._state[ca].update(kw)

    def _worker(self):
        while True:
            ca = self._q.get()
            self._set(ca, state="running")
            try:
                res = self.run_fn(ca)
                self._set(ca, state="done", result=res, error=None)
            except Exception as e:
                self._set(ca, state="error", error=f"{e}\n{traceback.format_exc()}")
            finally:
                self._q.task_done()
