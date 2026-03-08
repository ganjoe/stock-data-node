import unittest
from pathlib import Path
import shutil
import tempfile
from unittest.mock import MagicMock
from file_watcher import FileWatcher

class TestFileWatcherBackup(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.watch_dir = Path(self.test_dir) / "watch"
        self.backup_dir = Path(self.test_dir) / "data" / "watchlists"
        self.watch_dir.mkdir(parents=True)
        # Note: The FileWatcher in src/file_watcher.py uses a hardcoded Path("data/watchlists")
        # relative to the CWD. For testing, we'll need to be careful or mock the Path.
        # Let's check how FileWatcher is initialized.
        
        self.queue = MagicMock()
        self.resolver = MagicMock()
        self.config = MagicMock()
        self.failed_store = MagicMock()
        
        # We need to monkeypatch the backup_dir in the instance for testing
        self.watcher = FileWatcher(
            str(self.watch_dir),
            self.queue,
            self.resolver,
            self.config,
            self.failed_store
        )
        self.watcher._backup_dir = self.backup_dir

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_scan_once_backups_file(self):
        # Create a dummy watch file
        test_file = self.watch_dir / "test.txt"
        test_file.write_text("AAPL, MSFT", encoding="utf-8")
        
        # Run scan_once
        self.watcher.scan_once()
        
        # Verify backup exists
        backup_file = self.backup_dir / "test.txt"
        self.assertTrue(backup_file.exists(), "Backup file should exist")
        self.assertEqual(backup_file.read_text(encoding="utf-8"), "AAPL, MSFT")
        
        # Verify original file was processed (in this case, deleted since no failures)
        # The resolver needs to return something for it to be considered processed
        # Wait, scan_once calls _process_file which calls _cleanup_file.
        # If no failed_tickers, it unlinks.
        
    def test_scan_once_creates_backup_dir(self):
        # Remove backup dir if it was created in setUp
        if self.backup_dir.exists():
            shutil.rmtree(self.backup_dir)
            
        test_file = self.watch_dir / "test.txt"
        test_file.write_text("AAPL", encoding="utf-8")
        
        self.watcher.scan_once()
        
        self.assertTrue(self.backup_dir.exists(), "Backup directory should be created")

if __name__ == "__main__":
    unittest.main()
