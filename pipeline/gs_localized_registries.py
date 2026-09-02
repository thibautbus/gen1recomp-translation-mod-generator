"""Corpus-backed Gen 2 type and status registry catalogs."""
from __future__ import annotations

import json
from pathlib import Path

from .gs_index_join import parse_indexed_catalog
from .tokens import corpus_to_engine


_ROOT = Path(__file__).resolve().parents[1]


def _index_corpus(rows: list[tuple[str, str, str]]) -> dict[str, str]:
    """Build the qid -> target lookup once for a batch of _corpus_value_info calls.

    Raises on a duplicate qid rather than silently keeping whichever row
    happens to come last: build_gs_dialogue_mod's own corpus_rows is already
    guaranteed duplicate-free by join_gs_pointers() (which raises first), but
    this function is also callable on its own, and a dict comprehension would
    otherwise pick a row by list order with no warning.
    """
    result: dict[str, str] = {}
    for qid, _english, target in rows:
        if qid in result:
            raise ValueError(f"duplicate corpus qid: {qid!r}")
        result[qid] = target
    return result


def _corpus_value(rows: dict[str, str], qid: str, fallback: str) -> str:
    """Return one audited corpus value, with an explicit English fallback."""
    return _corpus_value_info(rows, qid, fallback)[0]


def _corpus_value_info(
    rows: dict[str, str], qid: str, fallback: str,
) -> tuple[str, bool]:
    """Return a corpus value and whether a non-empty row supplied it.

    A row whose target happens to equal English is still corpus-backed.  Keep
    that distinction separate from a missing row so coverage reports do not
    call an audited same-as-English label an unreviewed fallback. ``rows`` is
    the _index_corpus() lookup, not the raw corpus_rows list: callers resolve
    several qids per catalog, and re-scanning the whole corpus for each one
    was the dominant cost of building these catalogs.
    """
    target = rows.get(qid)
    if target is not None:
        value = corpus_to_engine(target, bare_dynamic_tokens=True).replace("@", "").strip()
        if value:
            return value, True
    return fallback, False


def _catalog_stats(total: int, translated: int, *, scope: str) -> dict:
    return {
        "total": total,
        "translated": translated,
        "no_corpus_entry": total - translated,
        "fallback_english": total - translated,
        "scope": scope,
        "policy": "english-fallback",
    }


# These are the stable ids emitted by data.gen2Constants.phoneContactOrder.
# The four non-trainer rows have official corpus labels; trainer contacts get
# their presentation name from the translated trainers registry at runtime.
PHONE_CONTACT_NAMES = {
    "PHONE_MOM": ("gs.non_trainer_names.NonTrainerCallerNames.mom", "MOM"),
    "PHONE_OAK": ("gs.non_trainer_names.NonTrainerCallerNames.bikeshop", "BIKE SHOP"),
    "PHONE_BILL": ("gs.non_trainer_names.NonTrainerCallerNames.bill", "BILL"),
    "PHONE_ELM": ("gs.non_trainer_names.NonTrainerCallerNames.elm", "PROF.ELM"),
}
PHONE_REGISTERED_IDS = (
    "PHONE_00", "PHONE_MOM", "PHONE_OAK", "PHONE_BILL", "PHONE_ELM",
    "PHONE_SCHOOLBOY_JACK", "PHONE_POKEFAN_BEVERLY", "PHONE_SAILOR_HUEY",
    "PHONE_COOLTRAINERM_GAVEN", "PHONE_COOLTRAINERF_BETH",
    "PHONE_BIRDKEEPER_JOSE", "PHONE_COOLTRAINERF_REENA", "PHONE_YOUNGSTER_JOEY",
    "PHONE_BUG_CATCHER_WADE", "PHONE_FISHER_RALPH", "PHONE_PICNICKER_LIZ",
    "PHONE_HIKER_ANTHONY", "PHONE_CAMPER_TODD", "PHONE_PICNICKER_GINA",
    "PHONE_JUGGLER_IRWIN", "PHONE_BUG_CATCHER_ARNIE", "PHONE_SCHOOLBOY_ALAN",
    "PHONE_LASS_DANA", "PHONE_SCHOOLBOY_CHAD", "PHONE_POKEFANM_DEREK",
    "PHONE_FISHER_CHRIS", "PHONE_POKEMANIAC_BRENT", "PHONE_PICNICKER_TIFFANY",
    "PHONE_BIRDKEEPER_VANCE", "PHONE_FISHER_WILTON", "PHONE_BLACKBELT_KENJI",
    "PHONE_HIKER_PARRY", "PHONE_PICNICKER_ERIN",
)


def phone_contact_catalog(
    corpus_rows: list[tuple[str, str, str]],
    corpus_index: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict]:
    """Join the four localized non-trainer phone display names.

    The 29 trainer rows deliberately are not copied into the catalog: their
    names are resolved through ``class``/``member`` and the translated trainer
    registry, while replacing them with an invented literal would break that
    identity.  The report still accounts for all 33 registered contact ids.
    """
    rows = corpus_index if corpus_index is not None else _index_corpus(corpus_rows)
    resolved = {
        contact_id: _corpus_value_info(rows, qid, english)
        for contact_id, (qid, english) in PHONE_CONTACT_NAMES.items()
    }
    # Never publish the English fallback as a runtime patch. Missing rows are
    # recorded below so a later corpus refresh can turn them into patches;
    # trainer contacts are resolved by the translated trainer registry and
    # therefore remain outside this direct catalog.
    result = {
        contact_id: value for contact_id, (value, found) in resolved.items()
        if found
    }
    translated = sum(found for _value, found in resolved.values())
    stats = _catalog_stats(
        33, translated,
        scope="33 registered contacts; 4 non-trainer names corpus-backed, trainer names resolve via trainer registry",
    )
    stats["same_as_english"] = sum(
        found and value == PHONE_CONTACT_NAMES[key][1]
        for key, (value, found) in resolved.items()
    )
    stats["fallback_ids"] = [
        key for key, (_value, found) in resolved.items() if not found
    ]
    stats["omitted_registry_ids"] = [
        key for key in PHONE_REGISTERED_IDS if key not in PHONE_CONTACT_NAMES
    ]
    stats["backlog"] = [
        {"id": key, "english": english, "reason": "missing-corpus"}
        for key, (_qid, english) in PHONE_CONTACT_NAMES.items()
        if key in stats["fallback_ids"]
    ]
    return result, stats


RADIO_CHANNEL_NAMES = {
    "OAKS_POKEMON_TALK": ("gs.pokegear.OaksPKMNTalkName", "OAK's <PK><MN> Talk"),
    "POKEDEX_SHOW": ("gs.pokegear.PokedexShowName", "POKéDEX Show"),
    "POKEMON_MUSIC": ("gs.pokegear.PokemonMusicName", "POKéMON Music"),
    "LUCKY_CHANNEL": ("gs.pokegear.LuckyChannelName", "Lucky Channel"),
    "PLACES_AND_PEOPLE": ("gs.pokegear.PlacesAndPeopleName", "Places & People"),
    "LETS_ALL_SING": ("gs.pokegear.LetsAllSingName", "Let's All Sing!"),
    # The engine intentionally aliases these two station ids to existing
    # display labels, as the original radio jumptable does.
    "ROCKET_RADIO": ("gs.pokegear.LetsAllSingName", "Let's All Sing!"),
    "POKE_FLUTE_RADIO": ("gs.pokegear.PokeFluteStationName", "POKé FLUTE"),
    "UNOWN_RADIO": ("gs.pokegear.UnownStationName", "?????"),
    "EVOLUTION_RADIO": ("gs.pokegear.UnownStationName", "?????"),
}


def radio_channel_catalog(
    corpus_rows: list[tuple[str, str, str]],
    corpus_index: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict]:
    """Join all ten radio station display names by their stable station id."""
    rows = corpus_index if corpus_index is not None else _index_corpus(corpus_rows)
    resolved = {
        station: _corpus_value_info(rows, qid, english)
        for station, (qid, english) in RADIO_CHANNEL_NAMES.items()
    }
    # Identity English labels with no corpus row are fallback/backlog only;
    # emitting them would make a missing translation look complete.
    result = {
        station: value for station, (value, found) in resolved.items()
        if found
    }
    translated = sum(found for _value, found in resolved.values())
    stats = _catalog_stats(
        10, translated,
        scope="10 radio station ids; labels are corpus-backed where the station name has an official row",
    )
    stats["same_as_english"] = sum(
        found and value == RADIO_CHANNEL_NAMES[station][1]
        for station, (value, found) in resolved.items()
    )
    stats["fallback_ids"] = sorted(station for station, (_value, found) in resolved.items() if not found)
    stats["backlog"] = [
        {"id": station, "english": english, "reason": "missing-corpus"}
        for station, (_qid, english) in RADIO_CHANNEL_NAMES.items()
        if station in stats["fallback_ids"]
    ]
    return result, stats


# DecorationNames is a compact 26-row table, while Decorations.ATTRIBUTES is
# the 53-row runtime table.  The mapping below is audited against the latter:
# category headers and authored names use DecorationNames; species names are
# intentionally left to data.pokemon at runtime.
DECORATION_NAME_QIDS = {
    0: 1, 1: 2, 2: 19, 3: 21, 4: 22, 5: 20, 6: 2,
    7: 23, 8: 24, 9: 25, 10: 26, 11: 2, 12: 3, 13: 4,
    14: 5, 15: 2, 16: 6, 17: 20, 20: 2, 21: 7, 22: 8,
    23: 9, 24: 10, 25: 2, 29: 2, 31: 13, 51: 11, 52: 12,
}

DECORATION_NAME_ENGLISH = {
    1: "CANCEL", 2: "PUT IT AWAY", 3: "MAGNAPLANT", 4: "TROPICPLANT",
    5: "JUMBOPLANT", 6: "TOWN MAP", 7: "NES", 8: "SUPER NES",
    9: "NINTENDO64", 10: "VIRTUAL BOY", 11: "GOLD TROPHY",
    12: "SILVER TROPHY", 13: "SURF PIKACHU DOLL", 14: " BED",
    15: " CARPET", 16: " POSTER", 17: " DOLL", 18: "BIG ",
    19: "FEATHERY", 20: "PIKACHU", 21: "PINK", 22: "POLKADOT",
    23: "RED", 24: "BLUE", 25: "YELLOW", 26: "GREEN",
}
DECORATION_ATTR_NAMES = (
    "CANCEL", "PUT IT AWAY", "FEATHERY", "PINK", "POLKADOT", "PIKACHU",
    "PUT IT AWAY", "RED", "BLUE", "YELLOW", "GREEN", "PUT IT AWAY",
    "MAGNAPLANT", "TROPICPLANT", "JUMBOPLANT", "PUT IT AWAY", "TOWN MAP",
    "PIKACHU", "CLEFAIRY", "JIGGLYPUFF", "PUT IT AWAY", "NES", "SUPER NES",
    "NINTENDO64", "VIRTUAL BOY", "PUT IT AWAY", "SNORLAX", "ONIX", "LAPRAS",
    "PUT IT AWAY", "PIKACHU", "SURF PIKACHU DOLL", "CLEFAIRY", "JIGGLYPUFF",
    "BULBASAUR", "CHARMANDER", "SQUIRTLE", "POLIWAG", "DIGLETT", "STARYU",
    "MAGIKARP", "ODDISH", "GENGAR", "SHELLDER", "GRIMER", "VOLTORB", "WEEDLE",
    "UNOWN", "GEODUDE", "MACHOP", "TENTACOOL", "GOLD TROPHY", "SILVER TROPHY",
)


def decoration_catalog(
    corpus_rows: list[tuple[str, str, str]],
    corpus_index: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict]:
    """Join authored decoration names without replacing species identity."""
    rows = corpus_index if corpus_index is not None else _index_corpus(corpus_rows)
    result: dict[str, str] = {}
    found_by_id: dict[int, bool] = {}
    translated = 0
    same_as_english = 0
    for deco_id in range(len(DECORATION_ATTR_NAMES)):
        qid_number = DECORATION_NAME_QIDS.get(deco_id)
        if qid_number is None:
            # These rows are species-backed dolls.  Emitting their English
            # spelling would override the translated Pokémon registry and is
            # therefore deliberately forbidden; the engine resolves the
            # display name from data.pokemon at runtime.
            found_by_id[deco_id] = False
            continue
        qid = f"gs.names.DecorationNames.{qid_number}"
        english = DECORATION_NAME_ENGLISH[qid_number].replace("@", "").strip()
        value, found_by_id[deco_id] = _corpus_value_info(rows, qid, english)
        if found_by_id[deco_id]:
            result[f"deco:{deco_id}"] = value
            translated += 1
            if value == english:
                same_as_english += 1
    stats = _catalog_stats(
        53, translated,
        scope="53 decoration rows; authored names use DecorationNames, species names remain resolved by the translated species registry",
    )
    stats["same_as_english"] = same_as_english
    stats["fallback_ids"] = [
        f"deco:{deco_id}" for deco_id in range(len(DECORATION_ATTR_NAMES))
        if not found_by_id[deco_id]
    ]
    stats["omitted_species_ids"] = [
        f"deco:{deco_id}" for deco_id in range(len(DECORATION_ATTR_NAMES))
        if deco_id not in DECORATION_NAME_QIDS
    ]
    stats["backlog"] = [
        {
            "id": f"deco:{deco_id}",
            "english": DECORATION_ATTR_NAMES[deco_id],
            "reason": "missing-corpus",
        }
        for deco_id in range(len(DECORATION_ATTR_NAMES))
        if not found_by_id[deco_id] and deco_id in DECORATION_NAME_QIDS
    ]
    return result, stats


def type_name_catalog(
    types_tsv: str | Path, corpus_rows: list[tuple[str, str, str]],
    path: str | Path | None = None,
) -> tuple[dict[str, str], dict]:
    """Join extracted type records to the official Pokédex type-search row."""
    entries = parse_indexed_catalog(types_tsv)
    path = Path(path) if path is not None else _ROOT / "config" / "gsc" / "type_search_indices.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "gen1recomp-translation-mods/gs-type-search-indices" or data.get("version") != 1:
        raise ValueError("unsupported Gen 2 type-search indices schema")
    indices = data.get("types")
    if not isinstance(indices, dict) or not indices:
        raise ValueError("Gen 2 type-search indices require a non-empty types object")
    by_index: dict[int, str] = {}
    prefix = "gs.search_strings.PokedexTypeSearchStrings."
    for qid, english, target in corpus_rows:
        if not qid.startswith(prefix) or not qid[len(prefix):].isdigit():
            continue
        value = corpus_to_engine(target, bare_dynamic_tokens=True).replace("@", "").strip()
        if value:
            by_index[int(qid[len(prefix):])] = value
    result: dict[str, str] = {}
    for entry in entries:
        search_index = indices.get(entry.id)
        if isinstance(search_index, bool) or not isinstance(search_index, int) or search_index < 0:
            continue
        value = by_index.get(search_index)
        if value:
            result[entry.id] = value
    translated = len(result)
    total = len(entries)
    return result, {
        "total": total,
        "translated": translated,
        "no_corpus_entry": total - translated,
        "fallback_english": total - translated,
    }


def status_label_catalog(
    corpus_rows: list[tuple[str, str, str]], path: str | Path | None = None,
    corpus_index: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict]:
    """Resolve Gen 2 status labels by audited qid, including toxic -> PSN."""
    path = Path(path) if path is not None else _ROOT / "config" / "gsc" / "status_anchors.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "gen1recomp-translation-mods/gs-status-anchors" or data.get("version") != 1:
        raise ValueError("unsupported Gen 2 status anchors schema")
    anchors = data.get("statuses")
    if not isinstance(anchors, dict) or not anchors:
        raise ValueError("Gen 2 status anchors require a non-empty statuses object")
    rows = corpus_index if corpus_index is not None else _index_corpus(corpus_rows)
    result: dict[str, str] = {}
    for status, qid in anchors.items():
        if not isinstance(status, str) or not status or not isinstance(qid, str) or not qid.startswith("gs."):
            raise ValueError(f"invalid Gen 2 status anchor for {status!r}")
        value = corpus_to_engine(rows.get(qid, ""), bare_dynamic_tokens=True).replace("@", "").strip()
        if value:
            result[status] = value
    total = len(anchors)
    return result, {
        "total": total,
        "translated": len(result),
        "no_corpus_entry": total - len(result),
        "fallback_english": total - len(result),
    }
