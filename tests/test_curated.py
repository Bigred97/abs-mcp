import pytest

from abs_mcp import curated


@pytest.fixture(autouse=True)
def reset_registry():
    curated.reset_registry()
    yield
    curated.reset_registry()


def test_list_ids_returns_curated_dataflows():
    ids = curated.list_ids()
    assert set(ids) == {
        "LF", "CPI", "ABS_ANNUAL_ERP_ASGS2021", "BA_GCCSA", "LEND_HOUSING",
        "WPI", "JV", "ANA_AGG", "AWE", "ERP_Q",
        "C21_G02_SA2", "C21_G02_POA", "RT",
    }


def test_get_lf_loads_dimensions():
    lf = curated.get("LF")
    assert lf is not None
    assert lf.name == "Labour Force"
    assert "measure" in lf.dimensions
    assert "region" in lf.dimensions
    # Hidden dims present but flagged
    assert lf.dimensions["age"].hidden is True
    assert lf.dimensions["age"].default == "1599"


def test_get_is_case_insensitive():
    assert curated.get("lf") is not None
    assert curated.get("LF") is not None


def test_get_returns_none_for_unknown():
    assert curated.get("NOPE_NOT_REAL") is None


def test_translate_filters_lf_unemployment_nsw():
    """Brief-required: curated.LF resolves plain-English values to SDMX codes."""
    lf = curated.get("LF")
    sdmx = curated.translate_filters(lf, {"region": "nsw", "measure": "unemployment_rate"})
    assert sdmx == {"REGION": ["1"], "MEASURE": ["M13"]}


def test_translate_filters_accepts_list_for_multi_value():
    lf = curated.get("LF")
    sdmx = curated.translate_filters(lf, {"region": ["nsw", "vic"]})
    assert sdmx == {"REGION": ["1", "2"]}


def test_translate_filters_unknown_dim_raises_with_suggestion():
    lf = curated.get("LF")
    with pytest.raises(ValueError, match="Unknown filter 'state'"):
        curated.translate_filters(lf, {"state": "nsw"})


def test_translate_filters_unknown_value_raises_with_suggestion():
    lf = curated.get("LF")
    # 'sydney' is a city, not a state — should fall through to suggestion logic.
    with pytest.raises(ValueError, match="Unknown value 'sydney'"):
        curated.translate_filters(lf, {"region": "sydney"})


# ---- aus-identity cross-source normalisation on `region` ----


def test_region_accepts_full_state_name():
    """`region='Queensland'` should resolve to QLD → curated key 'qld'."""
    lf = curated.get("LF")
    sdmx = curated.translate_filters(lf, {"region": "Queensland"})
    assert sdmx == {"REGION": ["3"]}  # QLD's SDMX code


def test_region_accepts_uppercase_short_code():
    """`region='NSW'` (canonical) maps to lowercase curated key 'nsw'."""
    lf = curated.get("LF")
    sdmx = curated.translate_filters(lf, {"region": "NSW"})
    assert sdmx == {"REGION": ["1"]}


def test_region_accepts_full_name_with_spaces():
    """`region='New South Wales'` resolves to NSW → 'nsw' → SDMX '1'."""
    lf = curated.get("LF")
    sdmx = curated.translate_filters(lf, {"region": "New South Wales"})
    assert sdmx == {"REGION": ["1"]}


def test_region_accepts_iso_3166_form():
    """`region='AU-VIC'` resolves to VIC → SDMX '2'."""
    lf = curated.get("LF")
    sdmx = curated.translate_filters(lf, {"region": "AU-VIC"})
    assert sdmx == {"REGION": ["2"]}


def test_region_accepts_postcode_string():
    """`region='2000'` (Sydney CBD postcode) routes to NSW → SDMX '1'."""
    lf = curated.get("LF")
    sdmx = curated.translate_filters(lf, {"region": "2000"})
    assert sdmx == {"REGION": ["1"]}


def test_region_postcode_in_act_routes_correctly():
    """`region='2600'` (Parliament House) resolves to ACT, not NSW."""
    lf = curated.get("LF")
    sdmx = curated.translate_filters(lf, {"region": "2600"})
    assert sdmx == {"REGION": ["8"]}  # ACT SDMX


def test_region_unknown_state_still_raises():
    """Inputs that aren't a state, postcode, or curated key still fail."""
    lf = curated.get("LF")
    with pytest.raises(ValueError, match="Unknown value 'narnia'"):
        curated.translate_filters(lf, {"region": "narnia"})


def test_translate_filters_accepts_raw_sdmx_code_as_escape_hatch():
    lf = curated.get("LF")
    sdmx = curated.translate_filters(lf, {"region": "1"})  # raw SDMX code
    assert sdmx == {"REGION": ["1"]}


def test_apply_defaults_injects_hidden_dim_values():
    lf = curated.get("LF")
    user_filters = {"REGION": ["1"], "MEASURE": ["M13"]}
    full = curated.apply_defaults(lf, user_filters)
    # Hidden dims got their defaults
    assert full["AGE"] == ["1599"]
    assert full["TSEST"] == ["20"]
    assert full["FREQ"] == ["M"]
    # User filters preserved
    assert full["REGION"] == ["1"]
    assert full["MEASURE"] == ["M13"]


def test_apply_defaults_does_not_overwrite_user_value():
    lf = curated.get("LF")
    full = curated.apply_defaults(lf, {"AGE": ["1564"]})  # user overrode default age
    assert full["AGE"] == ["1564"]


def test_build_sdmx_key_uses_dim_order_and_skips_time():
    """LF dimension order: MEASURE.SEX.AGE.TSEST.REGION.FREQ (TIME_PERIOD skipped)."""
    order = ["MEASURE", "SEX", "AGE", "TSEST", "REGION", "FREQ", "TIME_PERIOD"]
    filters = {
        "MEASURE": ["M13"],
        "SEX": ["3"],
        "AGE": ["1599"],
        "TSEST": ["20"],
        "REGION": ["1"],
        "FREQ": ["M"],
    }
    key = curated.build_sdmx_key(order, filters)
    assert key == "M13.3.1599.20.1.M"


def test_build_sdmx_key_leaves_unspecified_dims_blank():
    order = ["A", "B", "C"]
    key = curated.build_sdmx_key(order, {"A": ["x"], "C": ["z"]})
    assert key == "x..z"


def test_build_sdmx_key_joins_multi_value_with_plus():
    order = ["A"]
    key = curated.build_sdmx_key(order, {"A": ["x", "y"]})
    assert key == "x+y"


def test_all_curated_dataflows_load_without_error():
    for ds_id in curated.list_ids():
        df = curated.get(ds_id)
        assert df is not None
        assert df.id == ds_id
        assert df.dimensions  # at least one dim


def test_translate_filters_strips_whitespace_on_values():
    """LLM agents commonly pass padded strings; whitespace is never meaningful."""
    lf = curated.get("LF")
    sdmx = curated.translate_filters(lf, {"region": " nsw "})
    assert sdmx == {"REGION": ["1"]}


def test_translate_filters_strips_whitespace_inside_list():
    lf = curated.get("LF")
    sdmx = curated.translate_filters(lf, {"region": ["  nsw", "vic  "]})
    assert sdmx == {"REGION": ["1", "2"]}


def test_translate_filters_rejects_empty_list_with_hint():
    """An empty list filter used to silently expand to 'all values'."""
    lf = curated.get("LF")
    with pytest.raises(ValueError, match="empty list"):
        curated.translate_filters(lf, {"region": []})


def test_translate_filters_rejects_empty_value():
    lf = curated.get("LF")
    with pytest.raises(ValueError, match="empty value"):
        curated.translate_filters(lf, {"region": "   "})


def test_translate_filters_rejects_hidden_dim_with_clean_error():
    """Hidden dims have no user-facing value map. Passing one used to produce
    'Try one of: ' (empty list). Now: explicit auto-managed message."""
    lf = curated.get("LF")
    with pytest.raises(ValueError, match="auto-managed"):
        curated.translate_filters(lf, {"age": "15-19"})


def test_lend_housing_yaml_declares_quarterly():
    """LEND_HOUSING was misdeclared 'monthly' but FREQ default is Q; ABS
    publishes Lending Indicators quarterly."""
    cd = curated.get("LEND_HOUSING")
    assert cd.update_frequency == "quarterly"
    assert "quarterly" in cd.description.lower()


def test_apply_defaults_still_injects_hidden_dim_defaults():
    """Regression: hidden dims are no longer user-passable, but their defaults
    must still be auto-applied so curated queries hit valid SDMX series."""
    lf = curated.get("LF")
    full = curated.apply_defaults(lf, {"REGION": ["1"], "MEASURE": ["M13"]})
    assert full["AGE"] == ["1599"]
    assert full["FREQ"] == ["M"]
    assert full["TSEST"] == ["20"]


# ---------- permissive dim escape hatch (ASGS sub-state codes) ----------

def test_asgs_region_dim_is_permissive():
    """The YAML promises 2,985 sub-state codes work — the YAML must declare
    the dim permissive for that promise to hold."""
    cd = curated.get("ABS_ANNUAL_ERP_ASGS2021")
    assert cd.dimensions["region"].permissive is True


def test_translate_filters_permissive_accepts_sa2_numeric_code():
    """SA2 codes are nine-digit numerics. They're not in the curated value
    map (only 14 entries) but the permissive flag lets them through."""
    cd = curated.get("ABS_ANNUAL_ERP_ASGS2021")
    sdmx = curated.translate_filters(cd, {"region": "101021010", "region_type": "sa2"})
    assert sdmx["ASGS_2021"] == ["101021010"]


def test_translate_filters_permissive_accepts_sa4_short_numeric():
    cd = curated.get("ABS_ANNUAL_ERP_ASGS2021")
    sdmx = curated.translate_filters(cd, {"region": "117", "region_type": "sa4"})
    assert sdmx["ASGS_2021"] == ["117"]


def test_translate_filters_permissive_normalises_uppercase_state_code():
    """'NSW' (uppercase) is now normalised via aus_identity to the curated
    key 'nsw'. Permissive ASGS dim still gets the canonical SDMX '1'."""
    cd = curated.get("ABS_ANNUAL_ERP_ASGS2021")
    sdmx = curated.translate_filters(cd, {"region": "NSW"})
    assert sdmx["ASGS_2021"] == ["1"]


def test_translate_filters_permissive_normalises_full_state_name():
    """'Queensland' (full name) is now normalised via aus_identity to 'qld'."""
    cd = curated.get("ABS_ANNUAL_ERP_ASGS2021")
    sdmx = curated.translate_filters(cd, {"region": "Queensland"})
    assert sdmx["ASGS_2021"] == ["3"]


def test_translate_filters_permissive_rejects_real_typo():
    """Non-state non-postcode non-curated values still raise with curated hint."""
    cd = curated.get("ABS_ANNUAL_ERP_ASGS2021")
    with pytest.raises(ValueError, match="Try one of"):
        curated.translate_filters(cd, {"region": "narnia"})


def test_translate_filters_permissive_rejects_injection_chars():
    """URL-injection guard still applies on the permissive escape hatch.
    A code-like value with '?' or '&' must NOT slip through."""
    cd = curated.get("ABS_ANNUAL_ERP_ASGS2021")
    with pytest.raises(ValueError, match="Try one of"):
        curated.translate_filters(cd, {"region": "101021010?inject=1"})


def test_translate_filters_non_permissive_dim_still_rejects_unknown():
    """Other curated dataflows (LF, CPI etc.) are not permissive — passing
    a code-like value that's not in the YAML must still error with the
    curated hint."""
    lf = curated.get("LF")
    assert lf.dimensions["region"].permissive is False
    with pytest.raises(ValueError, match="Try one of"):
        curated.translate_filters(lf, {"region": "999"})


def test_translate_filters_curated_key_still_resolves_on_permissive_dim():
    """The escape hatch must not break the curated-key path: 'nsw' still
    resolves to '1'."""
    cd = curated.get("ABS_ANNUAL_ERP_ASGS2021")
    sdmx = curated.translate_filters(cd, {"region": "nsw"})
    assert sdmx["ASGS_2021"] == ["1"]


def test_translate_filters_known_sdmx_code_still_resolves_on_permissive_dim():
    """The escape hatch for known codes ('AUS', '1GSYD') must still work even
    though the permissive path could also accept them."""
    cd = curated.get("ABS_ANNUAL_ERP_ASGS2021")
    sdmx = curated.translate_filters(cd, {"region": "1GSYD"})
    assert sdmx["ASGS_2021"] == ["1GSYD"]


# ---------- 0.2.14: suggestion-style ValueError messages (CLAUDE.md dim #5) ----------

def test_unknown_filter_message_points_to_describe_dataset():
    """Regression: every Unknown-filter raise must point at describe_dataset(id)
    so the LLM has a self-correction path, not just a rejection notice."""
    lf = curated.get("LF")
    with pytest.raises(ValueError, match=r"describe_dataset\('LF'\)"):
        curated.translate_filters(lf, {"state": "nsw"})


def test_unknown_filter_message_includes_did_you_mean_for_typo():
    """Regression: an obvious typo ('measur' → 'measure') must trigger a
    'Did you mean X?' hint via difflib."""
    lf = curated.get("LF")
    with pytest.raises(ValueError, match=r"Did you mean 'measure'\?"):
        curated.translate_filters(lf, {"measur": "unemployment_rate"})


def test_unknown_value_message_points_to_describe_dataset():
    """Regression: every Unknown-value raise must point at describe_dataset(id)."""
    lf = curated.get("LF")
    with pytest.raises(ValueError, match=r"describe_dataset\('LF'\)"):
        # 'narnia' is not a state, not a postcode, not a curated key.
        curated.translate_filters(lf, {"region": "narnia"})


def test_unknown_value_message_includes_did_you_mean_for_typo():
    """Regression: 'unemploymnt_rate' (typo of 'unemployment_rate') must
    surface a 'Did you mean X?' hint."""
    lf = curated.get("LF")
    with pytest.raises(ValueError, match=r"Did you mean 'unemployment_rate'\?"):
        curated.translate_filters(lf, {"measure": "unemploymnt_rate"})


def test_hidden_dim_filter_message_points_to_describe_dataset():
    """The auto-managed-dim path also gets the describe_dataset pointer so
    LLMs can discover the visible filter surface."""
    lf = curated.get("LF")
    with pytest.raises(ValueError, match=r"describe_dataset\('LF'\)"):
        curated.translate_filters(lf, {"age": "15-19"})
