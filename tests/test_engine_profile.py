import tempfile
import unittest

from unittest.mock import patch

from pipeline.engine_profile import (
    PINNED_PROFILE, UPSTREAM_PROFILE, checkout_revision, normalize_engine_profile, profile_for,
    validate_engine_profile_and_source,
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

    def test_checkout_revision_is_memoized_per_resolved_path(self):
        # A multilingual matrix run calls this once per language against the
        # same checkout; it must not spawn a redundant git subprocess each
        # time for a value that cannot change within one run. A fresh temp
        # directory keeps this test's cache key from colliding with any
        # other test's (the cache is process-wide, keyed by resolved path).
        with tempfile.TemporaryDirectory() as directory:
            with patch("subprocess.check_output", return_value="deadbeef\n") as run:
                first = checkout_revision(directory)
                second = checkout_revision(directory)
            self.assertEqual(first, "deadbeef")
            self.assertEqual(second, "deadbeef")
            run.assert_called_once()

    def test_validate_engine_profile_and_source_rejects_both_mismatches(self):
        # Shared by builder.build() and gs_mod.build_gs(), which each catch
        # this ValueError and re-raise it as their own BuildError.
        with self.assertRaisesRegex(ValueError, "upstream-local.*engine-source.*checkout"):
            validate_engine_profile_and_source(UPSTREAM_PROFILE, None)
        with self.assertRaisesRegex(ValueError, "engine_source.*upstream-local"):
            validate_engine_profile_and_source(PINNED_PROFILE, "/some/checkout")
        self.assertEqual(validate_engine_profile_and_source(PINNED_PROFILE, None), PINNED_PROFILE)
        self.assertEqual(
            validate_engine_profile_and_source(UPSTREAM_PROFILE, "/some/checkout"), UPSTREAM_PROFILE,
        )


if __name__ == "__main__":
    unittest.main()
