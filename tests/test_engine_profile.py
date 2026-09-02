import unittest

from pipeline.engine_profile import (
    PINNED_PROFILE, UPSTREAM_PROFILE, normalize_engine_profile, profile_for,
)
from pipeline.roms import GS_REQUIRED_TSV, gs_required_tsv


class EngineProfileTests(unittest.TestCase):
    def test_default_is_the_published_pinned_profile(self):
        self.assertEqual(PINNED_PROFILE, "pinned")
        self.assertEqual(normalize_engine_profile(None), PINNED_PROFILE)
        self.assertNotIn("gs_rom_text.tsv", gs_required_tsv())
        self.assertNotIn("gs_types.tsv", gs_required_tsv())
        self.assertIn("gs_rom_text.tsv", gs_required_tsv(UPSTREAM_PROFILE))
        self.assertIn("gs_types.tsv", gs_required_tsv(UPSTREAM_PROFILE))

    def test_unknown_and_versioned_profiles_are_rejected(self):
        for value in (
            "local-working-tree-without-explicit-profile", "v0.2.41",
            "default", "upstream", "local",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_engine_profile(value)

    def test_pinned_and_upstream_paths_keep_their_capability_boundaries(self):
        self.assertEqual(normalize_engine_profile("pinned"), PINNED_PROFILE)
        self.assertEqual(normalize_engine_profile("upstream-local"), UPSTREAM_PROFILE)
        self.assertFalse(profile_for(PINNED_PROFILE).supports_rom_text)
        self.assertTrue(profile_for(UPSTREAM_PROFILE).supports_rom_text)
        # Strings()/Strings.source() matching works on both profiles --
        # match_gs_engine_strings() verifies a pinned checkout against the
        # pin itself instead of refusing it outright.
        self.assertTrue(profile_for(PINNED_PROFILE).supports_engine_strings)
        self.assertTrue(profile_for(UPSTREAM_PROFILE).supports_engine_strings)
        self.assertIn("gs_rom_text.tsv", GS_REQUIRED_TSV)


if __name__ == "__main__":
    unittest.main()
