"""The Gold mod: manifest + font, and now (backlog steps 10-11) content.

The first step shipped a mod that installs, is correctly identified
(games=["gold"], its own mod id so it can never collide with the RBY
archive on disk), and actually loads under generation=2 (proven by
tools/gate_gen2.lua, not modkit validate), with no content yet.

Step 10 ("Brancher le slice dialogue") wired pipeline/gold_join.py's
pointer join into a `text` catalog: a mod.content.text:override call per
resolved pointer, and silence (the ROM's own English) for every pointer
step 8 could not resolve -- the "repli anglais" the plan asks for is
exactly that omission, not a special case.

Step 11 ("Etendre le slice... pokemon, moves, items, trainers") adds the
index-joined catalogs (pipeline/gold_index_join.py): species names/dex
entries, move names, item names, trainer class names. Every catalog is
isolated in the manifest as its own lang/<name>.lua file and its own
`each` loop, same as RBY's CATALOGS/YELLOW_CATALOG_HOOKS pattern
(pipeline/mod.py) -- deliberately not reusing that code directly, since
generate_mod's machinery (Yellow layers, semantic anchors, worksheets...)
targets RBY's pointer-and-engine-string world, none of which applies
here. Patch call shapes ARE the same as RBY's, though: verified against
src/mods/Schemas.lua's own comment on R.trainers, "the registry keeps
the Gen 1 call shape... and only the one level of indirection to
`.classes` is new" (handled internally, not by the mod author).

font needs no code here at all: "Profil de police inchange pour Or"
(ttf_registration/install_font_assets are already generation-independent).
`strings` (the engine-string chain) is not in this module -- see the
plan's own sequencing and this backlog's commit history for why it
remains open. `landmarks` (a Gen-2-only registry the shared RBY mod
never writes to) was evaluated against its engine read point before
inclusion, same rule as `statuses` -- see build_gold_dialogue_mod's
docstring for what that check found. `radio_channels` was not: out of
scope for this pass, not evaluated one way or the other.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Callable

from .builder import BuildError, _run
from .corpus import canonical_language
from .roms import import_gold_rom, verify_gold_rom
from .generate import lua_string
from .gold_index_join import join_by_index, join_dex_entries, join_landmarks, parse_indexed_catalog
from .gold_join import (
    GoldJoinEntry, audit_join, gold_coverage_report,
    join_gold_pointers, load_map_banks, read_corpus_rows,
)
from .gold_text import parse_gold_text_catalog
from .mod import TRANSLATION_MOD_PRIORITY, install_font_assets, ttf_registration, validate_font_profile
from .project import is_frozen, project_version, resource_root
from .specs import game_spec, release_profile

MAIN = '''-- Generated Gold translation mod.
return function(mod)
__TTF_REGISTRATION__
__CATALOG_REGISTRATION__
end
'''

_CATALOG_HELPER = '''  local function catalog(name)
    local body = mod:read("lang/" .. name .. ".lua")
    if not body then return {} end
    local chunk = loadstring(body)
    if not chunk then return {} end
    local ok, value = pcall(chunk)
    return ok and type(value) == "table" and value or {}
  end
  local function each(name, apply)
    for id, value in pairs(catalog(name)) do
      if type(value) == "string" and value ~= "" then apply(id, value) end
    end
  end
'''

# One entry per registry this mod writes to, in the plan's own order
# (text, [strings, font -- not here], pokemon, moves, items, trainers).
# The dexEntry sub-record takes two separate catalogs/patches (kind,
# text): Loader merges same-id patches, so two `each` calls landing on
# the same species id compose rather than clobber each other.
#
# The `text` registry's catalog file is named "dialogue", not "text",
# matching pipeline/mod.py's own CATALOGS convention for RBY -- not
# coincidentally: tools/modkit.py's MK305 bulk-dump check fires on any
# lang/<name>.lua whose basename exactly matches a data/generated/<name>.lua
# module name ("text" among them), diffing it against that repo's
# data/generated/text.lua. This Gold checkout never has one (there is no
# build_rom_data.py-shaped import for Gold), so modkit reports "dump check
# skipped" as a WARN -- and `modkit pack` runs `validate --strict`, which
# turns that warning fatal and refuses to pack. Named anything outside
# GENERATED_MODULES, the file never triggers the check at all.
GOLD_CATALOG_HOOKS = {
    "dialogue": "mod.content.text:override(id, value)",
    "species_names": "mod.content.pokemon:patch(id, { name = value })",
    "species_kinds": "mod.content.pokemon:patch(id, { dexEntry = { kind = value } })",
    "species_dex_text": "mod.content.pokemon:patch(id, { dexEntry = { text = value } })",
    "move_names": "mod.content.moves:patch(id, { name = value })",
    "item_names": "mod.content.items:patch(id, { name = value })",
    "trainer_class_names": "mod.content.trainers:patch(id, { name = value })",
    "landmarks": "mod.content.landmarks:patch(id, { name = value })",
}

# Mirrors tools/modkit.py's own GENERATED_MODULES (vendored, not importable
# before the dependency is fetched) so a future catalog name reintroduces
# the same "text" collision above as a loud AssertionError here, at import
# time, rather than as a `modkit pack` MK305/--strict failure discovered
# only by actually running a build.
_MODKIT_GENERATED_MODULES = frozenset({
    "constants", "maps", "tilesets", "text", "text_pointers",
    "trainer_headers", "font", "sprites", "pokemon", "moves", "items",
    "type_chart", "trainers", "encounters", "field", "battle_anims",
    "audio", "palettes", "icons",
})
assert not (set(GOLD_CATALOG_HOOKS) & _MODKIT_GENERATED_MODULES), (
    "a GOLD_CATALOG_HOOKS name collides with modkit's GENERATED_MODULES "
    "and will make `modkit pack` fail under --strict; rename the catalog"
)

# A Gold release is not a best-effort extraction.  These are the files emitted
# by tools/gold_extract.lua and consumed by the complete first release slice;
# an absent or empty file means the import was incomplete and must stop here.
GOLD_REQUIRED_TSV = (
    "gold_text.tsv", "gold_labels.tsv", "gold_maps.tsv", "gold_species.tsv",
    "gold_moves.tsv", "gold_items.tsv", "gold_trainer_classes.tsv",
    "gold_landmarks.tsv",
)
GOLD_REQUIRED_REGISTRIES = (
    "species_names", "species_kinds", "species_dex_text", "move_names",
    "item_names", "trainer_class_names", "landmarks",
)

# Known, upstream, harmless-to-us: a real generation=2 boot of this mod
# logs one loader.errors entry per move_names id -- "unresolved
# reference to move_effects EFFECT_..." --
# because Loader:_validate attributes a pre-existing dangling f.id
# reference to "the id's last writer" (Loader.lua's own comment), and a
# move_names patch is the only write this mod makes to any move record.
# gen2MoveEffects is not fully seeded in this v0.1.79 checkout (Gold's Gen
# 2 engine is Phase 1 -- README.md), which is an engine gap the RBY mod's
# equivalent statuses/move_effects exclusion already treats as out of our
# hands, not something a translation mod can or should patch around. It
# does not block the name patch itself: the real boot this was found in
# rendered the translated move/species/NPC text correctly regardless.


def gold_mod_id(language: str) -> str:
    """translation-<lang>-gen2: the generation, never the game set, is what
    the id encodes -- Gold, Silver and Crystal all being generation 2
    means this id never needs a future rename, unlike one spelling out
    today's game list.
    """
    return f"translation-{canonical_language(language).lower()}-gen2"


def gold_archive_name(language: str, version: str) -> str:
    return f"translation-{canonical_language(language).lower()}-gen2-{version}.zip"


def generate_gold_mod(
    destination: str | Path,
    mod_id: str | None = None,
    language: str = "fr",
    target_name: str | None = None,
    target_description: str | None = None,
    font_source: str | Path | None = None,
    font_profile: str = "fusion",
    text_catalog: dict[str, str] | None = None,
    extra_catalogs: dict[str, dict[str, str]] | None = None,
) -> Path:
    """Write manifest.json + main.lua (+ lang/<name>.lua per non-empty catalog).

    ``games: ["gold"]`` is the only field that makes this a Gen 2 mod --
    ``gen2compat`` is derived from it by Manifest.lua, not set directly
    (verified against src/mods/Manifest.lua: raw.games covering generation
    2 already implies gen2compat=true).

    ``text_catalog`` is ``{pointer: translation}`` for pointers that
    resolved (pipeline/gold_join.py's pointer join); ``extra_catalogs`` is
    ``{catalog_name: {id: translation}}`` for the index-joined registries
    in GOLD_CATALOG_HOOKS (pipeline/gold_index_join.py). Either way, an id
    absent from its catalog is never overridden, so the ROM's own English
    shows -- the fallback, not a special-cased branch. Sorted keys make
    regenerating from the same join byte-for-byte deterministic, same as
    RBY's own catalogs.
    """
    language = canonical_language(language)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    mod_id = mod_id or gold_mod_id(language)

    catalogs = {"dialogue": text_catalog or {}, **(extra_catalogs or {})}
    catalogs = {name: values for name, values in catalogs.items() if values}
    unknown = set(catalogs) - set(GOLD_CATALOG_HOOKS)
    if unknown:
        raise ValueError(f"no registry hook for catalog(s): {sorted(unknown)}")

    catalog_registration = ""
    if catalogs:
        (destination / "lang").mkdir(parents=True, exist_ok=True)
        for name, values in catalogs.items():
            lines = [f"-- Generated by the Gold pipeline ({language}): {name}", "return {"]
            lines.extend(f"  [{lua_string(id_)}] = {lua_string(value)}," for id_, value in sorted(values.items()))
            lines.append("}")
            (destination / "lang" / f"{name}.lua").write_text("\n".join(lines) + "\n", encoding="utf-8")
        catalog_registration = _CATALOG_HELPER + "".join(
            f'  each("{name}", function(id, value) {GOLD_CATALOG_HOOKS[name]} end)\n'
            for name in catalogs
        )
    main_body = (
        MAIN.replace("__TTF_REGISTRATION__", ttf_registration(language, font_source, font_profile))
        .replace("__CATALOG_REGISTRATION__", catalog_registration)
    )
    (destination / "main.lua").write_text(main_body, encoding="utf-8")
    install_font_assets(destination, language, font_source, font_profile)

    display_name = target_name or f"{language} translation (Gold)"
    description = target_description or (
        f"{display_name} for Pokemon Gold, based mostly on PokeCorpus. "
        + ("Some engine-specific text remains untranslated." if catalogs
           else "Text is not wired up yet; this is a loadable skeleton.")
    )
    manifest_body = {
        "id": mod_id, "name": display_name, "version": project_version(), "api": 2,
        "entry": "main.lua", "profile": "content", "games": ["gold"],
        "game_version": ">=0.0.0-dev <1.0.0", "category": "GAMEPLAY",
        "priority": TRANSLATION_MOD_PRIORITY, "dependencies": [], "optional_dependencies": [],
        "conflicts": [], "description": description,
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest_body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return destination


def package_gold_mod(
    mod_dir: str | Path,
    gen1recomp: str | Path,
    modkit: str | Path,
    build_root: str | Path,
    destination: str | Path,
    language: str = "fr",
    luajit: str | Path | None = None,
    log_fn: Callable[[str], None] | None = None,
) -> Path:
    """Pack the Gold mod directory into translation-<lang>-gen2-<version>.zip.

    Reuses builder.py's own pack/publish plumbing (_modkit_command, _run,
    publish_archive) rather than reimplementing it: packaging a mod
    directory into a distributable archive is not RBY-specific, only the
    orchestration that builds the directory's CONTENT is. No "--base" is
    passed: modkit pack's default ("auto") falls back to the mod's own
    fixture data when no imported ROM dataset exists, which is always true
    for Gold in this pipeline: Gold's own tools/build_data.py never writes
    a Gen 2 cache, so there is no data/generated/ for it.

    ``pack`` (via ``validate --strict``) shells out to LuaJIT itself, for
    the same reasons pipeline.builder.build's own pack step does: modkit's
    own MODKIT_LUAJIT resolution (tools/modkit.py's `LUAJIT =
    os.environ.get("MODKIT_LUAJIT", "luajit")`) falls back to a bare
    "luajit" on PATH, which only happens to work here because this dev
    environment has one. Passing ``luajit`` builds the same env build()
    does, so a frozen build (bundled LuaJIT, no guarantee it is "luajit"
    on PATH) does not fail with modkit's own MK100 "cannot run luajit".
    """
    from .orchestration import package_release

    language = canonical_language(language)
    archive_name = gold_archive_name(language, project_version())
    env = None
    if luajit is not None:
        env = dict(os.environ)
        env["MODKIT_LUAJIT"] = str(luajit)
        env["LUA"] = str(luajit)
        if is_frozen():
            lua_dir = str(Path(luajit).resolve().parent)
            env["PATH"] = lua_dir + os.pathsep + env.get("PATH", "")
    return package_release(
        mod_dir, gen1recomp, modkit, build_root, destination, archive_name,
        env=env, log_fn=log_fn,
    )


def gold_text_catalog_from_join(entries: list[GoldJoinEntry]) -> dict[str, str]:
    """{pointer: translation} for entries the join actually resolved."""
    return {entry.pointer: entry.translation for entry in entries if entry.translation is not None}


def _write_gate_expectations(mod_dir: Path, catalogs: dict[str, dict[str, str]]) -> Path:
    """Write a tiny, private expectation file consumed by the registry gate."""
    if set(catalogs) != set(GOLD_REQUIRED_REGISTRIES):
        missing = sorted(set(GOLD_REQUIRED_REGISTRIES) - set(catalogs))
        extra = sorted(set(catalogs) - set(GOLD_REQUIRED_REGISTRIES))
        raise BuildError(
            "Gold registry gate expectations are incomplete"
            + (f"; missing: {', '.join(missing)}" if missing else "")
            + (f"; unexpected: {', '.join(extra)}" if extra else "")
        )
    expected: dict[str, dict[str, str]] = {}
    for name in GOLD_REQUIRED_REGISTRIES:
        values = catalogs[name]
        if not isinstance(values, dict) or not values:
            raise BuildError(f"Gold registry gate expectation is empty: {name}")
        key = sorted(values)[0]
        value = values[key]
        if not isinstance(key, str) or not isinstance(value, str) or not value:
            raise BuildError(f"Gold registry gate expectation is malformed: {name}")
        expected[name] = {"id": key, "value": value}
    path = mod_dir.parent / f".{mod_dir.name}.registry-gate.json"
    path.write_text(json.dumps(expected, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


def run_gold_release_gates(
    mod_dir: str | Path,
    entries: list[GoldJoinEntry],
    gen1recomp: str | Path,
    luajit: str,
    *,
    catalogs: dict[str, dict[str, str]] | None = None,
) -> dict:
    """Run every Gold gate before the candidate archive is published.

    This is intentionally fail-closed: an unavailable gate, unresolved ROM
    pointer, bad token, or loader error raises ``BuildError`` and leaves no
    archive in the destination directory.

    Deliberately does NOT run pipeline/validate.py's charmap-based glyph
    check (gold_charmap() reads tools/rom_manifest_gold.json's ROM tile
    charmap). That charmap is the byte encoding for the ROM's stock
    English character set; Gold ships translations through a TTF font
    override instead (Font.drawCode's TTF branch short-circuits the tile
    machinery entirely -- the entire reason `font` entered the release
    slice at all is to carry accents). Gating on it would reject every
    accented character a translation adds. Verified directly: running it
    against a real French build failed with "glyph: 'e'", "glyph: 'a'",
    "glyph: 'i'"... for every accented letter in the corpus, on a build
    that had already passed a real boot verification.
    pipeline.builder.build() does not run this check either -- parity,
    not a Gold-specific gap.
    """
    problems = audit_join(entries)
    if problems:
        raise BuildError("Gold join audit failed:\n" + "\n".join(problems))
    gen1recomp = Path(gen1recomp).resolve()
    coverage = gold_coverage_report(entries)

    tools = resource_root() / "tools"
    fixtures = tools / "gen2_gate_fixtures"
    for script in ("gate_gen2.lua", "gate_gold_dialogue.lua", "gate_gold_registries.lua"):
        if not (tools / script).is_file():
            raise BuildError(f"Gold release gate script is missing: {tools / script}")
    if not Path(luajit).is_file() and shutil.which(luajit) is None:
        raise BuildError("Gold release gate requires a LuaJIT executable")
    mod_dir = Path(mod_dir).resolve()
    # First prove the generation=2 harness itself, then prove this generated
    # mod's dialogue and index registries with values from its own catalogs.
    _run([luajit, str(tools / "gate_gen2.lua"), str(gen1recomp), str(fixtures)])
    translated = next((e for e in entries if e.translation is not None), None)
    if translated is None:
        raise BuildError("Gold dialogue gate requires at least one translated pointer")
    unresolved = next((e for e in entries if e.translation is None), None)
    unresolved_pointer = unresolved.pointer if unresolved else "__gold_unresolved_gate_pointer__"
    _run([luajit, str(tools / "gate_gold_dialogue.lua"), str(gen1recomp), str(mod_dir),
          translated.pointer, translated.translation, unresolved_pointer])
    expectation_path = _write_gate_expectations(mod_dir, catalogs or {})
    try:
        _run([luajit, str(tools / "gate_gold_registries.lua"), str(gen1recomp), str(mod_dir), str(expectation_path)])
    finally:
        expectation_path.unlink(missing_ok=True)
    return {"coverage": coverage}


# qid prefix for each index-joined registry -- verified against the
# real corpus: PokemonNames/MoveNames/
# ItemNames/TrainerClassNames.<N> all use a bare numeric qid suffix, and
# that suffix is reused across unrelated registries (DecorationNames.29 is
# a real, different qid from TrainerClassNames.29), so the prefix must be
# matched, never inferred from the number alone -- caught building this
# join, when an unscoped index map silently picked up the wrong category's
# row for the same number.
_INDEX_CATALOG_QID_PREFIXES = {
    "species_names": "gs.names.PokemonNames.",
    "move_names": "gs.names.MoveNames.",
    "item_names": "gs.names.ItemNames.",
    "trainer_class_names": "gs.class_names.TrainerClassNames.",
}


def build_gold_dialogue_mod(
    gold_out_dir: str | Path,
    corpus_dir: str | Path,
    destination: str | Path,
    mod_id: str | None = None,
    language: str = "fr",
    target_name: str | None = None,
    target_description: str | None = None,
    font_source: str | Path | None = None,
    font_profile: str = "fusion",
    overrides: dict[str, str] | None = None,
) -> tuple[Path, list[GoldJoinEntry], dict]:
    """The repo command this backlog step's gate asks to be reproducible
    from: tools/gold_extract.lua's output (gold_text.tsv/gold_labels.tsv/
    gold_maps.tsv/gold_species.tsv/gold_moves.tsv/gold_items.tsv/
    gold_trainer_classes.tsv, from ``gold_out_dir``) joined against the
    GoldSilver corpus (``corpus_dir``), written straight into a loadable
    mod: the pointer-joined `text` registry (step 10) plus the
    index-joined species/move/item/trainer-class registries (step 11).

    landmarks (a Gen-2-only registry the shared RBY mod never writes to)
    was evaluated against its engine read point before inclusion, same
    rule as `statuses`: Schemas.lua's own R.landmarks spec is
    semantics="record" with the example
    `mod.content.landmarks:patch("LANDMARK_ROUTE_29", { x = 12 })`, i.e.
    per-id patching works exactly like pokemon/moves/items/trainers
    despite the routed target (data.gen2Landmarks.landmarks) being a
    table Game2.lua also pre-populates before mods:load runs -- that
    indirection affects the base data a patch merges onto, not whether a
    single named landmark can be overridden. 93/95 landmarks (98%) have
    a real corpus translation (join_landmarks, matched by normalised
    name: the corpus has no landmark index, only "gs.landmarks.
    <Name>Name" qids). radio_channels was not evaluated -- out of scope
    for this pass, not ruled out.

    Returns ``(mod_dir, entries, stats)`` so a caller can inspect the
    pointer join (audit_join, unresolved_report) without re-running it;
    ``stats["index_catalogs"]`` carries the index-joined registries'
    coverage, one entry per registry rather than a global total.
    """
    gold_out_dir = Path(gold_out_dir)
    language = canonical_language(language)
    missing_or_empty = []
    for filename in GOLD_REQUIRED_TSV:
        path = gold_out_dir / filename
        if not path.is_file() or not any(line.strip() for line in path.read_text(encoding="utf-8").splitlines()):
            missing_or_empty.append(filename)
    if missing_or_empty:
        raise ValueError(
            "Gold release extraction is incomplete; required non-empty TSVs missing: "
            + ", ".join(missing_or_empty)
        )
    records = parse_gold_text_catalog(
        gold_out_dir / "gold_text.tsv", gold_out_dir / "gold_labels.tsv",
    )
    corpus_rows = read_corpus_rows(corpus_dir, target_lang=language)
    maps_path = gold_out_dir / "gold_maps.tsv"
    map_banks = load_map_banks(maps_path)
    entries, stats = join_gold_pointers(records, corpus_rows, map_banks=map_banks, overrides=overrides)

    extra_catalogs: dict[str, dict[str, str]] = {}
    index_stats: dict[str, dict] = {}
    species_path = gold_out_dir / "gold_species.tsv"
    species = parse_indexed_catalog(species_path)
    if not species:
        raise ValueError("gold_species.tsv contains no valid indexed entries")
    for catalog_name, tsv_name in (
        ("species_names", "gold_species.tsv"), ("move_names", "gold_moves.tsv"),
        ("item_names", "gold_items.tsv"), ("trainer_class_names", "gold_trainer_classes.tsv"),
    ):
        tsv_path = gold_out_dir / tsv_name
        catalog_entries = species if catalog_name == "species_names" else parse_indexed_catalog(tsv_path)
        if not catalog_entries:
            raise ValueError(f"{tsv_name} contains no valid indexed entries")
        translations, catalog_stats = join_by_index(
            catalog_entries, corpus_rows, _INDEX_CATALOG_QID_PREFIXES[catalog_name],
        )
        extra_catalogs[catalog_name] = translations
        index_stats[catalog_name] = catalog_stats
    kind_translations, kind_stats = join_dex_entries(species, corpus_rows, "dex_entries", "Species")
    extra_catalogs["species_kinds"] = kind_translations
    index_stats["species_kinds"] = kind_stats
    text_translations, text_stats = join_dex_entries(species, corpus_rows, "dex_entries_gold")
    extra_catalogs["species_dex_text"] = text_translations
    index_stats["species_dex_text"] = text_stats
    landmarks_path = gold_out_dir / "gold_landmarks.tsv"
    landmarks = parse_indexed_catalog(landmarks_path)
    if not landmarks:
        raise ValueError("gold_landmarks.tsv contains no valid indexed entries")
    landmark_translations, landmark_stats = join_landmarks(landmarks, corpus_rows)
    extra_catalogs["landmarks"] = landmark_translations
    index_stats["landmarks"] = landmark_stats
    stats["index_catalogs"] = index_stats
    # Kept in-memory for the pre-publication registry gate; callers that
    # serialize stats can omit this private payload.
    stats["_gate_catalogs"] = extra_catalogs

    mod_dir = generate_gold_mod(
        destination, mod_id=mod_id, language=language, target_name=target_name,
        target_description=target_description, font_source=font_source, font_profile=font_profile,
        text_catalog=gold_text_catalog_from_join(entries),
        extra_catalogs=extra_catalogs,
    )
    return mod_dir, entries, stats


def build_gold(
    gold_rom: str | Path,
    language: str,
    language_name: str,
    luajit: str,
    workspace_root: str | Path | None = None,
    output_dir: str | Path | None = None,
    log_fn: Callable[[str], None] | None = None,
    status_fn: Callable[[str], None] | None = None,
    font_profile: str = "fusion",
) -> Path:
    """The generation=2 mirror of pipeline.builder.build(): the complete
    private extraction, join, and pack flow for a single Gold ROM.

    A deliberately separate function, not a branch inside build() or a
    unification of the two: the two pipelines' actual steps differ (a
    luajit subprocess against tools/gold_extract.lua vs a Python
    subprocess against build_rom_data.py; one ROM and no Yellow-style
    versioned layer vs up to three ROMs), and building a single function
    that takes both shapes would mean exactly the sentinel-parameter
    problem this project avoids elsewhere: gold_rom=None on a Gen 1 call,
    red_rom=None on a Gen 2 one. ``luajit``
    is accepted for signature symmetry with build() and because callers
    already resolved it via check_prerequisites(); import_gold_rom
    re-resolves it internally regardless (pipeline.project.which_luajit).
    """
    def status(message: str) -> None:
        if status_fn:
            status_fn(message)

    def log(message: str) -> None:
        print(message)
        if log_fn:
            log_fn(message)

    language = canonical_language(language)
    profile = release_profile("gold")
    spec = game_spec("gold")
    if spec.corpus_collection not in profile.corpus_collections:
        raise BuildError("Gold release profile and game spec disagree on corpus collection")
    font_profile = validate_font_profile(language, font_profile)
    status("Validating ROM")
    verify_gold_rom(gold_rom)

    from .orchestration import prepare_build_context
    context = prepare_build_context(
        workspace_root, output_dir, profile=profile, language=language,
        font_profile=font_profile,
    )
    workspace = context.workspace
    destination = context.destination

    status("Preparing dependencies")
    gen1recomp, corpus, font_source = context.gen1recomp, context.corpus, context.font_source
    corpus_gold_silver = corpus / "corpus" / "GoldSilver"

    log("\nExtracting private Gold ROM data...")
    status("Extracting private Gold ROM data")
    gold_out = workspace / "gold" / "extracted"
    import_gold_rom(gold_rom, gen1recomp, gold_out, log_fn=log_fn)

    build_root = workspace / "interactive-gold" / language
    mod_id = gold_mod_id(language)
    mod_dir = build_root / mod_id
    log("\nJoining corpus and generating the mod...")
    status("Joining corpus and generating the mod")
    mod_dir, entries, stats = build_gold_dialogue_mod(
        gold_out, corpus_gold_silver, mod_dir, mod_id=mod_id, language=language,
        target_name=f"{language_name} translation", font_source=font_source, font_profile=font_profile,
    )
    log(
        f"  text: {stats['unique'] + stats['harmless_ambiguous'] + stats['map_context']}/{stats['total']} pointers"
        f" ({stats['unresolved']} unresolved, left in English)"
    )

    status("Running Gold release gates")
    run_gold_release_gates(
        mod_dir, entries, gen1recomp, luajit,
        catalogs=stats.get("_gate_catalogs", {}),
    )

    version = project_version()
    destination.mkdir(parents=True, exist_ok=True)
    modkit = gen1recomp / "tools" / "modkit.py"
    status("Packaging translation mod")
    published = package_gold_mod(
        mod_dir, gen1recomp, modkit, build_root, destination, language=language, luajit=luajit, log_fn=log_fn,
    )
    status("Build complete")
    return published
