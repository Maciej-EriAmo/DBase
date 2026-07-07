"""Testy parsera składni KarminQL."""

import unittest

from cynober_query_engine import KarminParser, CreateBubbleNode, ConditionNode, AggregateNode


class TestKarminParser(unittest.TestCase):
    def setUp(self):
        self.parser = KarminParser()

    def test_parse_create_bubble(self):
        nodes = self.parser.parse('UTRWAL "Encja"')
        self.assertEqual(len(nodes), 1)
        self.assertIsInstance(nodes[0][2], CreateBubbleNode)
        self.assertEqual(nodes[0][2].name, "Encja")

    def test_parse_condition_with_and(self):
        nodes = self.parser.parse('ZNAJDŹ GDZIE "Typ" = "A" ORAZ "RAM" > 10')
        node = nodes[0][2]
        self.assertIsInstance(node, ConditionNode)
        self.assertEqual(node.logic, "ORAZ")
        self.assertEqual(node.cond1[0], "Typ")
        self.assertEqual(node.cond2[0], "RAM")
        self.assertEqual(node.cond2[1], ">")

    def test_parse_gte_operator_not_split(self):
        """>= musi być rozpoznany jako jeden operator, nie > + =."""
        nodes = self.parser.parse('ZNAJDŹ GDZIE "RAM" >= 500')
        node = nodes[0][2]
        self.assertEqual(node.cond1[1], ">=")
        self.assertEqual(node.cond1[2], "500")

    def test_parse_aggregate_with_group_by(self):
        nodes = self.parser.parse('SUMA "RAM" GDZIE "Typ" = "Serwer" POGRUPUJ "Typ"')
        node = nodes[0][2]
        self.assertIsInstance(node, AggregateNode)
        self.assertEqual(node.action, "SUMA")
        self.assertEqual(node.group_by, "Typ")

    def test_rejects_unknown_syntax(self):
        with self.assertRaises(SyntaxError):
            self.parser.parse("TO NIE JEST KOMENDA")

    def test_ignores_comments_and_blank_lines(self):
        script = """
        # komentarz
        UTRWAL "A"

        UTRWAL "B"
        """
        nodes = self.parser.parse(script)
        self.assertEqual(len(nodes), 2)


if __name__ == "__main__":
    unittest.main()