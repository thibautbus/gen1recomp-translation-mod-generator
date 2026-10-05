"""The generation-3 game families a translation mod is built for.

gen1recomp runs FireRed/LeafGreen, Ruby/Sapphire and Emerald on the same game3
runtime, but
each family has its own PokeCorpus collection, qid namespace, pret charmap and
text dialect (``TextIR.DIALECTS``), and its own reviewed configuration under
its release's ``config/`` and ``overrides/<language>/`` directories.  The joins in
pipeline.gen3 read all of that from the family the corpus was loaded for.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Gen3Family:
    # config/<id>/, overrides/<language>/<id>/ and the schema names' prefix
    id: str
    # the family's name in messages and reports
    game: str
    # PokeCorpus collection directory (corpus/<collection>/)
    collection: str
    # every qid of the collection starts with it
    qid_prefix: str
    # src/core/game3/scripting/text_ir.lua, TextIR.DIALECTS
    dialect: str
    # the editions one mod covers; the first one is the default
    editions: tuple[str, ...]
    # other game3 families whose reviewed engine strings this one falls back
    # on: the runtime and its Strings() keys are shared
    shared_engine_families: tuple[str, ...] = ()
    # the release whose config/ and overrides/<language>/ directory holds the
    # family's files, and the prefix they carry there: a release's base game
    # has none, its companion edition is named, as Crystal's files are
    # crystal_*.json in Gold/Silver's release and Yellow's yellow_*.json in
    # Red/Blue's
    directory: str = ""
    file_prefix: str = ""
    # the collection's battle string table (pret's battle_message.c, or
    # pokeruby's battle_strings_en.h)
    battle_group: str = "battle_message"
    # the marker a qid group carries when its rows belong to one edition
    # only (PokeCorpus writes Ruby's and Sapphire's Pokédex entries as
    # pokedex_entries^R and pokedex_entries^S)
    edition_markers: tuple[tuple[str, str], ...] = ()

    def schema(self, name: str) -> str:
        return f"gen1recomp-translation-mods/{self.id}-{name}"

    def edition_marker(self, edition: str | None) -> str | None:
        return dict(self.edition_markers).get(edition) if edition else None

    def config_path(self, root: str | Path, name: str) -> Path:
        """config/<release>/<prefix><name>"""
        return Path(root) / "config" / (self.directory or self.id) / f"{self.file_prefix}{name}"

    def overrides_path(self, root: str | Path, language: str, name: str) -> Path:
        """overrides/<language>/<release>/<prefix><name>"""
        return Path(root) / "overrides" / language / (self.directory or self.id) / f"{self.file_prefix}{name}"


FRLG = Gen3Family("frlg", "FireRed", "FireRedLeafGreen", "frlg.", "frlg", ("firered", "leafgreen"))
# Ruby and Sapphire are the base game of the "rse" release (config/rse/,
# overrides/<language>/rse/): one collection (their Pokédex entries told
# apart by the qid's ^R/^S group), pokeruby's symbols and charmap, the
# runtime's ``rs`` dialect.  The runtime is Emerald's, so the family falls
# back on Emerald's reviewed engine strings, then FireRed's.
RUBY_SAPPHIRE = Gen3Family("rs", "Ruby/Sapphire", "RubySapphire", "rs.", "rs", ("ruby", "sapphire"),
                           shared_engine_families=("emerald", "frlg"), directory="rse",
                           battle_group="battle_strings", edition_markers=(("ruby", "R"), ("sapphire", "S")))
# Emerald is the companion edition of the "rse" release (config/rse/,
# overrides/<language>/rse/, its files named emerald_*.json).
EMERALD = Gen3Family("emerald", "Emerald", "Emerald", "e.", "rse", ("emerald",),
                     shared_engine_families=("frlg",), directory="rse", file_prefix="emerald_")
FAMILIES = {family.id: family for family in (FRLG, RUBY_SAPPHIRE, EMERALD)}
