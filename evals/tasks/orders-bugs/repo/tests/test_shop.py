import unittest
from decimal import Decimal as D

from shop import Catalog, Inventory, InvalidTransition, OutOfStock, checkout, fmt, render, to_money
from shop.pricing import coupon_discount, line_total
from shop.shipping import shipping_cost

CSV = """sku,name,price,weight_kg
a1, usb cable ,2.50,0.1
b2,Desk lamp,45.00,2.4
c3,Bookshelf,120.00,18
"""


class ShopTests(unittest.TestCase):
    def setUp(self):
        self.cat = Catalog.from_csv(CSV)
        self.inv = Inventory({"A1": 100, "B2": 5, "C3": 2})

    def test_money(self):
        self.assertEqual(to_money("2.555"), D("2.56"))
        self.assertEqual(fmt(D("1234.5")), "1,234.50")

    def test_catalog(self):
        self.assertEqual(self.cat.get(" a1 ").name, "usb cable")
        self.assertEqual([p.sku for p in self.cat.search("lamp")], ["B2"])
        with self.assertRaises(KeyError):
            self.cat.get("zz")

    def test_pricing(self):
        self.assertEqual(line_total(D("2.50"), 3), D("7.50"))
        self.assertEqual(line_total(D("2.50"), 20), D("45.00"))
        self.assertEqual(coupon_discount("pct10", D("80.00")), D("8.00"))

    def test_shipping(self):
        self.assertEqual(shipping_cost(D("0.5"), D("20")), D("4.99"))
        self.assertEqual(shipping_cost(D("7"), D("20")), D("12.99"))
        self.assertEqual(shipping_cost(D("7"), D("150")), D("0.00"))

    def test_checkout_and_flow(self):
        o = checkout(self.cat, self.inv, [("b2", 1)], "or")
        self.assertEqual((o.subtotal, o.shipping, o.tax, o.total), (D("45.00"), D("9.99"), D("0.00"), D("54.99")))
        self.assertEqual(self.inv.available("b2"), 4)
        o.advance("paid"); o.advance("shipped"); o.advance("delivered")
        self.assertEqual(o.history, ["new", "paid", "shipped", "delivered"])
        with self.assertRaises(InvalidTransition):
            o.advance("paid")

    def test_out_of_stock(self):
        with self.assertRaises(OutOfStock):
            checkout(self.cat, self.inv, [("c3", 3)], "ca")

    def test_invoice(self):
        o = checkout(self.cat, self.inv, [("a1", 2)], "ny", order_id="ORD-7")
        text = render(o)
        self.assertTrue(text.startswith("Invoice ORD-7 (new)\n"))
        self.assertIn("A1            2        5.00", text)


if __name__ == "__main__":
    unittest.main()
