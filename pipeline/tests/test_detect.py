"""Unit tests for pricing-signal detection (pure function)."""

from govscout.detect import MAX_SCORE, score_pricing


class TestKeywordFamilies:
    def test_no_signals_scores_zero(self):
        score, flags = score_pricing("The Government requires janitorial services.", [])
        assert score == 0
        assert flags == []

    def test_online_pricing_family(self):
        score, flags = score_pricing("Vendors must use online pricing to submit.", [])
        assert score == 40
        assert flags == ["online_pricing"]

    def test_online_pricing_other_keywords(self):
        for text in ("Please enter your quote via the portal.", "Subject to AUTOMATED EVALUATION."):
            score, flags = score_pricing(text, [])
            assert score == 40
            assert flags == ["online_pricing"]

    def test_historical_pricing_family(self):
        for keyword in ("historical pricing", "price history", "prior award", "last paid"):
            score, flags = score_pricing(f"See {keyword} data for reference.", [])
            assert score == 25
            assert flags == ["historical_pricing"]

    def test_quote_workflow_family(self):
        for keyword in ("request for quotation", "rfq", "quote due"):
            score, flags = score_pricing(f"This {keyword} closes Friday.", [])
            assert score == 20
            assert flags == ["quote_requested"]

    def test_competitive_family(self):
        score, flags = score_pricing("This is a full and open competitive acquisition.", [])
        assert score == 10
        assert flags == ["competitive"]


class TestScoring:
    def test_additive_weights(self):
        score, flags = score_pricing(
            "Request for quotation with online pricing and historical pricing.", []
        )
        assert score == 40 + 25 + 20
        assert flags == ["online_pricing", "historical_pricing", "quote_requested"]

    def test_capped_at_100(self):
        text = (
            "Request for quotation under full and open competition. "
            "Automated evaluation with online pricing; enter your quote. "
            "Historical pricing and prior award data available."
        )
        score, _ = score_pricing(text, ["pricing_worksheet.xlsx"])
        assert 40 + 25 + 20 + 10 + 15 > MAX_SCORE  # sanity: uncapped sum exceeds 100
        assert score == MAX_SCORE

    def test_case_insensitive(self):
        score, flags = score_pricing("ONLINE PRICING IS REQUIRED", [])
        assert score == 40
        assert flags == ["online_pricing"]

    def test_keyword_family_matched_once(self):
        score, _ = score_pricing("online pricing online pricing online pricing", [])
        assert score == 40

    def test_word_boundary_rfq(self):
        score, _ = score_pricing("The srfxq system is unrelated.", [])
        assert score == 0

    def test_empty_and_none_text(self):
        assert score_pricing("", []) == (0, [])
        assert score_pricing(None, []) == (0, [])  # type: ignore[arg-type]


class TestAttachments:
    def test_pricing_attachment_bonus(self):
        score, flags = score_pricing("No signals in the text.", ["pricing_worksheet.xlsx"])
        assert score == 15
        assert flags == ["pricing_attachment"]

    def test_attachment_keywords(self):
        for name in ("Price_Schedule.pdf", "vendor_quote.docx", "cost_proposal.xlsx"):
            _, flags = score_pricing("nothing", [name])
            assert flags == ["pricing_attachment"]

    def test_non_pricing_attachment_ignored(self):
        score, flags = score_pricing("nothing", ["statement_of_work.pdf"])
        assert score == 0
        assert flags == []

    def test_attachment_stacks_with_text(self):
        score, flags = score_pricing("This RFQ closes soon.", ["pricing.xlsx"])
        assert score == 20 + 15
        assert flags == ["quote_requested", "pricing_attachment"]
