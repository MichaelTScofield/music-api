import socket
import sys
import threading
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from single_instance import SingleInstance


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class SingleInstanceTest(unittest.TestCase):
    def test_second_instance_notifies_first_instance(self):
        event = threading.Event()
        first = SingleInstance("test-single-instance", get_free_port())
        try:
            self.assertTrue(first.acquire())
            first.set_show_callback(event.set)

            second = SingleInstance("test-single-instance", first.port)
            self.assertFalse(second.acquire())
            self.assertTrue(second.notify_existing())
            self.assertTrue(event.wait(2))
        finally:
            first.close()

    def test_ignores_message_for_different_app_id(self):
        event = threading.Event()
        first = SingleInstance("test-single-instance-a", get_free_port())
        try:
            self.assertTrue(first.acquire())
            first.set_show_callback(event.set)

            other = SingleInstance("test-single-instance-b", first.port)
            self.assertFalse(other.notify_existing())

            time.sleep(0.2)
            self.assertFalse(event.is_set())
        finally:
            first.close()

    def test_queues_show_until_callback_is_registered(self):
        event = threading.Event()
        first = SingleInstance("test-single-instance", get_free_port())
        try:
            self.assertTrue(first.acquire())

            second = SingleInstance("test-single-instance", first.port)
            self.assertFalse(second.acquire())
            self.assertTrue(second.notify_existing())

            first.set_show_callback(event.set)
            self.assertTrue(event.wait(2))
        finally:
            first.close()


if __name__ == "__main__":
    unittest.main()
