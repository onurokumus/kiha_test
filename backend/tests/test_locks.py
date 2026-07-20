"""Lock-ordering guarantees in locks.data_read (bug 2.1).

A read of an idle test must not be starved by reads that are parked on a
write-locked (rebuilding/ingesting) test. With the per-test read lock taken
BEFORE the global read slot, a parked reader blocks without holding a slot.
"""

import threading
import time
import unittest
from threading import BoundedSemaphore
from unittest.mock import patch

from app import locks


class DataReadOrderingTests(unittest.TestCase):
    def test_read_of_idle_test_not_starved_by_write_locked_test(self):
        # One slot: under the OLD (slot-first) ordering a single reader parked
        # on the write-locked test would hold it and block every other read.
        with patch.object(locks, "_data_read_slots", BoundedSemaphore(1)):
            parked_started = threading.Event()
            release = threading.Event()

            def read_busy():
                parked_started.set()
                # Blocks on test_read("busy") because we hold test_write below.
                with locks.data_read("busy"):
                    release.wait(3)

            with locks.test_write("busy"):
                parker = threading.Thread(target=read_busy)
                parker.start()
                self.assertTrue(parked_started.wait(1))
                time.sleep(0.15)  # let it reach the blocking point

                idle_done = threading.Event()

                def read_idle():
                    with locks.data_read("idle"):
                        idle_done.set()

                reader = threading.Thread(target=read_idle)
                reader.start()
                # New ordering: "idle" grabs its read lock + the free slot and
                # finishes promptly. Old ordering: the slot is held by the
                # parked reader and this times out.
                self.assertTrue(
                    idle_done.wait(1),
                    "read of idle test was blocked by a write-locked test (2.1)")
                reader.join(1)

            release.set()
            parker.join(2)
            self.assertFalse(parker.is_alive())

    def test_slot_still_bounds_concurrent_reads(self):
        # The slot must keep bounding concurrent native reads (the crash gate).
        with patch.object(locks, "_data_read_slots", BoundedSemaphore(1)):
            inside = threading.Event()
            hold = threading.Event()
            second_entered = threading.Event()

            def first():
                with locks.data_read("t"):
                    inside.set()
                    hold.wait(3)

            def second():
                with locks.data_read("t"):
                    second_entered.set()

            t1 = threading.Thread(target=first)
            t1.start()
            self.assertTrue(inside.wait(1))
            t2 = threading.Thread(target=second)
            t2.start()
            # only one slot -> the second read cannot enter until the first
            # releases (both share test_read, so it's the slot doing the gating)
            self.assertFalse(second_entered.wait(0.3))
            hold.set()
            self.assertTrue(second_entered.wait(1))
            t1.join(1)
            t2.join(1)


if __name__ == "__main__":
    unittest.main()
