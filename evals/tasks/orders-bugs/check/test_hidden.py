# orders-bugs eval fixture: hidden checks, copied in only after the run. Imports are inside the tests so the score
# (pass share) reflects how many of the seeded bugs were fixed.
import unittest
from decimal import Decimal as D

CSV = """ SKU , Name , Price , Weight_kg
a1, USB cable ,2.50,0.1
b2,Desk lamp,45.00,2.4
c3,Bookshelf,120.00,18
d4,usb hub,19.99,0.3
e5,Lamp shade,0.50,0.2

"""


def shop():
    import shop as s
    return s


def setup():
    s = shop()
    return s, s.Catalog.from_csv(CSV), s.Inventory({"a1": 100, "B2": 5, "C3": 2, "D4": 10, "E5": 50})


class Money(unittest.TestCase):
    def test_half_up(self):
        s = shop()
        for x, want in [("0.125", "0.13"), ("-0.125", "-0.13"), ("2.345", "2.35"), ("0.005", "0.01"), ("1.004", "1.00")]:
            with self.subTest(x=x):
                self.assertEqual(s.to_money(D(x)), D(want))

    def test_fmt(self):
        s = shop()
        self.assertEqual([s.fmt(D("1234.5")), s.fmt(D("-3")), s.fmt(D("0")), s.fmt(D("1234567.125"))],
                         ["1,234.50", "-3.00", "0.00", "1,234,567.13"])


class CatalogTests(unittest.TestCase):
    def test_load_and_get(self):
        s, cat, _ = setup()
        self.assertEqual(cat.get(" A1").name, "USB cable")
        self.assertEqual(cat.get("b2").price, D("45.00"))
        with self.assertRaises(KeyError):
            cat.get("nope")

    def test_search_is_case_insensitive(self):
        s, cat, _ = setup()
        self.assertEqual([p.sku for p in cat.search("USB")], ["A1", "D4"])
        self.assertEqual([p.sku for p in cat.search(" LAMP ")], ["B2", "E5"])
        self.assertEqual([p.sku for p in cat.search("Hub")], ["D4"])

    def test_bad_csv(self):
        s = shop()
        for text, line in [("sku,name,price\n", 1), ("sku,name,price,weight_kg\na,b,1\n", 2),
                           ("sku,name,price,weight_kg\na,b,1,1\nA,c,2,1\n", 3), ("sku,name,price,weight_kg\na,b,x,1\n", 2),
                           ("sku,name,price,weight_kg\na,b,-1,1\n", 2), ("sku,name,price,weight_kg\n,b,1,1\n", 2)]:
            with self.subTest(text=text), self.assertRaises(ValueError) as cm:
                s.Catalog.from_csv(text)
            self.assertTrue(str(cm.exception).startswith(f"line {line}:"), str(cm.exception))


class Pricing(unittest.TestCase):
    def test_bulk_starts_at_ten(self):
        from shop.pricing import line_total
        self.assertEqual(line_total(D("2.50"), 9), D("22.50"))
        self.assertEqual(line_total(D("2.50"), 10), D("22.50"))
        self.assertEqual(line_total(D("2.50"), 11), D("24.75"))
        self.assertEqual(line_total(D("0.25"), 10), D("2.25"))

    def test_coupons(self):
        from shop.pricing import coupon_discount
        self.assertEqual(coupon_discount("save5", D("50.00")), D("5.00"))
        self.assertEqual(coupon_discount("SAVE5", D("49.99")), D("0.00"))
        self.assertEqual(coupon_discount(" Pct10 ", D("33.35")), D("3.34"))
        self.assertEqual(coupon_discount("FREESHIP", D("10")), D("0.00"))
        with self.assertRaises(ValueError):
            coupon_discount("BOGUS", D("10"))


class Shipping(unittest.TestCase):
    def test_each_started_kg(self):
        from shop.shipping import shipping_cost
        for w, want in [("1", "4.99"), ("1.01", "9.99"), ("5", "9.99"), ("5.2", "11.49"), ("6.0", "11.49"), ("6.2", "12.99"), ("18", "29.49")]:
            with self.subTest(w=w):
                self.assertEqual(shipping_cost(D(w), D("10")), D(want))

    def test_free(self):
        from shop.shipping import shipping_cost
        self.assertEqual(shipping_cost(D("20"), D("100.00")), D("0"))
        self.assertEqual(shipping_cost(D("20"), D("99.99"), "FREESHIP"), D("0"))


class TaxTests(unittest.TestCase):
    def test_rates(self):
        from shop.tax import tax_for
        self.assertEqual(tax_for(" ca ", D("10.00")), D("0.73"))
        self.assertEqual(tax_for("TX", D("100")), D("6.25"))
        with self.assertRaises(ValueError):
            tax_for("ZZ", D("1"))

    def test_shipping_is_not_taxed(self):
        s, cat, inv = setup()
        o = s.checkout(cat, inv, [("b2", 1)], "CA")
        self.assertEqual((o.subtotal, o.shipping, o.tax, o.total), (D("45.00"), D("9.99"), D("3.26"), D("58.25")))


class InventoryTests(unittest.TestCase):
    def test_all_or_nothing(self):
        s, _, inv = setup()
        with self.assertRaises(s.OutOfStock) as cm:
            inv.reserve([("a1", 5), ("c3", 3), ("b2", 6), ("d4", 1)])
        self.assertEqual(cm.exception.skus, ["B2", "C3"])
        self.assertEqual([inv.available(k) for k in ("a1", "b2", "c3", "d4")], [100, 5, 2, 10])

    def test_repeated_sku_adds_up(self):
        s, _, inv = setup()
        with self.assertRaises(s.OutOfStock):
            inv.reserve([("c3", 1), ("C3", 2)])
        self.assertEqual(inv.available("c3"), 2)
        inv.reserve([("c3", 1), ("C3", 1)])
        self.assertEqual(inv.available("c3"), 0)
        inv.release([("c3", 2)])
        self.assertEqual((inv.available("c3"), inv.available("zz")), (2, 0))

    def test_failed_checkout_reserves_nothing(self):
        s, cat, inv = setup()
        with self.assertRaises(s.OutOfStock):
            s.checkout(cat, inv, [("a1", 1), ("c3", 5)], "ca")
        with self.assertRaises(ValueError):
            s.checkout(cat, inv, [("a1", 1)], "zz")
        with self.assertRaises(ValueError):
            s.checkout(cat, inv, [("a1", 1)], "ca", coupon="nope")
        self.assertEqual(inv.available("a1"), 100)


class Orders(unittest.TestCase):
    def order(self):
        s, cat, inv = setup()
        return s, inv, s.checkout(cat, inv, [("b2", 2)], "ny")

    def test_cannot_cancel_after_shipping(self):
        s, inv, o = self.order()
        o.advance("paid"); o.advance("shipped")
        with self.assertRaises(s.InvalidTransition):
            o.advance("cancelled", inv)
        self.assertEqual((o.status, inv.available("b2")), ("shipped", 3))
        o.advance("delivered")
        with self.assertRaises(s.InvalidTransition):
            o.advance("cancelled", inv)

    def test_cancel_paid_releases_stock(self):
        s, inv, o = self.order()
        o.advance("paid")
        with self.assertRaises(ValueError):
            o.advance("cancelled")
        o.advance("cancelled", inv)
        self.assertEqual((o.history, inv.available("b2")), (["new", "paid", "cancelled"], 5))
        with self.assertRaises(s.InvalidTransition):
            o.advance("paid")


class Checkout(unittest.TestCase):
    def test_merge_lines_and_bulk_on_merged_qty(self):
        s, cat, inv = setup()
        o = s.checkout(cat, inv, [("e5", 6), ("a1", 1), ("E5", 4)], "or")
        self.assertEqual([(l.sku, l.qty, l.amount) for l in o.lines], [("A1", 1, D("2.50")), ("E5", 10, D("4.50"))])
        self.assertEqual((o.subtotal, o.shipping, o.total), (D("7.00"), D("9.99"), D("16.99")))
        self.assertEqual(inv.available("e5"), 40)

    def test_coupon_then_free_shipping_threshold_uses_discounted_subtotal(self):
        s, cat, inv = setup()
        o = s.checkout(cat, inv, [("b2", 2), ("a1", 4)], "tx", coupon="pct10")  # 100.00 -> 90.00 after discount, so shipping is charged (5.2 kg)
        self.assertEqual((o.subtotal, o.discount, o.shipping, o.tax, o.total), (D("100.00"), D("10.00"), D("11.49"), D("5.63"), D("107.12")))
        self.assertEqual(o.coupon, "PCT10")

    def test_bad_carts(self):
        s, cat, inv = setup()
        for cart in ([], [("a1", 0)], [("a1", -1)], [("a1", 1.5)], [("a1", True)]):
            with self.subTest(cart=cart), self.assertRaises(ValueError):
                s.checkout(cat, inv, cart, "ca")
        with self.assertRaises(KeyError):
            s.checkout(cat, inv, [("zz", 1)], "ca")


class Invoice(unittest.TestCase):
    def test_layout(self):
        s, cat, inv = setup()
        o = s.checkout(cat, inv, [("d4", 1), ("a1", 10)], "ca", coupon="save5", order_id="ORD-9")
        want = ("Invoice ORD-9 (new)\n"
                "A1           10       22.50\n"
                "D4            1       19.99\n"
                "Subtotal              42.49\n"
                "Discount               0.00\n"
                "Shipping               9.99\n"
                "Tax                    3.08\n"
                "Total                 55.56\n"
                "Note: Coupon SAVE5 applied\n")
        self.assertEqual(s.render(o), want)

    def test_notes_do_not_leak_between_renders(self):
        s, cat, inv = setup()
        free = s.checkout(cat, inv, [("c3", 1)], "or", order_id="F")
        paid = s.checkout(cat, inv, [("a1", 1)], "or", order_id="P")
        self.assertIn("Note: Free shipping", s.render(free))
        self.assertNotIn("Note:", s.render(paid))
        mine = ["gift"]
        text = s.render(free, mine)
        self.assertEqual(mine, ["gift"])
        self.assertTrue(text.endswith("Note: gift\nNote: Free shipping\n"), text)
        self.assertEqual(s.render(free).count("Free shipping"), 1)


if __name__ == "__main__":
    unittest.main()
