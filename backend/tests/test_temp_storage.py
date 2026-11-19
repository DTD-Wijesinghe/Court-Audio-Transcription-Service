import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from court_transcription import temp_storage


class TemporaryStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "court-temp"
        self.patch = patch.object(temp_storage, "TEMP_STORAGE_ROOT", self.root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_workspace_is_isolated_and_removed(self):
        workspace = temp_storage.create_job_workspace("job-123")
        payload = workspace / "chunks" / "chunk.flac"
        payload.write_bytes(b"audio")
        reclaimed = temp_storage.cleanup_job_workspace(workspace)
        self.assertGreaterEqual(reclaimed, 5)
        self.assertFalse(workspace.exists())

    def test_cleanup_cannot_delete_outside_managed_root(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        with self.assertRaises(ValueError):
            temp_storage.cleanup_job_workspace(outside)
        self.assertTrue(outside.exists())

    def test_stale_dead_workspace_is_removed(self):
        workspace = temp_storage.create_job_workspace("abandoned")
        (workspace / ".owner-pid").write_text("99999999", encoding="ascii")
        old = time.time() - 3 * 3600
        os.utime(workspace, (old, old))
        result = temp_storage.cleanup_stale_workspaces(stale_hours=1)
        self.assertEqual(result["removed"], 1)
        self.assertFalse(workspace.exists())

    def test_active_workspace_is_never_removed(self):
        workspace = temp_storage.create_job_workspace("active-job")
        old = time.time() - 3 * 3600
        os.utime(workspace, (old, old))
        result = temp_storage.cleanup_stale_workspaces(active_job_ids=["active-job"], stale_hours=1)
        self.assertEqual(result["removed"], 0)
        self.assertTrue(workspace.exists())


if __name__ == "__main__":
    unittest.main()
