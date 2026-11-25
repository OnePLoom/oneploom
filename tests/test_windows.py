import os
import unittest

from oneploom.windows import catalog_path


@unittest.skipUnless(os.name == "nt", "Windows 사용자 폴더 조회 API 검사")
class WindowsTests(unittest.TestCase):
    def test_actual_known_folder_lookup_without_creation(self):
        path = catalog_path()
        self.assertTrue(path.is_absolute())
        self.assertEqual(path.name, "projects.json")
        self.assertEqual(path.parent.name, "OnepLoom")
        self.assertTrue(path.parent.parent.is_dir())
        before = path.exists()
        self.assertEqual(catalog_path(), path)
        self.assertEqual(path.exists(), before)


if __name__ == "__main__":
    unittest.main()
