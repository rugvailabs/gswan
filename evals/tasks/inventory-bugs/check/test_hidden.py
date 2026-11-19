import unittest

from inventory import Inventory


class Hidden(unittest.TestCase):
    def test_add_validates(self):
        i = Inventory()
        for bad in (0, -3):
            with self.assertRaises(ValueError):
                i.add("a", bad)
        self.assertEqual(i.total_units(), 0)

    def test_remove(self):
        i = Inventory(); i.add("a", 5)
        with self.assertRaises(KeyError):
            i.remove("zzz", 1)
        for bad in (0, -1, 6):
            with self.assertRaises(ValueError):
                i.remove("a", bad)
        i.remove("a", 5)
        self.assertNotIn("a", i.items)

    def test_low_stock_strict_and_sorted(self):
        i = Inventory(); i.add("b", 2); i.add("a", 1); i.add("c", 3)
        self.assertEqual(i.low_stock(3), ["a", "b"])
        self.assertEqual(i.low_stock(1), [])

    def test_merge_leaves_other_unchanged(self):
        a, b = Inventory(), Inventory(); a.add("x", 1); b.add("x", 2); b.add("y", 4)
        a.merge(b)
        self.assertEqual(a.items, {"x": 3, "y": 4}); self.assertEqual(b.items, {"x": 2, "y": 4})


if __name__ == "__main__":
    unittest.main()
