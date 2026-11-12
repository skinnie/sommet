"""sommet_paths: the one-time move of the app's old-name folders (AmbitApp -> Sommet)."""
import importlib
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))


def load(home):
    os.environ["HOME"] = str(home)
    import sommet_paths
    return importlib.reload(sommet_paths)


class MoveOldFolders(unittest.TestCase):
    def setUp(self):
        self._home = os.environ.get("HOME")
        self.t = pathlib.Path(tempfile.mkdtemp())

    def tearDown(self):
        os.environ["HOME"] = self._home

    def put(self, rel, text):
        p = self.t / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def test_plain_rename(self):
        self.put("AmbitAppBackups/garmin_tokens/k", "key")
        self.put("AmbitAppPlans/a.json", "plan")
        self.put(".config/ambit-app/known_watches.json", "{}")
        sp = load(self.t)
        self.assertEqual(sp.backups_dir(), self.t / "SommetBackups")
        self.assertEqual((self.t / "SommetBackups/garmin_tokens/k").read_text(), "key")
        self.assertTrue((self.t / "SommetPlans/a.json").exists())
        self.assertTrue((self.t / ".config/Sommet/known_watches.json").exists())
        for old in ("AmbitAppBackups", "AmbitAppPlans", ".config/ambit-app"):
            self.assertFalse((self.t / old).exists(), old)

    def test_merge_never_overwrites(self):
        self.put("AmbitAppBackups/scale/w", "old")
        self.put("AmbitAppBackups/scale/only_old", "x")
        self.put("SommetBackups/scale/w", "new")
        load(self.t).move_old_folders()
        self.assertEqual((self.t / "SommetBackups/scale/w").read_text(), "new")
        self.assertEqual((self.t / "AmbitAppBackups/scale/w").read_text(), "old")
        self.assertTrue((self.t / "SommetBackups/scale/only_old").exists())

    def test_nothing_to_move(self):
        load(self.t).move_old_folders()
        self.assertFalse((self.t / "SommetBackups").exists())

    def test_symlinked_old_folder_stays_a_link(self):
        target = self.t / "external" / "backups"
        target.mkdir(parents=True)
        (target / "k").write_text("key")
        os.symlink(target, self.t / "AmbitAppBackups")
        sp = load(self.t)
        sp.move_old_folders()
        new = self.t / "SommetBackups"
        self.assertTrue(new.is_symlink())
        self.assertEqual((new / "k").read_text(), "key")
        self.assertFalse((self.t / "AmbitAppBackups").exists())

    def test_refused_rename_links_the_new_name(self):
        self.put("AmbitAppPlans/a.json", "plan")
        sp = load(self.t)
        real_rename = sp.os.rename
        def refuse(*a, **k):
            raise OSError(18, "Invalid cross-device link")
        sp.os.rename = refuse
        try:
            sp.move_old_folders()
        finally:
            sp.os.rename = real_rename
        new = self.t / "SommetPlans"
        self.assertTrue(new.is_symlink())
        self.assertEqual((new / "a.json").read_text(), "plan")
        self.assertTrue((self.t / "AmbitAppPlans" / "a.json").exists())


if __name__ == "__main__":
    unittest.main()
