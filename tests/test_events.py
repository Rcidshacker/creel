import unittest

from creel.core.events import AttemptFinished, AttemptStarted, EventBus


class TestEventBus(unittest.TestCase):
    def test_emit_reaches_all_subscribers(self):
        bus = EventBus()
        received = []
        bus.subscribe(received.append)
        bus.subscribe(received.append)
        event = AttemptStarted(run_id="r1", engine="scrapling_http", url="https://x.com", at=1.0)
        bus.emit(event)
        self.assertEqual(len(received), 2)
        self.assertIs(received[0], event)

    def test_attempt_finished_shape(self):
        event = AttemptFinished(
            run_id="r1", engine="scrapling_http", url="https://x.com",
            at=1.0, duration_ms=120, status="failed", failure_class="blocked", detail="403",
        )
        self.assertEqual(event.status, "failed")
        self.assertEqual(event.failure_class, "blocked")

    def test_no_subscribers_does_not_raise(self):
        EventBus().emit(AttemptStarted(run_id="r1", engine="x", url="https://x.com", at=1.0))


if __name__ == "__main__":
    unittest.main()
