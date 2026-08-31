from __future__ import annotations

import unittest

from spectarr_converter.models import OutputFormat
from spectarr_converter.recipes import compile_recipe


class RecipeCompilerTests(unittest.TestCase):
    def test_compiles_backend_recipe_and_job_overrides(self) -> None:
        recipe = compile_recipe(
            {
                "name": "custom-search",
                "converter": "msconvert",
                "output_format": "MGF",
                "parameters": {"mz_precision": 64, "filters": []},
            },
            {"filters": [{"kind": "ms_level", "levels": [2]}], "intensity_precision": 32},
        )
        self.assertEqual(recipe.output_format, OutputFormat.MGF)
        self.assertIn("msLevel 2", recipe.arguments)
        self.assertIn("--inten32", recipe.arguments)

    def test_compiles_contiguous_peak_picking_levels_as_a_range(self) -> None:
        recipe = compile_recipe(
            {
                "name": "search",
                "converter": "msconvert",
                "output_format": "MGF",
                "parameters": {
                    "filters": [{"kind": "peak_picking", "ms_levels": [1, 2]}],
                },
            }
        )
        self.assertIn("peakPicking vendor msLevel=1-2", recipe.arguments)

    def test_rejects_noncontiguous_ms_level_ranges(self) -> None:
        with self.assertRaisesRegex(ValueError, "continuous range"):
            compile_recipe(
                {
                    "name": "search",
                    "converter": "msconvert",
                    "output_format": "MGF",
                    "parameters": {
                        "filters": [{"kind": "peak_picking", "ms_levels": [1, 3]}],
                    },
                }
            )

    def test_supports_mzxml(self) -> None:
        recipe = compile_recipe(
            {"name": "legacy", "converter": "msconvert", "output_format": "mzXML", "parameters": {}}
        )
        self.assertEqual(recipe.output_format, OutputFormat.MZXML)
        self.assertIn("--mzXML", recipe.arguments)

    def test_rejects_untyped_overrides(self) -> None:
        with self.assertRaisesRegex(ValueError, "override"):
            compile_recipe(
                {"name": "safe", "converter": "msconvert", "output_format": "mzML", "parameters": {}},
                {"raw_arguments": ["--exec"]},
            )


if __name__ == "__main__":
    unittest.main()
