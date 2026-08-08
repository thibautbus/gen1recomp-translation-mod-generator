"""Join corpus records to the exact keys emitted by modkit worksheets."""
from __future__ import annotations

import re
import json
from dataclasses import dataclass
from pathlib import Path
from collections import defaultdict

from .model import Alignment
from .tokens import corpus_to_engine

CATALOGS = ("dialogue", "strings", "species_names", "move_names", "item_names", "trainer_names", "status_labels")

# Terminology is deliberately represented by corpus qids, never by a table of
# translated strings.  This keeps machine displays auditable when adding a
# language: the selected corpus row is the only source of the localized prefix
# and quantity style.
_DEFAULT_TERMINOLOGY_ANCHORS = Path(__file__).resolve().parents[1] / "config" / "terminology_anchors.json"


def _load_terminology_anchors(path: str | Path | None = None) -> dict:
    explicit = path is not None
    path = Path(path) if explicit else _DEFAULT_TERMINOLOGY_ANCHORS
    if not path.is_file():
        if explicit:
            raise ValueError(f"terminology anchors file not found: {path}")
        return {}
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid terminology anchors JSON: {path}") from exc
    return _validate_terminology_anchors(body)


def _validate_terminology_anchors(body: object) -> dict:
    if not isinstance(body, dict):
        raise ValueError("terminology anchors must be a JSON object")
    if body.get("schema") != "gen1recomp-translation-mods/terminology-anchors":
        raise ValueError("unsupported or missing terminology anchors schema")
    if body.get("version") != 1:
        raise ValueError("unsupported or missing terminology anchors version")
    anchors = body.get("anchors")
    if not isinstance(anchors, dict):
        raise ValueError("terminology anchors require an 'anchors' object")
    required = {"technical_prefix", "hidden_prefix", "quantity_style"}
    if set(anchors) != required:
        raise ValueError("terminology anchors must contain exactly technical_prefix, hidden_prefix and quantity_style")
    seen_qids = set()
    expected = {
        "technical_prefix": ("text", None),
        "hidden_prefix": ("text", None),
        "quantity_style": ("quantity_digits", "01"),
    }
    for name in required:
        spec = anchors.get(name)
        if not isinstance(spec, dict) or not isinstance(spec.get("qid"), str) or not spec["qid"].strip():
            raise ValueError(f"terminology anchor {name!r} requires a non-empty qid")
        qid = spec["qid"].strip()
        if qid in seen_qids:
            raise ValueError("terminology anchor qids must be distinct")
        seen_qids.add(qid)
        extraction = spec.get("extraction")
        if not isinstance(extraction, dict) or extraction.get("kind") != expected[name][0]:
            raise ValueError(f"terminology anchor {name!r} has invalid extraction kind")
        if name == "quantity_style" and extraction.get("sample") != expected[name][1]:
            raise ValueError("quantity_style extraction sample must be exactly '01'")
    return body

# A handful of modkit labels are aliases for the labels used by the Red/Blue
# corpus.  These mappings are taken from Gen1Recomp's generated text data and
# are deliberately explicit: an alias is only followed when the corresponding
# engine symbol exists in the corpus.  Unknown aliases remain in the audit as
# manual review instead of being guessed from English text.
ENGINE_ALIASES = {
    "_ActorNameText": "rb.text_2.MonName1Text",
    "_PlayerMon2Text": "rb.text_2.PlayerMon2Text",
    "_PokemonMansion3FSuperNerdEndBattleText": "rb.PokemonMansion3F.PokemonMansion3FSuperNerdEndBattleText",
    "_RoseText": "rb.text_3.RoseText",
    "_UsedMove1Text": "rb.text_2.Used1Text",
    "_UsedMove2Text": "rb.text_2.Used2Text",
    **{f"_EndUsedMove{index}Text": f"rb.text_2.ExclamationPoint{index}Text" for index in range(1, 6)},
}


# Type display names are engine content: they live in the ``type_chart``
# registry (names are translated at draw time, see pipeline/mod.py) and have
# no modkit worksheet, so the join is qid-driven instead of key-driven.  The
# runtime chart carries exactly 15 records (TypeChart.TYPES).  PSYCHIC_TYPE is
# the pokered constant species types are stored as, displayed back as
# "PSYCHIC"; Bird is the pokered type the engine never registers and nothing
# displays, so its corpus row is recorded as excluded but never emitted.
TYPE_NAMES_QID_PREFIX = "rb.names.TypeNames."
TYPE_NAMES_RUNTIME_IDS = {
    "Normal": "NORMAL", "Fighting": "FIGHTING", "Flying": "FLYING",
    "Poison": "POISON", "Ground": "GROUND", "Rock": "ROCK", "Bug": "BUG",
    "Ghost": "GHOST", "Fire": "FIRE", "Water": "WATER", "Grass": "GRASS",
    "Electric": "ELECTRIC", "Psychic": "PSYCHIC_TYPE", "Ice": "ICE",
    "Dragon": "DRAGON",
}

# Engine demo-battle thrower names that are hard-coded in Lua/scripts
# (BattleState.makeOldManDemo's "OLD MAN" default, and Yellow's Pallet
# intro which passes "PROF.OAK").  Corpus qid -> canonical English literal.
DEMO_NAMES_QIDS = {
    "OLD MAN": "rb.core.DisplayBattleMenu.oldManName",
    "PROF.OAK": "rb.name_pointers.TrainerNamePointers.ProfOakName",
}

# The trainer send-out message: the engine renders it as fixed templates
# (BattleState.lua TrainerSentOutText) fed from one corpus row. Older engines
# split it as "%s is\nabout to use" -> "%s!" -> "Will
#   %s\nchange POKéMON?"; the current engine (commit #565) merged the
#   first two into "%s is\nabout to use\v%s!" (2 placeholders, \v = wait
#   for a button press).  The templates are English-structured; fr/es/it
#   mirror them, de and ja need structural adaptation (see
#   _derive_sendout_templates).  Since the v0.1.69 pin, only the merged
#   key and the change prompt are looked up; the pre-#565 split forms were
#   dropped with the pin.
SENDOUT_QID = "rb.text_2.TrainerAboutToUseText"
SENDOUT_ENGINE_KEYS = ("%s is\nabout to use\v%s!", "Will %s\nchange POKéMON?")


def _derive_sendout_templates(value: str, lang: str) -> dict[str, str]:
    """Derive current and legacy send-out templates from the corpus message.

    The corpus value carries the whole localized message with text control
    codes; the trainer name, the incoming nick and the player name are RAM
    placeholders the engine parameterizes.  Structure per language:
    en/fr/it  "X is about to use<CONT>@NICK!<PARA>Will <PLAYER> change #MON?"
    es         ¡@X ... (the ¡ precedes the name)
    de         X wird<LINE>@NICK in den<CONT>Kampf schicken!<PARA>... (nick
               inline; the change prompt has no <PLAYER>)
    ja         Xは　@NICKを<LINE>くりだそうとしているようだ<PARA>...
               (the sentence continues after the nick's を particle)
    """
    value = value.replace("<DONE>", "")
    head, _, tail = value.partition("<PARA>")
    before_nick, nick_sep, nick_suffix = head.partition("{text_ram wEnemyMonNick}")
    prefix, trainer_sep, body = before_nick.partition("{text_ram wTrainerName}")
    if not nick_sep or not trainer_sep:
        # Corpus format drift: a missing RAM token would embed the raw nick
        # or trainer text into the derived template.  Emit nothing and let
        # the coverage gate report the send-out keys as unmatched instead of
        # shipping garbage.
        return {}

    def clean(text: str, cont: str = " ") -> str:
        return (text.replace("{text_start}", "").replace("@", "")
                    .replace("<LINE>", "\n").replace("<CONT>", cont))

    # msg1: the static text glued to the trainer name, cut where the nick
    # begins (<CONT> in en/fr/es/it, a trailing <LINE> in de, nothing in ja).
    if "<CONT>" in body:
        body = body.split("<CONT>", 1)[0]
    elif "<LINE>" in body:
        body = body.split("<LINE>", 1)[0]
    msg1 = clean(prefix) + "%s" + clean(body)

    # msg2: the nick plus its suffix ("!", ja's を particle, de's verb phrase
    # which must wrap onto a second line to fit the message box).
    if lang == "de":
        msg2 = "%s" + clean(nick_suffix, cont="\n")
    else:
        msg2 = "%s" + clean(nick_suffix)

    # The current engine (#565) merged msg1+msg2 into one template with a
    # \v wait marker between the parts; older engines kept them separate.
    merged12 = msg1 + "\v" + msg2

    # msg3: the change prompt.  <PLAYER> becomes the placeholder; the ROM's
    # "#MON" symbol renders as POKéMON in the engine template.  de's prompt
    # addresses the player without a name, so the name is injected (the
    # engine template requires its %s).
    # PokeCorpus uses both ``#MON`` and the bare Japanese ``#`` macro for the
    # Pokémon wordmark.  Engine strings do not expand corpus macros, so never
    # let either form reach Font as a literal/unknown glyph.
    prompt = clean(tail).replace("#MON", "POKéMON").replace("#", "POKéMON")
    if "<PLAYER>" in tail:
        prompt = prompt.replace("<PLAYER>", "%s")
    elif lang == "de":
        prompt = "%s, " + prompt

    return {
        "%s is\nabout to use": msg1,
        "%s!": msg2,
        "Will %s\nchange POKéMON?": prompt,
        "%s is\nabout to use\v%s!": merged12,
    }


def sendout_strings_catalog(items: list[Alignment], target_lang: str = "fr") -> tuple[dict[str, str], dict]:
    """Join the engine's trainer send-out templates from the corpus.

    All three engine keys are emitted (left empty when untranslated, like
    type_names/demo_names) so the coverage gate counts them; an empty
    catalog is returned when the corpus carries no such row at all.
    """
    by_qid = {_base_qid(item.qid): item for item in items}
    row = by_qid.get(SENDOUT_QID)
    output: dict[str, str] = {}
    report = {"translated": 0, "unmatched": [], "strategies": {}, "reasons": {}, "qids": {key: SENDOUT_QID for key in SENDOUT_ENGINE_KEYS}}
    if row is None:
        for key in SENDOUT_ENGINE_KEYS:
            report["unmatched"].append(key)
            report["strategies"][key] = "manual_review"
            report["reasons"][key] = f"{SENDOUT_QID}: no {target_lang} translation; manual review required"
        return output, report
    if not row.translation:
        for key in SENDOUT_ENGINE_KEYS:
            output[key] = ""
            report["unmatched"].append(key)
            report["strategies"][key] = "manual_review"
            report["reasons"][key] = f"{SENDOUT_QID}: empty {target_lang} translation; manual review required"
        return output, report
    templates = _derive_sendout_templates(str(row.translation), target_lang)
    for key in SENDOUT_ENGINE_KEYS:
        value = templates.get(key) or ""
        output[key] = value
        if value:
            report["translated"] += 1
            report["strategies"][key] = "sendout_qid"
        else:
            report["unmatched"].append(key)
            report["strategies"][key] = "manual_review"
            report["reasons"][key] = f"{SENDOUT_QID}: no derivable {target_lang} template; manual review required"
    return output, report


@dataclass
class WorksheetEntry:
    key: str
    english: str
    catalog: str


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    value = value.replace('\\"', '"').replace('\\\\', '\\')
    value = value.replace('\\n', '\n').replace('\\r', '\r').replace('\\t', '\t')
    return re.sub(r"\\([0-7]{1,3})", lambda m: chr(int(m.group(1), 8)), value)


def _repair_cp1252_mojibake(value: str) -> str:
    """Repair UTF-8 output decoded once as CP1252 by Modkit on Windows."""
    try:
        return value.encode("cp1252").decode("utf-8")
    except UnicodeError:
        return value


def read_worksheets(root: str | Path) -> dict[str, list[WorksheetEntry]]:
    root = Path(root); result = {}
    for catalog in CATALOGS:
        path = root / f"{catalog}.txt"
        entries = []
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line or line.startswith("#") or "\t" not in line:
                    continue
                key, english = line.split("\t", 1)
                entries.append(WorksheetEntry(
                    _repair_cp1252_mojibake(_unquote(key)),
                    _repair_cp1252_mojibake(_unquote(english)),
                    catalog,
                ))
        result[catalog] = entries
    return result


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", corpus_to_engine(value).replace("\r\n", "\n").strip()).casefold()


def _symbols(qid: str) -> set[str]:
    # Scope markers are internal (e.g. ^RG.Species), not part of the symbol.
    cleaned = re.sub(r"\^(?:RG|R|G|B)(?=\.|$)", "", qid)
    parts = cleaned.split(".")
    tail = parts[-1]
    # Structured records append a field name after the actual decomp symbol.
    # Dialogue symbols themselves commonly end in "Text", so never strip that
    # suffix from an arbitrary symbol.
    if tail in {"Species", "DescriptionText"} and len(parts) > 1:
        tail = parts[-2]
    return {"_" + tail, tail}


def _same_value(candidates: list[Alignment]) -> list[Alignment]:
    """Collapse canonical aliases when every candidate yields the same value.

    Duplicate ROM labels (for example the two Juggler entries) are not a
    semantic ambiguity when they carry identical French text.  We keep the
    first qid in stable order and record the strategy in the audit.
    """
    if any(candidate.translation is None for candidate in candidates):
        return candidates
    by_value: dict[str, list[Alignment]] = defaultdict(list)
    for candidate in candidates:
        by_value[corpus_to_engine(candidate.translation)].append(candidate)
    if len(by_value) == 1 and candidates:
        return [sorted(candidates, key=lambda item: item.qid)[0]]
    return candidates


def _game_prefix(candidates: list[Alignment]) -> str:
    """Corpus game prefix for the candidate set ('y.' for Yellow, else 'rb.').

    Both corpora share the section layout (``dex_text``, ``names.MoveNames``,
    ...); only the game-scoped prefix differs.
    """
    return "y." if any(item.qid.startswith("y.") for item in candidates) else "rb."


def _canonical_candidates(catalog: str, key: str, candidates: list[Alignment]) -> tuple[list[Alignment], str | None, str | None]:
    """Apply catalogue-specific canonical selection rules.

    Returns candidates, a strategy label, and an optional reason.  A ``None``
    strategy means that the generic symbol/English matching was retained.
    """
    # Engine aliases are stronger than symbol/English matching and are only
    # accepted when the exact qid is present.
    alias = ENGINE_ALIASES.get(key) if catalog == "dialogue" else None
    if alias:
        selected = [item for item in candidates if item.qid == alias]
        if not selected and any(item.qid.startswith("y.") for item in candidates):
            selected = [item for item in candidates if item.qid == alias.replace("rb.", "y.", 1)]
        if selected:
            return selected, "engine_alias", f"Gen1Recomp engine alias {key} -> {selected[0].qid}"

    if catalog == "dialogue" and key.startswith("_") and key.endswith("DexEntry"):
        prefix = _game_prefix(candidates)
        expected = f"{prefix}dex_text.{key[1:]}"
        selected = [
            item for item in candidates
            if item.qid == expected
            # The CLI's compact aligned JSON intentionally omits corpus
            # metadata; an unsuffixed qid is therefore the stable proof of the
            # shared (Red+Blue) scope in addition to the richer metadata path.
            and (item.english.metadata.get("version_scope") == "both" or "^" not in item.qid)
            and item.english.text != "[NULL]"
        ]
        return selected, "canonical_dex_text", f"canonical {prefix}dex_text entry {expected}; excluded dex_entries Species and scoped NULL rows"

    canonical_prefix = {
        "move_names": ("rb.names.MoveNames.", "y.names.MoveNames."),
        "item_names": ("rb.names.ItemNames.", "y.names.ItemNames."),
        "trainer_names": ("rb.names.TrainerNames.", "y.names.TrainerNames."),
    }.get(catalog)
    if canonical_prefix:
        rb_prefix, y_prefix = canonical_prefix
        prefix = y_prefix if _game_prefix(candidates) == "y." else rb_prefix
        selected = [item for item in candidates if item.qid.startswith(prefix)]
        return _same_value(selected), f"canonical_{catalog}", f"selected {prefix} catalogue"

    return candidates, None, None


def _base_qid(qid: str) -> str:
    # Scope markers are internal (e.g. ^RG.rb.names.TypeNames.Fire or a
    # trailing rb.names.TypeNames.Fire^RG), not part of the symbol.
    return re.sub(r"\^(?:RG|R|G|B)(?=\.|$)", "", qid).lstrip(".")


def _type_name_tail(qid: str) -> str | None:
    # Scope markers are internal (e.g. ^RG.rb.names.TypeNames.Fire or a
    # trailing rb.names.TypeNames.Fire^RG), not part of the symbol.
    cleaned = _base_qid(qid)
    if not cleaned.startswith(TYPE_NAMES_QID_PREFIX):
        return None
    return cleaned[len(TYPE_NAMES_QID_PREFIX):]


def type_names_catalog(items: list[Alignment], target_lang: str = "fr") -> tuple[dict[str, str], dict]:
    """Join the corpus TypeNames rows onto the engine's type_chart ids.

    Every runtime id is emitted, left empty when untranslated (the game then
    keeps the English name).  When the corpus carries no TypeNames rows at
    all, an empty catalog is returned so callers without type data keep their
    prior behavior; ``report["excluded"]`` always records the Bird row and
    why it is not emitted.
    """
    by_tail: dict[str, list[Alignment]] = defaultdict(list)
    for item in items:
        tail = _type_name_tail(item.qid)
        if tail:
            by_tail[tail].append(item)
    output: dict[str, str] = {}
    report = {
        "translated": 0,
        "unmatched": [],
        "strategies": {},
        "reasons": {},
        "excluded": {
            "Bird": {
                "qid": TYPE_NAMES_QID_PREFIX + "Bird",
                "reason": "the engine registers no Bird type (type_chart has 15 records) and nothing displays it, so the corpus row is recorded as excluded",
            },
        },
    }
    if not by_tail:
        return output, report
    for tail, runtime_id in TYPE_NAMES_RUNTIME_IDS.items():
        qid = TYPE_NAMES_QID_PREFIX + tail
        candidates = _same_value(by_tail.get(tail, []))
        if len(candidates) == 1 and candidates[0].translation is not None:
            output[runtime_id] = corpus_to_engine(str(candidates[0].translation))
            report["translated"] += 1
            report["strategies"][runtime_id] = "type_name_qid"
        elif len(candidates) == 1:
            output[runtime_id] = ""
            report["unmatched"].append(runtime_id)
            report["strategies"][runtime_id] = "manual_review"
            report["reasons"][runtime_id] = f"{qid}: no {target_lang} translation; manual review required"
        else:
            output[runtime_id] = ""
            report["unmatched"].append(runtime_id)
            report["strategies"][runtime_id] = "manual_review"
            reason = "no canonical candidate" if not candidates else f"ambiguous {qid}: {[x.qid for x in candidates]}"
            report["reasons"][runtime_id] = f"{reason}; manual review required"
    return output, report


# The Pokédex footer (ui/PokedexMenu.lua) is one engine template assembled
# from two corpus labels ("SEEN" and "OWN" fragments of the ROM's footer).
POKEDEX_FOOTER_ENGINE_KEYS = {
    "SEEN %3d  OWN %3d",
}
POKEDEX_FOOTER_LABEL_QIDS = {
    "SEEN": "rb.pokedex.PokedexSeenText",
    "OWN": "rb.pokedex.PokedexOwnText",
}


def pokedex_footer_catalog(items: list[Alignment], target_lang: str = "fr") -> tuple[dict[str, str], dict]:
    """Localize the Pokédex footer template from the two corpus label rows.

    The current engine (#639) formats the footer as one fixed template with
    two 3-digit fields ("SEEN %3d  OWN %3d"); the corpus labels keep their
    ROM casing while the format directive widths come from the template.
    """
    output: dict[str, str] = {}
    report: dict = {"strategies": {}, "reasons": {}, "unmatched": []}
    by_qid: dict[str, Alignment] = {}
    for item in items:
        qid = _base_qid(item.qid)
        by_qid.setdefault(qid, item)
    parts: dict[str, str] = {}
    for role, qid in POKEDEX_FOOTER_LABEL_QIDS.items():
        row = by_qid.get(qid)
        if row is None:
            report["strategies"][role] = "unmatched"
            report["reasons"][role] = f"missing corpus row {qid}"
            report["unmatched"].append(role)
            return output, report
        translation = row.translation
        if not translation:
            report["strategies"][role] = "empty_corpus"
            report["reasons"][role] = f"empty {qid} translation"
            report["unmatched"].append(role)
            return output, report
        parts[role] = translation.replace("{text_start}", "").replace("@", "").strip()
    template = next(iter(POKEDEX_FOOTER_ENGINE_KEYS))
    output[template] = f"{parts['SEEN']} %3d  {parts['OWN']} %3d"
    report["strategies"][template] = "corpus_footer_labels"
    return output, report


# romText fallback keys that v0.1.69 renders via Strings because the pokered
# label cannot carry the call's arguments (slot mismatch in RomText).
#
# 1. "%s\nused %s!" (battle move-use, BattleState.lua:3433): _ItemUseText001
#    extracts as "{PLAYER} used" / "{PLAYER} utilise:" — a single slot, while
#    the call passes user + move.  The item-use variant of the same message
#    ("%s used\n%s!", BattleState.lua:4491) is already translated from
#    rb.text_7.ItemUseText001, so the battle key aliases it.
# 2. "The enemy's weak!\nGet'm! %s!" (BattleState.lua:1362): _EnemysWeakText
#    extracts without a name slot; the corpus row rb.text_2.EnemysWeakText
#    carries the localized phrase with the trailing name slot.
ROMTEXT_USED_KEY = "%s\nused %s!"
ROMTEXT_USED_ALIAS = "%s used\n%s!"
ENEMY_WEAK_QID = "rb.text_2.EnemysWeakText"
ENEMY_WEAK_KEY = "The enemy's weak!\nGet'm! %s!"


def romtext_fallback_catalog(values: dict[str, str], items: list[Alignment], target_lang: str = "fr") -> tuple[dict[str, str], dict]:
    """Localize the romText fallback keys the engine renders via Strings."""
    output: dict[str, str] = {}
    report = {"translated": 0, "unmatched": [], "strategies": {}, "reasons": {}}
    alias = values.get(ROMTEXT_USED_ALIAS)
    if alias:
        output[ROMTEXT_USED_KEY] = alias
        report["strategies"][ROMTEXT_USED_KEY] = "alias_item_use"
    else:
        report["unmatched"].append(ROMTEXT_USED_KEY)
        report["strategies"][ROMTEXT_USED_KEY] = "manual_review"
        report["reasons"][ROMTEXT_USED_KEY] = f"{ROMTEXT_USED_ALIAS!r} untranslated; manual review required"
    row = next((item for item in items if _base_qid(item.qid) == ENEMY_WEAK_QID), None)
    if row is not None and row.translation:
        value = row.translation.replace("{text_start}", "").replace("<LINE>", "\n")
        value = value.rstrip("@")
        output[ENEMY_WEAK_KEY] = value + "%s!"
        report["strategies"][ENEMY_WEAK_KEY] = "corpus_enemy_weak"
    else:
        report["unmatched"].append(ENEMY_WEAK_KEY)
        report["strategies"][ENEMY_WEAK_KEY] = "manual_review"
        report["reasons"][ENEMY_WEAK_KEY] = f"missing or empty corpus row {ENEMY_WEAK_QID}; manual review required"
    report["translated"] = len(output)
    return output, report


# The enemy-mon qualifier (BattleState.lua displayName, #779): battle texts
# naming the enemy mon print "Enemy " before the nickname.  The words come
# from the ROM's own enemy label (rb.text.EnemyText: " ennemi@", "Gegn. @",
# "Enem.@", " nemico@", "てきの　@"); the %s position is curated per language
# because fr/it qualify after the name (the engine note suggests "%s ennemi")
# while de/es/ja prefix it, and the ja label carries a full-width space.
ENEMY_QUALIFIER_QID = "rb.text.EnemyText"
ENEMY_QUALIFIER_KEY = "Enemy %s"
ENEMY_QUALIFIER_VALUES = {
    "fr": "%s ennemi",
    "de": "Gegn. %s",
    "es": "Enem. %s",
    "it": "%s nemico",
    "ja-Hrkt": "てきの　%s",
}

# Keys supplied by dedicated qid joins instead of the generic matcher.  Keep
# them in the engine coverage universe even when Modkit's scaffold omits a
# rendered fallback, including Yellow's refusal message.
QID_DRIVEN_ENGINE_KEYS = frozenset({
    *SENDOUT_ENGINE_KEYS,
    *POKEDEX_FOOTER_ENGINE_KEYS,
    ROMTEXT_USED_KEY,
    ENEMY_WEAK_KEY,
    ENEMY_QUALIFIER_KEY,
})

# Rendered romText fallbacks omitted by Modkit's strings.lua harvester still
# belong to the engine universe.  The two translated fallbacks are already in
# QID_DRIVEN_ENGINE_KEYS; Yellow's starter-Pikachu refusal is supplied
# indirectly by the Yellow ROM/dialogue layer.
YELLOW_REFUSING_KEY = "%s\nis refusing!"
ENGINE_CATALOG_EXTRA_KEYS = QID_DRIVEN_ENGINE_KEYS | {YELLOW_REFUSING_KEY}


def enemy_qualifier_catalog(items: list[Alignment], target_lang: str = "fr") -> tuple[dict[str, str], dict]:
    """Localize the 'Enemy %s' qualifier from the corpus enemy label."""
    output: dict[str, str] = {}
    report = {"translated": 0, "unmatched": [], "strategies": {}, "reasons": {}, "qids": {ENEMY_QUALIFIER_KEY: ENEMY_QUALIFIER_QID}}
    row = next((item for item in items if _base_qid(item.qid) == ENEMY_QUALIFIER_QID), None)
    value = ENEMY_QUALIFIER_VALUES.get(target_lang)
    if row is None or not row.translation:
        report["unmatched"].append(ENEMY_QUALIFIER_KEY)
        report["strategies"][ENEMY_QUALIFIER_KEY] = "manual_review"
        report["reasons"][ENEMY_QUALIFIER_KEY] = f"missing or empty corpus row {ENEMY_QUALIFIER_QID}; manual review required"
    elif value is None:
        report["unmatched"].append(ENEMY_QUALIFIER_KEY)
        report["strategies"][ENEMY_QUALIFIER_KEY] = "manual_review"
        report["reasons"][ENEMY_QUALIFIER_KEY] = f"no curated value for {target_lang}; manual review required"
    else:
        output[ENEMY_QUALIFIER_KEY] = value
        report["strategies"][ENEMY_QUALIFIER_KEY] = "corpus_enemy_qualifier"
    report["translated"] = len(output)
    return output, report


def demo_names_catalog(items: list[Alignment], target_lang: str = "fr") -> tuple[dict[str, str], dict]:
    """Join the corpus rows for engine hard-coded demo-battle names.

    These literals (e.g. the old-man tutorial's "OLD MAN") are baked into
    engine Lua/scripts rather than trainer records, so the mod ships them as
    a small name map read by the makeOldManDemo hook.  An empty catalog is
    returned when the corpus has no matching rows.
    """
    by_qid = {_base_qid(item.qid): item for item in items}
    output: dict[str, str] = {}
    report = {"translated": 0, "unmatched": [], "strategies": {}, "reasons": {}, "qids": {}}
    for literal, qid in DEMO_NAMES_QIDS.items():
        row = by_qid.get(qid)
        report["qids"][literal] = qid
        if row is None:
            # No corpus rows at all: return an empty catalog (callers without
            # corpus data keep their prior behavior, like type_names).
            report["unmatched"].append(literal)
            report["strategies"][literal] = "manual_review"
            report["reasons"][literal] = f"{qid}: no {target_lang} translation; manual review required"
            continue
        # Emit the literal even when untranslated (empty value) so the
        # coverage gate counts it: a language missing the translation then
        # fails the 100% gate instead of silently falling back to English.
        output[literal] = corpus_to_engine(str(row.translation)) if row.translation else ""
        if row.translation:
            report["translated"] += 1
            report["strategies"][literal] = "demo_name_qid"
        else:
            report["unmatched"].append(literal)
            report["strategies"][literal] = "manual_review"
            report["reasons"][literal] = f"{qid}: empty {target_lang} translation; manual review required"
    return output, report


def _anchor_row(items: list[Alignment], qid: str | None, role: str = "prefix") -> tuple[Alignment | None, str]:
    """Return exactly one usable aligned row for a terminology anchor.

    We intentionally reject duplicate rows even when their values happen to
    be equal.  A duplicated qid means the corpus cannot prove which source
    record should drive a generated display, so callers must fall back to the
    ordinary worksheet join instead of manufacturing coverage.
    """
    if not qid:
        return None, "qid_missing"
    rows = [item for item in items if item.qid == qid]
    if len(rows) != 1:
        return None, "qid_absent" if not rows else "qid_ambiguous"
    value = rows[0].translation
    if value is None or not str(value).strip():
        return None, "prefix_empty" if role == "prefix" else "style_empty"
    return rows[0], "ok"


def _digit_sequence(value: str) -> str | None:
    """Extract the first two-digit decimal sequence (ASCII or fullwidth)."""
    match = re.search(r"[0-9０-９]{2}", value or "")
    return match.group(0) if match else None


def _quantity_style(row: Alignment | None, extraction: dict | None = None) -> tuple[str, str]:
    """Infer number glyph style from the aligned InitialQuantityText row.

    A style is proven only when both English and target contain a two-digit
    quantity and the target represents the English sample ``01``.  Mixed,
    malformed, or missing samples deliberately fall back to ASCII.
    """
    if row is None or row.translation is None:
        return "ascii", "style_unproven"
    extraction = extraction if isinstance(extraction, dict) else {}
    sample = str(extraction.get("sample", "01"))
    source = _digit_sequence(row.english.text)
    target = _digit_sequence(str(row.translation))
    if source != sample or target is None:
        return "ascii", "style_unproven"
    ascii_digits = all("0" <= char <= "9" for char in target)
    fullwidth_digits = all("０" <= char <= "９" for char in target)
    if ascii_digits and target == "01":
        return "ascii", "style_proven_ascii"
    if fullwidth_digits and target == "０１":
        return "fullwidth", "style_proven_fullwidth"
    # Do not infer a style from a mixed or non-01 sample: this is precisely
    # the case where silently claiming all 55 entries would create coverage.
    return "ascii", "style_unproven"


def _machine_terminology(items: list[Alignment], anchors: dict) -> dict:
    """Derive localized TM/HM displays from corpus terminology anchors."""
    anchor_map = anchors.get("anchors", anchors) if isinstance(anchors, dict) else {}
    technical = anchor_map.get("technical_prefix", {})
    hidden = anchor_map.get("hidden_prefix", {})
    quantity = anchor_map.get("quantity_style", {})
    technical_row, technical_status = _anchor_row(items, technical.get("qid") if isinstance(technical, dict) else None)
    hidden_row, hidden_status = _anchor_row(items, hidden.get("qid") if isinstance(hidden, dict) else None)
    quantity_row, quantity_status = _anchor_row(items, quantity.get("qid") if isinstance(quantity, dict) else None, "quantity")
    style, style_status = _quantity_style(quantity_row, quantity.get("extraction") if isinstance(quantity, dict) else None)
    # The qid itself is not enough proof: the English corpus value must match
    # the terminology role described by the anchor.
    if technical_status == "ok" and _norm(technical_row.english.text).upper() != "TM":
        technical_status = "role_mismatch"
    if hidden_status == "ok" and _norm(hidden_row.english.text).upper() != "HM":
        hidden_status = "role_mismatch"
    sample = quantity.get("extraction", {}).get("sample", "01") if isinstance(quantity, dict) else "01"
    if quantity_status == "ok" and _digit_sequence(quantity_row.english.text) != sample:
        quantity_status = "role_mismatch"
        style, style_status = "ascii", "style_unproven"
    # Keep the compact anchor view consistent with the role-level statuses.
    details_anchor_status = {
        "technical_prefix": technical_status,
        "hidden_prefix": hidden_status,
        "quantity_style": quantity_status,
    }
    technical_value = corpus_to_engine(str(technical_row.translation).strip()) if technical_row else ""
    hidden_value = corpus_to_engine(str(hidden_row.translation).strip()) if hidden_row else ""
    details = {
        "technical_prefix": technical_value,
        "hidden_prefix": hidden_value,
        "anchors": {
            "technical_prefix": {"qid": technical.get("qid") if isinstance(technical, dict) else None, "status": details_anchor_status["technical_prefix"]},
            "hidden_prefix": {"qid": hidden.get("qid") if isinstance(hidden, dict) else None, "status": details_anchor_status["hidden_prefix"]},
            "quantity_style": {"qid": quantity.get("qid") if isinstance(quantity, dict) else None, "status": details_anchor_status["quantity_style"]},
        },
        "number_style": style,
        "number_style_status": style_status,
    }
    style_ready = style_status in {"style_proven_ascii", "style_proven_fullwidth"}
    details["technical_status"] = technical_status
    details["hidden_status"] = hidden_status
    details["quantity_status"] = quantity_status
    details["technical_ready"] = technical_status == "ok" and style_ready
    details["hidden_ready"] = hidden_status == "ok" and style_ready
    if details["technical_ready"] and details["hidden_ready"]:
        details["status"] = "ready"
    elif details["technical_ready"] or details["hidden_ready"]:
        details["status"] = "partial"
    else:
        details["status"] = "fallback"
    return details


def _format_machine_number(number: str, style: str) -> str:
    if style != "fullwidth":
        return number
    return number.translate(str.maketrans("0123456789", "０１２３４５６７８９"))


def join_catalogs(items: list[Alignment], worksheets: dict[str, list[WorksheetEntry]], target_lang: str = "fr", terminology_anchors: str | Path | dict | None = None) -> tuple[dict[str, dict[str, str]], dict]:
    items = list(items)
    output = {name: {} for name in CATALOGS}
    report = {"matched": {}, "unmatched": {}, "ambiguous": {}, "strategies": {}, "reasons": {}}
    anchors = _validate_terminology_anchors(terminology_anchors) if isinstance(terminology_anchors, dict) else _load_terminology_anchors(terminology_anchors)
    machine_details = _machine_terminology(items, anchors)
    report["machine_display"] = machine_details
    by_english = defaultdict(list); by_symbol = defaultdict(list)
    for item in items:
        by_english[_norm(item.english.text)].append(item)
        for symbol in _symbols(item.qid): by_symbol[symbol].append(item)
    for catalog, entries in worksheets.items():
        for entry in entries:
            report["strategies"].setdefault(catalog, {})
            report["reasons"].setdefault(catalog, {})

            # Gen 1's item menu displays the localized machine family and
            # number, not the move name.  The worksheet English identifier is
            # the ROM/engine proof for the number (TM01..TM50, HM01..HM05).
            machine = re.fullmatch(r"(TM|HM)([0-9]{2})", entry.english.strip().upper())
            if catalog == "item_names" and entry.key.startswith(("TM_", "HM_")) and machine:
                prefix = machine_details.get("technical_prefix", "") if machine.group(1) == "TM" else machine_details.get("hidden_prefix", "")
                # Prefix values are populated from the exact corpus anchors;
                # absent/ambiguous/empty anchors intentionally fall through to
                # ordinary matching and therefore remain unmatched.
                family_ready = machine_details.get("technical_ready" if machine.group(1) == "TM" else "hidden_ready", False)
                if not prefix or not family_ready:
                    family_status = machine_details.get(
                        "technical_status" if machine.group(1) == "TM" else "hidden_status",
                        "style_unproven",
                    )
                    # Report the actual blocker for this family: an invalid
                    # prefix/role takes precedence; when that is valid, an
                    # unproven number style is the reason coverage falls back.
                    report["machine_display"]["last_fallback"] = (
                        family_status
                        if family_status != "ok"
                        else machine_details.get("number_style_status", "style_unproven")
                    )
                    prefix = ""
                if not prefix:
                    # Continue with generic symbol/English candidates.
                    pass
                else:
                    number = _format_machine_number(machine.group(2), machine_details.get("number_style", "ascii"))
                    expected_key_prefix = "TM_" if machine.group(1) == "TM" else "HM_"
                    if entry.key.startswith(expected_key_prefix):
                        output[catalog][entry.key] = f"{prefix}{number}"
                        report["matched"].setdefault(catalog, 0); report["matched"][catalog] += 1
                        # Keep the legacy strategy label for consumers while
                        # recording corpus provenance and style in the report.
                        report["strategies"][catalog][entry.key] = "official_machine_display"
                        report["reasons"][catalog][entry.key] = (
                            f"corpus terminology anchor {machine.group(1)} prefix; "
                            f"number style {report['machine_display'].get('number_style_status')}"
                        )
                        continue

            symbol_candidates = list(by_symbol.get(entry.key, []))
            strategy = "symbol"
            english_candidates = list(by_english.get(_norm(entry.english), []))
            # Symbol is authoritative. If several game variants share it, the
            # imported English selects the variant represented by this
            # worksheet.
            candidates = symbol_candidates
            if len(candidates) > 1:
                narrowed = [item for item in candidates if _norm(item.english.text) == _norm(entry.english)]
                if narrowed:
                    candidates = narrowed
            if not candidates:
                candidates = english_candidates
                strategy = "english"
            # De-duplicate a record matching both symbol and English.
            unique = {id(item): item for item in candidates}.values()
            candidates = list(unique)
            candidates, canonical_strategy, canonical_reason = _canonical_candidates(catalog, entry.key, candidates)
            if canonical_strategy:
                strategy = canonical_strategy
                report["reasons"][catalog][entry.key] = canonical_reason
            if len(candidates) > 1:
                collapsed = _same_value(candidates)
                if len(collapsed) == 1:
                    candidates = collapsed
                    strategy = f"{strategy}_equivalent"
                    report["reasons"][catalog][entry.key] = "multiple canonical qids have identical translated value"
            if len(candidates) == 1 and candidates[0].translation is not None:
                output[catalog][entry.key] = corpus_to_engine(candidates[0].translation)
                report["matched"].setdefault(catalog, 0); report["matched"][catalog] += 1
                report["strategies"][catalog][entry.key] = strategy
            elif len(candidates) == 0 or (len(candidates) == 1 and candidates[0].translation is None):
                output[catalog][entry.key] = ""
                report["unmatched"].setdefault(catalog, []).append(entry.key)
                report["strategies"][catalog][entry.key] = "manual_review"
                reason = f"canonical candidate has no {target_lang} translation" if candidates else "no canonical candidate"
                report["reasons"][catalog].setdefault(entry.key, f"{reason}; manual review required")
            else:
                output[catalog][entry.key] = ""
                report["ambiguous"].setdefault(catalog, {})[entry.key] = [x.qid for x in candidates]
                report["strategies"][catalog][entry.key] = "manual_review"
                report["reasons"][catalog].setdefault(entry.key, "multiple canonical candidates with different translated values")
    # Type display names are engine ``type_chart`` content with no modkit
    # worksheet.  They are joined qid-driven and gated like a catalog when
    # the corpus provides TypeNames rows; otherwise the catalog stays empty
    # and English names remain active at runtime.
    type_values, type_report = type_names_catalog(items, target_lang)
    output["type_names"] = type_values
    report["type_names"] = type_report
    if type_values:
        report["matched"]["type_names"] = type_report["translated"]
        report["unmatched"]["type_names"] = type_report["unmatched"]
        report["strategies"]["type_names"] = type_report["strategies"]
        report["reasons"]["type_names"] = type_report["reasons"]
    # Engine hard-coded demo-battle names (makeOldManDemo's "OLD MAN") are
    # joined the same qid-driven way and gated when the corpus provides rows.
    demo_values, demo_report = demo_names_catalog(items, target_lang)
    output["demo_names"] = demo_values
    report["demo_names"] = demo_report
    if demo_values:
        report["matched"]["demo_names"] = demo_report["translated"]
        report["unmatched"]["demo_names"] = demo_report["unmatched"]
        report["strategies"]["demo_names"] = demo_report["strategies"]
        report["reasons"]["demo_names"] = demo_report["reasons"]
    # The trainer send-out templates (strings catalog) are qid-driven too:
    # one corpus row feeds the engine's three fixed templates.
    sendout_values, sendout_report = sendout_strings_catalog(items, target_lang)
    output["strings"].update(sendout_values)
    report["strings_sendout"] = sendout_report
    if sendout_values:
        report["matched"]["strings_sendout"] = sendout_report["translated"]
        report["unmatched"]["strings_sendout"] = sendout_report["unmatched"]
        report["strategies"]["strings_sendout"] = sendout_report["strategies"]
        report["reasons"]["strings_sendout"] = sendout_report["reasons"]
    # The Pokédex footer template is assembled from two corpus labels.
    pokedex_values, pokedex_report = pokedex_footer_catalog(items, target_lang)
    output["strings"].update(pokedex_values)
    report["strings_pokedex"] = pokedex_report
    if pokedex_values:
        report["matched"]["strings_pokedex"] = pokedex_report.get("translated", len(pokedex_values))
        report["strategies"]["strings_pokedex"] = pokedex_report["strategies"]
        report["reasons"]["strings_pokedex"] = pokedex_report["reasons"]
    return output, report
