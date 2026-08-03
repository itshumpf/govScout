"""Unit tests for NSN / part-number / quantity extraction (pure functions)."""

from govscout.extract import extract_nsns, extract_part_numbers, extract_quantities


class TestExtractNsns:
    def test_single_nsn(self):
        assert extract_nsns("Item NSN 5340-01-234-5678 is required.") == ["5340-01-234-5678"]

    def test_multiple_nsns_deduped_in_order(self):
        text = "NSN 5962-01-456-7890 and NSN 1680-01-567-8901; see also 5962-01-456-7890."
        assert extract_nsns(text) == ["5962-01-456-7890", "1680-01-567-8901"]

    def test_rejects_malformed_numbers(self):
        assert extract_nsns("call 555-12-345-6789 now") == []  # 3-digit first group
        assert extract_nsns("id 12345-01-234-5678") == []  # 5-digit first group
        assert extract_nsns("id 5340-01-234-56789") == []  # 5-digit last group

    def test_empty_text(self):
        assert extract_nsns("") == []
        assert extract_nsns("no stock numbers here") == []


class TestExtractPartNumbers:
    def test_pn_slash_label(self):
        assert extract_part_numbers("bracket, P/N BACB10FM4, qty 5") == ["BACB10FM4"]

    def test_pn_label(self):
        assert extract_part_numbers("valve PN MIL-V-22052 in bronze") == ["MIL-V-22052"]

    def test_part_number_label(self):
        assert extract_part_numbers("part number SN74HC04N; microcircuit") == ["SN74HC04N"]

    def test_part_no_label_with_separator(self):
        assert extract_part_numbers("part no. MS90725-8, steel") == ["MS90725-8"]

    def test_case_insensitive_labels(self):
        assert extract_part_numbers("Part Number 4F11001-101A") == ["4F11001-101A"]
        assert extract_part_numbers("p/n xyz123-4") == ["xyz123-4"]

    def test_deduped(self):
        assert extract_part_numbers("P/N ABC-1 and P/N ABC-1") == ["ABC-1"]

    def test_no_part_numbers(self):
        assert extract_part_numbers("services only, no hardware") == []

    def test_pn_prefix_false_positive(self):
        """"PNEUMATIC" starts with "PN" but isn't the P/N label — must not match."""
        assert extract_part_numbers("PNEUMATIC cylinder assembly, qty 5") == []
        assert extract_part_numbers("replace PNEUMATIC line before PN 4F11001-101A") == ["4F11001-101A"]


class TestExtractQuantities:
    def test_qty_label(self):
        assert extract_quantities("QTY 250 EACH") == [250]

    def test_quantity_label(self):
        assert extract_quantities("quantity 48 each") == [48]

    def test_ea_label(self):
        assert extract_quantities("qty 12 ea") == [12]

    def test_each_label(self):
        assert extract_quantities("order each 7 units") == [7]

    def test_comma_grouped(self):
        assert extract_quantities("QTY 10,000 EACH") == [10000]

    def test_multiple_deduped_in_order(self):
        assert extract_quantities("qty 5 ea for line 1; qty 5 ea line 2; qty 9 ea line 3") == [5, 9]

    def test_word_boundary(self):
        assert extract_quantities("beach cleanup and teach-ins") == []

    def test_no_quantities(self):
        assert extract_quantities("no amounts stated") == []
