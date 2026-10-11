"""Native conversion policies survive API storage and immutable job snapshots."""

import pytest
from pydantic import ValidationError

from spectarr.schemas import ConversionParameters, RecipeCreate

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("definition", [
    {"converter": "unknown"},
    {"converter": "msconvert", "parameters": {"stored_centroids": True}},
    {"converter": "msconvert", "parameters": {"timeout_seconds": 10}},
    {"output_format": "mzXML"},
    {"parameters": {"preset": "sage"}},
    {"parameters": {"compression": "numpress"}},
    {"output_format": "MGF", "parameters": {"compression": "numpress"}},
    {"parameters": {"allow_experimental": "false"}},
    {"parameters": {"max_memory_mb": True}},
    {"parameters": {"filters": [{"kind": "peak_picking"}]}},
    {"parameters": {"filters": [{"kind": "ms_level", "levels": [1, 3]}]}},
    {"parameters": {"filters": [{"kind": "threshold", "threshold_type": "relative", "value": 0.5}]}},
    {"parameters": {"filters": [{"kind": "threshold", "threshold_type": "count", "value": 1.5}]}},
    {"parameters": {"filters": [{"kind": "threshold", "threshold_type": "absolute", "value": -1}]}},
])
async def test_unsupported_recipes_fail_before_storage(client, definition):
    before = (await client.get("/api/v1/recipes")).json()
    response = await client.post("/api/v1/recipes", json={
        "name": "unsupported-native", "converter": "msconvert-rs",
        "output_format": "mzML", **definition,
    })
    assert response.status_code == 422, response.text
    assert (await client.get("/api/v1/recipes")).json() == before


async def test_native_precision_defaults_survive_creation_and_parameter_replacement(client):
    response = await client.post("/api/v1/recipes", json={
        "name": "native-precision", "converter": "msconvert-rs", "output_format": "mzML",
    })
    assert response.status_code == 201, response.text
    recipe = response.json()
    assert recipe["parameters"]["intensity_precision"] == 64
    updated = await client.patch(f"/api/v1/recipes/{recipe['id']}", json={
        "parameters": {"compression_level": 0},
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["parameters"]["intensity_precision"] == 64
    explicit = await client.patch(f"/api/v1/recipes/{recipe['id']}", json={
        "parameters": {"intensity_precision": 32},
    })
    assert explicit.status_code == 200, explicit.text
    assert explicit.json()["parameters"]["intensity_precision"] == 32
    rejected = await client.patch(f"/api/v1/recipes/{recipe['id']}", json={"output_format": "mzXML"})
    assert rejected.status_code == 422
    legacy = await client.post("/api/v1/recipes", json={"name": "legacy-precision", "output_format": "mzML"})
    assert legacy.json()["parameters"]["intensity_precision"] == 32


def test_native_policy_fields_roundtrip_and_limits_are_bounded():
    recipe = RecipeCreate(
        name="native",
        converter="msconvert-rs",
        output_format="mzML",
        parameters={
            "stored_centroids": True,
            "allow_experimental": True,
            "bruker_frames": True,
            "require_retention_time": True,
            "require_mobility": True,
            "max_peaks_per_spectrum": 1000,
            "max_decompressed_bytes": 100000,
            "timeout_seconds": 30,
        },
    )
    data = recipe.model_dump()
    assert data["parameters"]["stored_centroids"] is True
    assert data["parameters"]["require_mobility"] is True
    assert data["parameters"]["timeout_seconds"] == 30
    with pytest.raises(ValidationError):
        ConversionParameters(max_temp_bytes=-1)


async def test_native_recipe_controls_survive_queued_job_snapshot(client, hierarchy):
    source = (
        await client.post(
            f"/api/v1/runs/{hierarchy['run_id']}/artifacts/upload",
            files={"file": ("source.raw", b"vendor bytes")},
            data={"role": "source"},
        )
    ).json()
    response = await client.post(
        "/api/v1/recipes",
        json={
            "name": "native-stored-centroids",
            "converter": "msconvert-rs",
            "output_format": "MGF",
            "parameters": {"stored_centroids": True, "timeout_seconds": 30},
        },
    )
    assert response.status_code == 201, response.text
    recipe = response.json()
    assert recipe["parameters"]["stored_centroids"] is True
    queued = await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/derivatives",
        json={"input_artifact_id": source["id"], "recipe_id": recipe["id"]},
    )
    assert queued.status_code == 202, queued.text
    snapshot = queued.json()["parameters"]["recipe_snapshot"]
    assert snapshot["converter"] == "msconvert-rs"
    assert snapshot["parameters"]["stored_centroids"] is True
    assert snapshot["parameters"]["timeout_seconds"] == 30


def test_native_compression_level_schema_rejects_silent_or_coerced_values():
    for level in (0, 9):
        recipe = RecipeCreate(name="native", converter="msconvert-rs", output_format="mzML", parameters={"compression_level": level})
        assert recipe.parameters.compression_level == level
    for value in (-1, 10, True, "6", 1.5):
        with pytest.raises(ValidationError):
            ConversionParameters(compression_level=value)
    for converter, output, compression in (("msconvert", "mzML", "zlib"), ("msconvert-rs", "MGF", "zlib"), ("msconvert-rs", "MS2", "zlib"), ("msconvert-rs", "mzML", "none")):
        with pytest.raises(ValidationError):
            RecipeCreate(name="invalid", converter=converter, output_format=output, parameters={"compression_level": 0, "compression": compression})


def test_loss_policy_is_strict_and_native_only():
    policy = {"deny": ["precision-reduction"], "require": ["polarity", "precursor-target"]}
    recipe = RecipeCreate(name="native", converter="msconvert-rs", output_format="mzML", parameters={"loss_policy": policy})
    assert recipe.model_dump()["parameters"]["loss_policy"] == policy
    RecipeCreate(name="legacy", output_format="mzML", parameters={"loss_policy": {}})
    with pytest.raises(ValidationError):
        RecipeCreate(name="legacy", output_format="mzML", parameters={"loss_policy": policy})
    for bad in (None, [], "polarity", {"deny": "precision-reduction"}, {"require": [True]}, {"deny": ["unknown"]}, {"allow": []}, {"require": None}):
        with pytest.raises(ValidationError):
            ConversionParameters(loss_policy=bad)
    with pytest.raises(ValidationError):
        ConversionParameters(loss_policies=policy)
    for parameters in ({"allow_approximate_calibration": True}, {"allow_approximate_calibration": "false", "allow_experimental": True}):
        with pytest.raises(ValidationError):
            ConversionParameters(**parameters)
    with pytest.raises(ValidationError):
        RecipeCreate(name="legacy", output_format="mzML", parameters={"allow_experimental": True, "allow_approximate_calibration": True})


async def test_loss_policy_survives_api_patch_and_immutable_snapshot(client, hierarchy):
    policy = {"deny": ["precision-reduction"], "require": ["polarity", "collision-energy"]}
    source = (await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/upload",
        files={"file": ("source.raw", b"vendor bytes")}, data={"role": "source"},
    )).json()
    response = await client.post("/api/v1/recipes", json={
        "name": "native-strict-losses", "converter": "msconvert-rs", "output_format": "mzML",
        "parameters": {"intensity_precision": 64, "loss_policy": policy, "allow_experimental": True, "allow_approximate_calibration": True},
    })
    assert response.status_code == 201, response.text
    recipe = response.json()
    assert recipe["parameters"]["loss_policy"] == policy
    queued = await client.post(f"/api/v1/runs/{hierarchy['run_id']}/derivatives", json={"input_artifact_id": source["id"], "recipe_id": recipe["id"]})
    assert queued.status_code == 202, queued.text
    snapshot = queued.json()["parameters"]["recipe_snapshot"]["parameters"]
    assert snapshot["loss_policy"] == policy
    assert snapshot["allow_approximate_calibration"] is True
    updated_policy = {"require": ["precursor-target"], "deny": []}
    changed = await client.patch(f"/api/v1/recipes/{recipe['id']}", json={"parameters": {"loss_policy": updated_policy}})
    assert changed.status_code == 200, changed.text
    assert changed.json()["parameters"]["loss_policy"] == updated_policy
    assert snapshot["loss_policy"] == policy
    legacy = (await client.post("/api/v1/recipes", json={"name": "legacy-loss-default", "output_format": "mzML"})).json()
    changed = await client.patch(f"/api/v1/recipes/{legacy['id']}", json={"parameters": {"loss_policy": policy}})
    assert changed.status_code == 422
    changed = await client.patch(f"/api/v1/recipes/{recipe['id']}", json={"parameters": {"loss_policy": {"require": ["unknown"]}}})
    assert changed.status_code == 422
    changed = await client.patch(f"/api/v1/recipes/{recipe['id']}", json={"parameters": {"allow_approximate_calibration": True}})
    assert changed.status_code == 422


async def test_compression_level_survives_api_and_update_requires_native_mzml(client):
    response = await client.post("/api/v1/recipes", json={"name": "native-zlib-zero", "converter": "msconvert-rs", "output_format": "mzML", "parameters": {"compression_level": 0}})
    assert response.status_code == 201, response.text
    recipe = response.json()
    assert recipe["parameters"]["compression_level"] == 0
    changed = await client.patch(f"/api/v1/recipes/{recipe['id']}", json={"output_format": "MGF"})
    assert changed.status_code == 422
    default = await client.post("/api/v1/recipes", json={"name": "pwiz-default", "output_format": "mzML"})
    assert default.status_code == 201, default.text
    changed = await client.patch(f"/api/v1/recipes/{default.json()['id']}", json={"parameters": {"compression_level": 0}})
    assert changed.status_code == 422
    changed = await client.patch(f"/api/v1/recipes/{recipe['id']}", json={"parameters": {"compression_level": 9}})
    assert changed.status_code == 200, changed.text
    assert changed.json()["parameters"]["compression_level"] == 9
