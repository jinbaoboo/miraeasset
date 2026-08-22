import tempfile
import unittest
from pathlib import Path

from src.pipeline.build_corpus import _exclusive_build_lock


class BuildLockTests(unittest.TestCase):
    def test_second_writer_is_rejected_and_lock_is_released(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.db"
            with _exclusive_build_lock(db_path):
                with self.assertRaises(RuntimeError):
                    with _exclusive_build_lock(db_path):
                        pass
            with _exclusive_build_lock(db_path):
                pass


if __name__ == "__main__":
    unittest.main()
