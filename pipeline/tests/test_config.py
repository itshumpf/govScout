"""Tests for weekday PSC/FSC rotation selection (Config.todays_psc_codes)."""

from datetime import date

from govscout.config import WEEKDAY_KEYS, Config, load_config, save_config

# 2024-01-01 was a Monday; the following six dates walk Tue..Sun.
_MONDAY = date(2024, 1, 1)


class TestWeekdayKeys:
    def test_weekday_keys_align_with_date_weekday(self):
        for offset, key in enumerate(WEEKDAY_KEYS):
            day = date(2024, 1, 1 + offset)
            assert day.weekday() == offset
            assert WEEKDAY_KEYS[day.weekday()] == key


class TestTodaysPscCodes:
    def _rotation(self):
        return {
            "mon": ["1111"],
            "tue": ["2222"],
            "wed": ["3333"],
            "thu": ["4444"],
            "fri": ["5555"],
            "sat": ["6666"],
            "sun": ["7777"],
        }

    def test_picks_the_group_for_each_weekday(self):
        config = Config(psc_rotation=self._rotation())
        for offset, expected in enumerate(["1111", "2222", "3333", "4444", "5555", "6666", "7777"]):
            day = date(2024, 1, 1 + offset)
            assert config.todays_psc_codes(today=day) == [expected]

    def test_falls_back_to_psc_codes_when_rotation_empty(self):
        config = Config(psc_codes=["5340", "5962"], psc_rotation={})
        assert config.todays_psc_codes(today=_MONDAY) == ["5340", "5962"]

    def test_missing_weekday_in_rotation_returns_empty_list(self):
        config = Config(psc_codes=["5340"], psc_rotation={"tue": ["2222"]})
        # Monday has no entry in this partial rotation — should not fall back
        # to psc_codes once rotation is configured at all.
        assert config.todays_psc_codes(today=_MONDAY) == []

    def test_defaults_to_today_when_unspecified(self):
        config = Config(psc_rotation=self._rotation())
        result = config.todays_psc_codes()
        assert result in [[v] for v in ("1111", "2222", "3333", "4444", "5555", "6666", "7777")]


class TestConfigRoundtrip:
    def test_psc_rotation_survives_save_and_load(self, tmp_path):
        rotation = {"mon": ["5305", "5306"], "tue": ["3110"]}
        config = Config(psc_codes=["5340"], psc_rotation=rotation, max_pages=3)
        path = save_config(config, tmp_path / "config.json")
        loaded = load_config(path)
        assert loaded.psc_rotation == rotation
        assert loaded.max_pages == 3

    def test_default_config_has_no_rotation(self):
        assert Config().psc_rotation == {}
        assert Config().todays_psc_codes(today=_MONDAY) == []


class TestShippedConfig:
    """Sanity-check the real pipeline/config.json rotation shape."""

    def _load(self):
        import json
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / "config.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_all_seven_weekdays_present_and_nonempty(self):
        data = self._load()
        rotation = data["psc_rotation"]
        assert set(rotation) == set(WEEKDAY_KEYS)
        for day, codes in rotation.items():
            assert codes, f"{day} group is empty"

    def test_codes_are_reasonably_balanced_across_days(self):
        data = self._load()
        sizes = [len(codes) for codes in data["psc_rotation"].values()]
        assert max(sizes) - min(sizes) <= 2

    def test_max_pages_is_modest_for_free_tier_budget(self):
        data = self._load()
        assert data["max_pages"] <= 5

    def test_no_duplicate_codes_within_a_single_day(self):
        data = self._load()
        for day, codes in data["psc_rotation"].items():
            assert len(codes) == len(set(codes)), f"{day} has duplicate codes"
