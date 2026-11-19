import unittest

from textkit import slugify, truncate, wrap


class Hidden(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify("Hello, World!"), "hello-world")
        self.assertEqual(slugify("  Crème Brûlée "), "creme-brulee")
        self.assertEqual(slugify(""), "")
        self.assertEqual(slugify("a---b__c"), "a-b-c")
        self.assertEqual(slugify("日本"), "")

    def test_wrap(self):
        self.assertEqual(wrap("the quick brown fox", 9), ["the quick", "brown fox"])
        self.assertEqual(wrap("", 5), [])
        self.assertEqual(wrap("abcdefghij", 4), ["abcd", "efgh", "ij"])
        self.assertEqual(wrap("a  b   c", 3), ["a b", "c"])
        with self.assertRaises(ValueError):
            wrap("x", 0)

    def test_truncate(self):
        self.assertEqual(truncate("hello", 5), "hello")
        self.assertEqual(truncate("hello world", 8), "hello w…")
        self.assertEqual(truncate("hello", 3, suffix="..."), "...")
        self.assertEqual(truncate("hello", 2, suffix="..."), "..")
        self.assertEqual(truncate("hello world", 8, suffix=""), "hello wo")
        with self.assertRaises(ValueError):
            truncate("x", 0)


if __name__ == "__main__":
    unittest.main()
