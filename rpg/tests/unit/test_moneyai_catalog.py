from model_registry import _ensure_curated_apis


def test_moneyai_catalog_self_heals_persistent_catalog():
    catalog = {"apis": [{"id": "vertex_ai", "display_name": "Vertex AI", "models": []}]}

    healed = _ensure_curated_apis(catalog)

    moneyai = next(api for api in healed["apis"] if api["id"] == "moneyai")
    assert moneyai["enabled"] is True
    assert {model["real_name"] for model in moneyai["models"]} == {
        "wanxiang-main",
        "wanxiang-m31-flash",
        "wanxiang-deepseek-flash",
    }


def test_moneyai_catalog_self_heal_is_idempotent():
    first = _ensure_curated_apis({"apis": []})
    second = _ensure_curated_apis(first)

    assert [api["id"] for api in second["apis"]].count("moneyai") == 1
