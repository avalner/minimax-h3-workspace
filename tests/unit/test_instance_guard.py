"""One data folder, one copy of the program.

The guard is a named Windows mutex, so it is checked the only way that proves anything: two real
processes, the second one must be refused. A single-process test would pass even if the mutex name
were a constant.

    python tests/unit/test_instance_guard.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

import support
from storage.atomic import atomic_write_json

import instance

CHILD = """
import sys, time
sys.path.insert(0, {app!r})
import instance
print("ok" if instance.acquire({data!r}) else "busy", flush=True)
time.sleep(float(sys.argv[1]))
instance.release()
"""


def child(data_dir, seconds):
    code = CHILD.format(app=str(support.APP), data=str(data_dir))
    return subprocess.Popen([sys.executable, "-B", "-c", code, str(seconds)], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")


def stop(proc):
    """Let the child finish and close its pipe (an open pipe is a ResourceWarning in the report)."""
    proc.wait(timeout=20)
    if proc.stdout:
        proc.stdout.close()


class InstanceGuardTest(unittest.TestCase):
    def test_a_second_copy_is_refused_while_the_first_works(self):
        data = support.new_dir("instance")
        first = child(data, 3)
        try:
            self.assertEqual(first.stdout.readline().strip(), "ok", "первая копия не смогла занять папку")
            second = child(data, 0.2)
            try:
                answer = second.stdout.readline().strip()
            finally:
                stop(second)
            self.assertEqual(answer, "busy", "вторая копия заняла ту же папку данных")
        finally:
            stop(first)

    def test_the_folder_is_free_again_after_the_copy_ends(self):
        data = support.new_dir("instance")
        first = child(data, 0.3)
        try:
            self.assertEqual(first.stdout.readline().strip(), "ok")
        finally:
            stop(first)
        again = child(data, 0.2)
        try:
            self.assertEqual(again.stdout.readline().strip(), "ok", "папка не освободилась после выхода копии")
        finally:
            stop(again)

    def test_two_different_folders_do_not_block_each_other(self):
        first_dir = support.new_dir("instance")
        second_dir = support.new_dir("instance")
        first = child(first_dir, 3)
        try:
            self.assertEqual(first.stdout.readline().strip(), "ok")
            second = child(second_dir, 0.2)
            try:
                self.assertEqual(second.stdout.readline().strip(), "ok",
                                 "вторая библиотека — законный случай, она не должна блокироваться")
            finally:
                stop(second)
        finally:
            stop(first)

    def test_the_running_copy_says_where_it_is(self):
        data = support.new_dir("instance")
        instance.publish(data, 8765)
        try:
            note = instance.published(data)
            self.assertEqual(note["pid"], os.getpid())
            self.assertEqual(note["url"], "http://127.0.0.1:8765")
        finally:
            instance.forget(data, pid=os.getpid())

    def test_a_note_from_a_dead_copy_is_ignored(self):
        data = support.new_dir("instance")
        atomic_write_json(Path(data) / "instance.json", {"pid": 2147483640, "port": 1})
        self.assertIsNone(instance.published(data), "запись мёртвой копии не должна выдавать себя за живую")

    def test_forget_does_not_remove_someone_elses_note(self):
        data = support.new_dir("instance")
        instance.publish(data, 1)
        try:
            instance.forget(data, pid=123456)
            self.assertIsNotNone(instance.published(data), "удалена запись другой копии")
            instance.forget(data, pid=os.getpid())
            self.assertIsNone(instance.published(data))
        finally:
            instance.forget(data)


if __name__ == "__main__":
    unittest.main()
