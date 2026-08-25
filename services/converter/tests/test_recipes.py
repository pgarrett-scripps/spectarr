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
