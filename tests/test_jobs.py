# tests/test_jobs.py
import unittest, time
from detector.jobs import JobQueue

class TestJobQueue(unittest.TestCase):
    def test_submit_runs_and_stores_result(self):
        q = JobQueue(run_fn=lambda ca: {"mint": ca, "ok": True})
        q.start()
        q.submit("CA1")
        for _ in range(50):
            if q.status("CA1")["state"] == "done":
                break
            time.sleep(0.05)
        st = q.status("CA1")
        self.assertEqual(st["state"], "done")
        self.assertEqual(st["result"]["mint"], "CA1")

    def test_error_is_captured(self):
        def boom(ca): raise ValueError("nope")
        q = JobQueue(run_fn=boom); q.start(); q.submit("CA2")
        for _ in range(50):
            if q.status("CA2")["state"] == "error":
                break
            time.sleep(0.05)
        self.assertEqual(q.status("CA2")["state"], "error")
        self.assertIn("nope", q.status("CA2")["error"])

    def test_unknown_ca_is_idle(self):
        q = JobQueue(run_fn=lambda ca: {})
        self.assertEqual(q.status("X")["state"], "idle")

if __name__ == "__main__":
    unittest.main()
