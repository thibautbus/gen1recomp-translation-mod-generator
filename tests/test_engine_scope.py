import tempfile
import subprocess
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.engine_scope import (
    MANIFEST_PATH, SCOPE_PATH, classify_callsites, classify_catalog,
    forced_dynamic_keys, iter_callsites, load_manifest, load_scope, validate_catalog_universe,
    verified_source,
)
from pipeline.dependencies import _tree_digest


class EngineScopeTests(unittest.TestCase):
    def _load_mutated(self, source, loader, mutate):
        data = json.loads(source.read_text(encoding="utf-8"))
        mutate(data)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mutated.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError): loader(path)

    def _load_mutated_manifest(self, mutate):
        self._load_mutated(MANIFEST_PATH, load_manifest, mutate)

    def _load_mutated_scope(self, mutate):
        self._load_mutated(SCOPE_PATH, load_scope, mutate)

    def test_manifest_rejects_schema_revision_path_and_fields(self):
        for mutate in [lambda d: d.pop("schema"), lambda d: d.update(schema="wrong"), lambda d: d.update(version=2), lambda d: d.update(gen1recomp_revision="abc"), lambda d: d.update(gen1recomp_revision="A" * 40), lambda d: d.update(gen1recomp_revision="g" * 40), lambda d: d.update(source_subdir="/tmp"), lambda d: d.update(source_subdir="../src"), lambda d: d.update(source_subdir="other"), lambda d: d.update(extra=1)]:
            self._load_mutated_manifest(mutate)

    def test_scope_rejects_schema_version_and_fields(self):
        for mutate in [lambda d: d.pop("schema"), lambda d: d.update(schema="wrong"), lambda d: d.update(classifier_version=5), lambda d: d.update(extra=1)]:
            self._load_mutated_scope(mutate)

    def test_scope_rejects_list_types_duplicates_and_overlaps(self):
        for field in ("rby_paths", "rby_ui_modules", "ui_review_modules", "link_modules", "modern_ui_modules", "rby_ui_keys", "link_ui_keys", "modern_ui_keys"):
            self._load_mutated_scope(lambda d, f=field: d.update({f: "bad"}))
            self._load_mutated_scope(lambda d, f=field: d[f].append(d[f][0]))
        self._load_mutated_scope(lambda d: d["rby_ui_modules"].__setitem__(0, "NoSuffix"))
        self._load_mutated_scope(lambda d: d["rby_ui_modules"].append(d["ui_review_modules"][0]))
        self._load_mutated_scope(lambda d: d["rby_ui_keys"].append(d["link_ui_keys"][0]))

    def test_manifest_rejects_invalid_dynamic_entries(self):
        mutations = (
            lambda d: d.update(forced_dynamic_keys=[]),
            lambda d: d.update(forced_dynamic_keys={"NAME": {}}),
            lambda d: d.update(engine_dynamic_values=[]),
            lambda d: d["engine_dynamic_values"]["FAST"].update(provenance="wrong"),
            lambda d: d["engine_dynamic_values"]["FAST"].update(extra=True),
        )
        for mutate in mutations:
            self._load_mutated_manifest(mutate)

    def test_scope_overrides_are_versioned_and_strict(self):
        scope = load_scope()
        self.assertEqual(scope["classifier_version"], 4)
        self.assertEqual(forced_dynamic_keys(scope), set())
        self.assertIn("_OakSpeechText2A", scope["key_scope_overrides"])
        self.assertEqual(
            scope["key_scope_overrides"]["_OakSpeechText2A"],
            {"category": "rby", "eligibility": "ineligible", "reason": "covered-by-rom", "engine_empty": True},
        )
        for key_set in ("rby_ui_keys", "link_ui_keys", "modern_ui_keys"):
            self.assertNotIn("_OakSpeechText2A", scope[key_set])
        self.assertNotIn("But every BOX\nis full!", scope["key_scope_overrides"])
        for key in ("Crammed full of\nPOKéMON books!", "POKéDEX comp-\nletion is:\f{NUM:hDexRatingNumMonsSeen} POKéMON seen\n{NUM:hDexRatingNumMonsOwned} POKéMON owned\fPROF.OAK's\nRating:", "{RIVAL}: Yeah! Am\nI great or what?", "Welcome to our\nPOKéMON CENTER!", "Your POKéMON are\nfighting fit!", "No SURFing here!", "Nothing to CUT!", "Keep it up!", "POKéDEX Rating{COLON}", "_OakSpeechText2A", "{RAM}\nPOKéMON GYM\nLEADER: {RAM}", "I like shorts!\nThey're comfy and\neasy to wear!", "%s is\ntaken out.\x0bGot %s.", "%s's\nhurt by poison!", "%s's\nhurt by the burn!"):
            self.assertEqual(scope["key_scope_overrides"][key]["reason"], "covered-by-rom")
            self.assertTrue(scope["key_scope_overrides"][key]["engine_empty"])
        self.assertIn("Printed %s's\ndata!\fSaved as\n%s\vin the save\nfolder.", scope["key_scope_overrides"])
        self.assertIn("Printed BOX %d!\fSaved as\n%s\vin the save\nfolder.", scope["key_scope_overrides"])
        for mutate in (
            lambda d: d["key_scope_overrides"].update({"x": {"category": "rby", "eligibility": "ineligible", "reason": "bad"}}),
            lambda d: d["key_scope_overrides"].update({"x": {"category": "rby", "eligibility": "ineligible"}}),
            lambda d: d["key_scope_overrides"].update({"x": {"category": "rby", "eligibility": "ineligible", "reason": "dead", "extra": 1}}),
            lambda d: d["key_scope_overrides"].update({"x": {"category": "bogus", "eligibility": "ineligible", "reason": "dead"}}),
            lambda d: d.update(key_scope_overrides=[]),
        ):
            self._load_mutated_scope(mutate)

    def test_obsolete_forced_keys_are_now_discovered_as_literals(self):
        scope = load_scope()
        self.assertEqual(forced_dynamic_keys(scope), set())
        checkout = Path(".cache/dependencies/gen1recomp")
        if not checkout.is_dir():
            self.skipTest("pinned Gen1Recomp checkout is unavailable")
        callsites = list(iter_callsites(checkout))
        source_keys = {row.get("source") for row in callsites}
        stat_keys = {"NAME", "ATTACK", "DEFENSE", "SPEED", "SPECIAL"}
        self.assertEqual(stat_keys <= source_keys, True)
        # A future engine_scope.json/classify_catalog change that misclassifies
        # these five stat-name keys would otherwise ship undetected: literal
        # discovery alone doesn't prove they're still eligible for translation.
        classified = classify_catalog(stat_keys, callsites, scope)
        for key in stat_keys:
            self.assertEqual(classified[key]["eligibility"], "eligible", key)
            self.assertNotEqual(classified[key].get("provenance"), "forced_dynamic", key)

    def test_engine_dynamic_values_keep_their_scope(self):
        result = classify_catalog(["FAST", "balanced", "ADAPTIVE"], [], load_scope())
        self.assertEqual(result["FAST"]["provenance"], "engine_dynamic")
        self.assertEqual(result["FAST"]["eligibility"], "ineligible")
        self.assertEqual(result["balanced"]["category"], "mixed")
        self.assertEqual(result["ADAPTIVE"]["category"], "modern")
        self.assertEqual(result["ADAPTIVE"]["eligibility"], "ineligible")
        self.assertEqual(result["ADAPTIVE"]["provenance"], "engine_dynamic")
        self.assertIn("src/ui/OptionsMenu.lua:507", result["ADAPTIVE"]["callsite"])
        self.assertIn("src/ui/gen2/OptionsMenu.lua:411", result["ADAPTIVE"]["callsite"])

    def test_finite_dynamic_option_and_time_domains_are_manifested(self):
        scope = load_scope()
        expected = {
            "LIGHT", "NORMAL", "STRONG", "PALETTE", "SKIN", "CENTER", "UPPER", "TOP",
            "AM", "PM", "FILL ", "DEVICE", "DD-MM-YYYY", "MM-DD-YYYY", "YYYY-MM-DD",
            "24 HOUR", "12 HOUR",
        }
        self.assertEqual(expected <= set(scope["engine_dynamic_values"]), True)
        self.assertNotIn("HEAVY", scope["engine_dynamic_values"])
        result = classify_catalog(expected, [], scope)
        for key in expected:
            self.assertEqual(result[key]["provenance"], "engine_dynamic")
            self.assertEqual(result[key]["eligibility"], "ineligible")

    def test_reporting_scope_excludes_source_only_keys(self):
        from pipeline.mod import _catalog_scope
        classified = {
            "catalog": {"category": "rby", "eligibility": "eligible"},
            "source-only": {"category": "modern", "eligibility": "ineligible"},
        }
        scoped = _catalog_scope(classified, {"catalog"})
        self.assertEqual(set(scoped), {"catalog"})
        self.assertEqual(sum(info["eligibility"] == "eligible" for info in scoped.values()), 1)
        self.assertEqual(sum(info["eligibility"] == "ineligible" for info in scoped.values()), 0)

    def test_scope_override_wins_after_raw_callsite_classification(self):
        scope = load_scope()
        scope["key_scope_overrides"] = {
            "x": {"category": "modern", "eligibility": "ineligible", "reason": "diagnostic"}
        }
        result = classify_callsites([{"source": "x", "path": "battle/BattleState.lua", "line": 1}], scope)
        self.assertEqual(result["x"]["category"], "modern")
        self.assertEqual(result["x"]["eligibility"], "ineligible")
        self.assertEqual(result["x"]["reason"], "diagnostic")
        self.assertEqual(result["x"]["raw_category"], "rby")
        self.assertEqual(result["x"]["callsites"][0]["category"], "rby")
        self.assertEqual(result["x"]["raw_callsites"], result["x"]["callsites"])

    def test_scope_override_applies_to_catalog_keys_without_callsites(self):
        scope = load_scope()
        result = classify_catalog(["Creatures inc.", "_OakSpeechText2A", "But every BOX\nis full!"], [], scope)
        self.assertEqual(result["Creatures inc."]["eligibility"], "ineligible")
        self.assertEqual(result["Creatures inc."]["reason"], "defensive")
        self.assertEqual(result["Creatures inc."]["raw_eligibility"], "review")
        self.assertEqual(result["_OakSpeechText2A"]["eligibility"], "ineligible")
        self.assertEqual(result["_OakSpeechText2A"]["reason"], "covered-by-rom")
        self.assertEqual(result["_OakSpeechText2A"]["engine_empty"], True)
        self.assertEqual(result["But every BOX\nis full!"]["eligibility"], "review")

    def _git_fixture(self):
        tmp = tempfile.TemporaryDirectory(); root = Path(tmp.name)
        (root / "src" / "battle").mkdir(parents=True)
        (root / "src" / "battle" / "One.lua").write_text('Strings("one")', encoding="utf-8")
        (root / "tools").mkdir()
        (root / "tools" / "extract.lua").write_text("return true\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "src", "tools"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
        revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        scope = load_scope(); scope["gen1recomp_revision"] = revision
        return tmp, root, scope

    def test_verified_source_accepts_clean_root_and_src_but_rejects_dirty_src(self):
        tmp, root, scope = self._git_fixture()
        try:
            self.assertEqual(verified_source(root, scope)[0], root / "src")
            self.assertEqual(verified_source(root / "src", scope)[0], root / "src")
            (root / "src" / "battle" / "Dirty.lua").write_text('Strings("dirty")', encoding="utf-8")
            with self.assertRaises(ValueError): verified_source(root, scope)
        finally: tmp.cleanup()

    def test_verified_source_rejects_dirty_tools_used_by_the_builder(self):
        tmp, root, scope = self._git_fixture()
        try:
            self.assertEqual(verified_source(root, scope)[0], root / "src")
            (root / "tools" / "extract.lua").write_text("return false\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                verified_source(root, scope)
        finally:
            tmp.cleanup()

    def test_verified_source_accepts_archive_root_and_src_and_rejects_spoofed_tree_pin(self):
        scope = load_scope()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "gen1recomp"
            source = root / "src"
            (source / "battle").mkdir(parents=True)
            (source / "battle" / "One.lua").write_text('Strings("one")', encoding="utf-8")
            trusted_tree = _tree_digest(source)
            config = {
                "gen1recomp": {
                    "archive_url": "https://example.invalid/gen1recomp.zip",
                    "archive_sha256": "a" * 64,
                    "archive_tree_sha256": trusted_tree,
                }
            }
            marker = root / ".archive-marker.json"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps({
                "revision": scope["gen1recomp_revision"],
                "url": config["gen1recomp"]["archive_url"],
                "sha256": config["gen1recomp"]["archive_sha256"],
                "tree_sha256": trusted_tree,
            }), encoding="utf-8")

            with patch("pipeline.project.project_config", return_value=config):
                from_root = verified_source(root, scope)
                from_src = verified_source(source, scope)
                self.assertEqual(from_root, from_src)
                self.assertEqual(from_root, (source, root, scope["gen1recomp_revision"]))

                (source / "battle" / "One.lua").write_text('Strings("tampered")', encoding="utf-8")
                marker_data = json.loads(marker.read_text(encoding="utf-8"))
                marker_data["tree_sha256"] = _tree_digest(source)
                marker.write_text(json.dumps(marker_data), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "archive source tree digest mismatch"):
                    verified_source(root, scope)

    def test_manifest_and_lua_suffix_rules(self):
        scope = load_scope()
        self.assertEqual(scope["gen1recomp_revision"], "6afeca1aebdebbd2f5bed422848c00ec7ec05dae")
        self.assertEqual(
            classify_callsites([{"source": "x", "path": "ui/BagMenu.lua", "line": 1}])["x"]["eligibility"],
            "eligible",
        )
        self.assertEqual(
            classify_callsites([{"source": "x", "path": "ui/BagMenu", "line": 1}])["x"]["eligibility"],
            "review",
        )

    def test_v0241_manifest_callsites_exist_in_pinned_checkout(self):
        manifest = load_manifest()
        checkout = Path(".cache/dependencies/gen1recomp")
        if not checkout.is_dir():
            self.skipTest("pinned Gen1Recomp checkout is unavailable")
        for group in ("forced_dynamic_keys", "engine_dynamic_values"):
            for key, entry in manifest[group].items():
                references = re.findall(r"(src/[^ :()]+\.lua):(\d+)(?:-(\d+))?", entry["callsite"])
                self.assertTrue(references, key)
                for relative, first, last in references:
                    source = checkout / relative
                    self.assertTrue(source.is_file(), f"{key}: {relative}")
                    lines = source.read_text(encoding="utf-8").splitlines()
                    start, end = int(first), int(last or first)
                    self.assertGreaterEqual(start, 1, key)
                    self.assertLessEqual(start, len(lines), f"{key}: {relative}:{start}")
                    self.assertLessEqual(end, len(lines), f"{key}: {relative}:{end}")
                    context = "\n".join(lines[start - 1:end])
                    description = entry["callsite"].rsplit("(", 1)[-1]
                    marker = description.split()[0].split(".")[0]
                    if marker in {"runtime", "literal"}:
                        marker = key
                    if marker == "TouchControls":
                        marker = "hapticLabel"
                    self.assertIn(marker, context, f"{key}: stale callsite {relative}:{first}")

    def test_new_ui_module_scope_classification(self):
        result = classify_callsites([
            {"source": "diploma", "path": "ui/Diploma.lua", "line": 1},
            {"source": "surfing", "path": "ui/SurfingMinigame.lua", "line": 1},
        ])
        self.assertEqual(result["diploma"]["eligibility"], "eligible")
        self.assertEqual(result["diploma"]["category"], "rby")
        self.assertEqual(result["surfing"]["eligibility"], "review")
        self.assertEqual(result["surfing"]["category"], "ui")

    def test_any_rby_without_link_and_link_review(self):
        rows = [
            {"source": "ok", "path": "ui/BagMenu.lua", "line": 1},
            {"source": "ok", "path": "ui/ChoiceBox.lua", "line": 2},
            {"source": "link", "path": "battle/BattleState.lua", "line": 1},
            {"source": "link", "path": "link/LinkBattle.lua", "line": 2},
        ]
        result = classify_callsites(rows)
        self.assertEqual(result["ok"]["eligibility"], "eligible")
        self.assertEqual(result["link"]["eligibility"], "review")

    def test_catalog_universe_mismatch(self):
        tmp, root, scope = self._git_fixture()
        try:
            with self.assertRaises(ValueError): validate_catalog_universe({"other"}, root)
            report = validate_catalog_universe({"one", *forced_dynamic_keys(scope)}, root, scope)
            self.assertEqual(report["forced_dynamic"], 0)
        finally: tmp.cleanup()

    def test_universe_extra_and_missing(self):
        tmp, root, scope = self._git_fixture()
        try:
            with self.assertRaises(ValueError): validate_catalog_universe({"one", "extra"}, root)
            # Subset semantics: a catalog key absent from the source fails the
            # check, but source keys without a catalog entry are tolerated
            # (untranslated engine strings / concatenated fragments).
            self.assertEqual(validate_catalog_universe(set(), root)["catalog_total"], 0)
            (root / "outside.txt").write_text("ok")
            self.assertEqual(verified_source(root, scope)[0], root / "src")
        finally: tmp.cleanup()

    def test_no_git_or_src_layout_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / "tests").mkdir()
            with self.assertRaises(ValueError): verified_source(root)

    def test_staged_and_untracked_source_rejected(self):
        for mode in ("staged", "untracked"):
            tmp, root, scope = self._git_fixture()
            try:
                target = root / "src" / "battle" / ("One.lua" if mode == "staged" else "Extra.lua")
                if mode == "staged":
                    target.write_text('Strings("changed")', encoding="utf-8")
                    subprocess.run(["git", "-C", str(root), "add", "src"], check=True)
                else:
                    target.write_text('Strings("extra")', encoding="utf-8")
                with self.assertRaises(ValueError): verified_source(root, scope)
            finally: tmp.cleanup()

    def test_generate_mod_rejects_invalid_engine_source_before_report(self):
        from pipeline.mod import generate_mod
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); ws = root / "ws"; ws.mkdir()
            for name in ("dialogue", "strings", "species_names", "move_names", "item_names", "trainer_names", "status_labels"):
                (ws / f"{name}.txt").write_text("# header\n", encoding="utf-8")
            (ws / "strings.lua").write_text('return { ["one"] = "" }\n', encoding="utf-8")
            report = root / "coverage.json"
            with self.assertRaises(ValueError):
                generate_mod([], root / "mod", modkit_worksheet=ws, report_path=report, strict_engine=True, engine_source=root / "not-a-checkout")
            self.assertFalse(report.exists())


if __name__ == "__main__":
    unittest.main()
