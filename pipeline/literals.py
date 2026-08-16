"""QID-driven literal dialogue handlers.

Some ported conversations contain literals because the original assembly text
was not emitted by the extractor.  Recipes keep those handlers data-driven:
the English prompt is named by an engine data field and translated branches
are looked up by immutable corpus qids.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .model import Alignment
from .tokens import corpus_to_engine
from .generate import lua_string

SCHEMA = "gen1recomp-translation-mods/literal-handlers"


@dataclass(frozen=True)
class LiteralHandler:
    map_id: str
    text_constant: str
    prompt: str
    yes: str
    no: str
    prompt_qid: str
    yes_qid: str
    no_qid: str
    # Version 2 recipes may describe a small, data-only script instead of the
    # historical prompt/yes/no shape.  The resolved flow contains translated
    # strings (never qids) and is rendered by the generator.
    flow: tuple[Mapping[str, Any], ...] = ()
    flow_qids: tuple[str, ...] = ()
    on_step: tuple[Mapping[str, Any], ...] = ()
    on_step_condition: Mapping[str, Any] | None = None


def load_recipes(path: str | Path) -> list[dict]:
    """Load and validate qid-driven handler recipes from JSON."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        not isinstance(document, dict)
        or document.get("schema") != SCHEMA
        or document.get("version") not in (1, 2)
    ):
        raise ValueError("literal handler config must be a version 1 or 2 object")
    recipes = document.get("handlers", [])
    if not isinstance(recipes, list):
        raise ValueError("literal handler config handlers must be a list")
    for recipe in recipes:
        if not isinstance(recipe, Mapping):
            raise ValueError("literal handler recipes must be objects")
        if "flow" in recipe:
            _flow_qids(recipe["flow"])
            if "on_step" in recipe:
                step = recipe["on_step"]
                if not isinstance(step, Mapping) or not isinstance(step.get("when"), Mapping):
                    raise ValueError("invalid literal handler on_step recipe")
                _step_condition(step["when"])
                _flow_qids(step.get("flow", []))
        else:
            _recipe(recipe)
    return recipes


def _recipe(item: Mapping[str, object]) -> tuple[str, str, str, str, str]:
    try:
        map_id = item["map"]
        text_constant = item["text_constant"]
        prompt = item["prompt"]
        yes = item["yes"]
        no = item["no"]
        prompt_qid = prompt.get("qid")  # type: ignore[union-attr]
        yes_qid = yes.get("qid")  # type: ignore[union-attr]
        no_qid = no.get("qid")  # type: ignore[union-attr]
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("invalid literal handler recipe") from exc
    fields = (map_id, text_constant, prompt_qid, yes_qid, no_qid)
    if not all(isinstance(value, str) and value for value in fields):
        raise ValueError("literal handler recipe fields may not be empty")
    return map_id, text_constant, prompt_qid, yes_qid, no_qid


def _flow_qids(value: object) -> list[str]:
    """Collect qids from a version 2 flow, rejecting malformed nodes."""
    if not isinstance(value, list):
        raise ValueError("literal handler flow must be a list")
    result: list[str] = []
    def integer(value: object, label: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{label} must be an integer")

    def condition(value: object) -> None:
        if not isinstance(value, Mapping):
            raise ValueError("if operation requires a condition")
        if isinstance(value.get("flag"), str) and value["flag"]:
            if len(value) != 1:
                raise ValueError("flag condition has unexpected fields")
            return
        inventory = value.get("inventory")
        if isinstance(inventory, Mapping):
            if not isinstance(inventory.get("item"), str) or not inventory["item"]:
                raise ValueError("inventory condition requires item")
            if inventory.get("op", "gt") not in ("gt", "gte", "eq"):
                raise ValueError("invalid inventory condition operator")
            integer(inventory.get("amount", 0), "inventory condition amount")
            return
        alternatives = value.get("any")
        if isinstance(alternatives, list) and alternatives:
            if len(value) != 1:
                raise ValueError("any condition has unexpected fields")
            for alternative in alternatives:
                condition(alternative)
            return
        if isinstance(value.get("money_gte"), int) and not isinstance(value.get("money_gte"), bool):
            if len(value) != 1:
                raise ValueError("money condition has unexpected fields")
            return
        if value.get("trainer_defeated") is True:
            if len(value) != 1:
                raise ValueError("trainer_defeated condition has unexpected fields")
            return
        raise ValueError("unsupported literal handler condition")

    for node in value:
        if not isinstance(node, Mapping) or len(node) != 1:
            raise ValueError("literal handler flow nodes must have one operation")
        op, payload = next(iter(node.items()))
        if op == "say":
            if (
                not isinstance(payload, Mapping)
                or not set(payload).issubset({"qid", "name_qid"})
                or not isinstance(payload.get("qid"), str) or not payload["qid"]
                or ("name_qid" in payload and (not isinstance(payload["name_qid"], str) or not payload["name_qid"]))
            ):
                raise ValueError("say operation requires a qid")
            result.append(payload["qid"])
            if "name_qid" in payload:
                result.append(payload["name_qid"])
        elif op in ("choice", "if"):
            if not isinstance(payload, Mapping):
                raise ValueError(f"{op} operation requires an object")
            allowed = {"yes", "no"} if op == "choice" else {"condition", "then", "else"}
            if not set(payload).issubset(allowed) or (op == "choice" and set(payload) != allowed):
                raise ValueError(f"invalid {op} operation fields")
            children = (payload.get("yes", []), payload.get("no", [])) if op == "choice" else (payload.get("then", []), payload.get("else", []))
            if op == "if":
                condition(payload.get("condition"))
            for child in children:
                result.extend(_flow_qids(child))
        elif op in ("set_flag", "inventory", "money", "script_move"):
            if op == "set_flag" and (not isinstance(payload, Mapping) or set(payload) != {"flag"} or not isinstance(payload.get("flag"), str) or not payload["flag"]):
                raise ValueError("set_flag operation requires a flag")
            if op == "inventory" and (not isinstance(payload, Mapping) or not set(payload).issubset({"item", "op", "amount"}) or not isinstance(payload.get("item"), str) or payload.get("op") not in ("add", "remove", "set")):
                raise ValueError("inventory operation requires item and add/remove/set")
            if op == "inventory":
                integer(payload.get("amount", 1), "inventory amount")
            if op == "money" and (not isinstance(payload, Mapping) or set(payload) != {"amount"} or isinstance(payload.get("amount"), bool) or not isinstance(payload.get("amount"), int)):
                raise ValueError("money operation requires an integer amount")
            if op == "script_move" and (not isinstance(payload, Mapping) or not set(payload).issubset({"direction", "steps"}) or payload.get("direction") not in ("up", "down", "left", "right")):
                raise ValueError("script_move operation requires a direction")
            if op == "script_move":
                integer(payload.get("steps", 1), "script_move steps")
        elif op == "done":
            if payload is not None:
                raise ValueError("done operation takes null")
        elif op == "engage_trainer":
            if payload is not None:
                raise ValueError("engage_trainer operation takes null")
        else:
            raise ValueError(f"unsupported literal handler operation: {op}")
    return result


def _resolve_flow(value: object, candidates: Mapping[str, set[str]]) -> tuple[tuple[tuple[dict[str, Any], ...], tuple[str, ...]], bool]:
    """Resolve every say.qid; return (flow, complete)."""
    qids = _flow_qids(value)
    resolved: dict[str, str] = {}
    for qid in qids:
        values = candidates.get(qid, set())
        if len(values) != 1:
            return ((), ()), False
        resolved[qid] = corpus_to_engine(next(iter(values)))

    def walk(nodes: list[object]) -> tuple[dict[str, Any], ...]:
        output: list[dict[str, Any]] = []
        for node in nodes:
            op, payload = next(iter(node.items()))  # type: ignore[union-attr]
            if op == "say":
                text = resolved[payload["qid"]]  # type: ignore[index]
                if "name_qid" in payload:  # type: ignore[operator]
                    # {RAM:...} is this project's own marker for the ROM's
                    # {text_ram ...} dynamic buffer; the flow DSL has no
                    # runtime substitution, but the substituted value is a
                    # fixed, known constant for a given handler (e.g. this
                    # scene always names the same item), so splice the
                    # already-resolved, already-translated name in at
                    # generation time instead.
                    name = resolved[payload["name_qid"]]  # type: ignore[index]
                    text = re.sub(r"\{RAM:[^}]+\}", lambda _match: name, text, count=1)
                output.append({"say": text})
            elif op in ("choice", "if"):
                copy = dict(payload)  # type: ignore[arg-type]
                if op == "choice":
                    copy["yes"] = walk(copy.get("yes", []))
                    copy["no"] = walk(copy.get("no", []))
                else:
                    copy["then"] = walk(copy.get("then", []))
                    copy["else"] = walk(copy.get("else", []))
                output.append({op: copy})
            else:
                output.append({op: payload})
        return tuple(output)

    return (walk(value), tuple(qids)), True  # type: ignore[arg-type]


def _step_condition(value: Mapping[str, Any]) -> None:
    coords = value.get("coords")
    if not isinstance(coords, list) or not coords or any(
        not isinstance(pair, list) or len(pair) != 2 or
        any(isinstance(n, bool) or not isinstance(n, int) for n in pair)
        for pair in coords
    ):
        raise ValueError("on_step requires integer coordinate pairs")
    if not isinstance(value.get("not_flag"), str) or not value["not_flag"]:
        raise ValueError("on_step requires not_flag")
    if set(value) != {"coords", "not_flag"}:
        raise ValueError("on_step condition has unexpected fields")


def extract_handlers(items: Iterable[Alignment], recipes: Iterable[Mapping[str, object]]) -> list[LiteralHandler]:
    """Resolve branch translations by qid; incomplete recipes are skipped.

    Skipping is intentional: registering a handler with a missing target would
    suppress the game's base English conversation and falsely claim coverage.
    """
    candidates: dict[str, set[str]] = defaultdict(set)
    for row in items:
        if row.qid and isinstance(row.translation, str) and row.translation:
            candidates[row.qid].add(row.translation)
    result: list[LiteralHandler] = []
    for raw in recipes:
        # Version 2's flow form is intentionally independent of the legacy
        # prompt/yes/no fields.  A malformed or incomplete flow is skipped so
        # vanilla English remains active.
        if "flow" in raw:
            try:
                map_id = raw["map"]
                text_constant = raw["text_constant"]
                if not all(isinstance(value, str) and value for value in (map_id, text_constant)):
                    raise ValueError
                (flow, flow_qids), complete = _resolve_flow(raw["flow"], candidates)
                on_step = ()
                on_step_condition = None
                if "on_step" in raw:
                    step = raw["on_step"]
                    if not isinstance(step, Mapping) or not isinstance(step.get("when"), Mapping):
                        raise ValueError
                    _step_condition(step["when"])
                    (on_step, step_qids), complete_step = _resolve_flow(step.get("flow", []), candidates)
                    if not complete_step:
                        complete = False
                    flow_qids = tuple(dict.fromkeys(flow_qids + step_qids))
                    on_step_condition = dict(step["when"])
            except (KeyError, TypeError, ValueError):
                continue
            if complete:
                result.append(LiteralHandler(
                    map_id, text_constant, "", "", "", "", "", "", flow, flow_qids, on_step, on_step_condition,
                ))
            continue
        map_id, text_constant, prompt_qid, yes_qid, no_qid = _recipe(raw)
        prompt_values = candidates.get(prompt_qid, set())
        yes_values = candidates.get(yes_qid, set())
        no_values = candidates.get(no_qid, set())
        if (
            len(prompt_values) != 1
            or len(yes_values) != 1
            or len(no_values) != 1
        ):
            continue
        prompt = next(iter(prompt_values))
        yes = next(iter(yes_values))
        no = next(iter(no_values))
        result.append(LiteralHandler(
            map_id,
            text_constant,
            corpus_to_engine(prompt),
            corpus_to_engine(yes),
            corpus_to_engine(no),
            prompt_qid,
            yes_qid,
            no_qid,
        ))
    return result


def generate_handlers(items: Iterable[Alignment], recipes: Iterable[Mapping[str, object]], output: str | Path) -> tuple[Path, list[LiteralHandler]]:
    """Generate a Lua module returning a setup function for literal handlers."""
    handlers = extract_handlers(items, recipes)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "-- Generated qid-driven literal dialogue handlers.",
        "return function(mod)",
        "  local TextBox = mod.ui.TextBox",
        "  local ChoiceBox = mod.ui.ChoiceBox",
    ]
    for handler in handlers:
        lines.append(f"  mod.content.map_scripts:register({lua_string(handler.map_id)}, {{talk = {{")
        lines.append(f"    [{lua_string(handler.text_constant)}] = function(game, ow, npc, done)")
        if handler.flow:
            lines.extend(_render_flow(handler.flow, 6, "done"))
        else:
            lines.extend([
                f"      game.stack:push(TextBox.new(game, {lua_string(handler.prompt)}, function()",
                "        game.stack:push(ChoiceBox.new(game, function(yes)",
                f"          game.stack:push(TextBox.new(game, yes and {lua_string(handler.yes)} or {lua_string(handler.no)}, done))",
                "        end))",
                "      end))",
            ])
        lines.extend(["    end,", "  },"])
        if handler.on_step:
            condition = _step_condition_lua(handler.on_step_condition or {})
            lines.append(f"    onStep = function(game, ow, x, y)")
            lines.append(f"      if {condition} then")
            lines.append("        local function on_done() end")
            lines.extend(_render_flow(handler.on_step, 8, "on_done"))
            lines.extend(["        return true", "      end", "      return false", "    end,"])
        lines.append("  })")
    lines.append("end")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output, handlers


def _step_condition_lua(value: Mapping[str, Any]) -> str:
    _step_condition(value)
    coords = " or ".join(f"(x == {pair[0]} and y == {pair[1]})" for pair in value["coords"])
    return f"({coords}) and not game.save.flags[{lua_string(value['not_flag'])}]"


def _condition(value: object) -> str:
    if not isinstance(value, Mapping):
        raise ValueError("if operation requires a condition")
    if isinstance(value.get("flag"), str):
        return f"game.save.flags[{lua_string(value['flag'])}]"
    inventory = value.get("inventory")
    if isinstance(inventory, Mapping) and isinstance(inventory.get("item"), str):
        item = lua_string(inventory["item"])
        op, amount = inventory.get("op", "gt"), inventory.get("amount", 0)
        if op not in ("gt", "gte", "eq") or not isinstance(amount, int):
            raise ValueError("invalid inventory condition")
        symbol = {"gt": ">", "gte": ">=", "eq": "=="}[op]
        return f"(game.save.inventory[{item}] or 0) {symbol} {amount}"
    alternatives = value.get("any")
    if isinstance(alternatives, list) and alternatives:
        return "(" + " or ".join(_condition(item) for item in alternatives) + ")"
    money = value.get("money_gte")
    if isinstance(money, int):
        return f"(game.save.money or 0) >= {money}"
    if value.get("trainer_defeated") is True:
        return "ow:trainerDefeated(npc)"
    raise ValueError("unsupported literal handler condition")


def _render_flow(nodes: tuple[Mapping[str, Any], ...], indent: int, done_expr: str) -> list[str]:
    """Render the flow DSL, carrying all remaining nodes through branches."""
    def invoke(expr: str, pad: str) -> str:
        return f"{pad}{expr}()"

    def seq(current: tuple[Mapping[str, Any], ...], level: int, continuation: str) -> list[str]:
        pad = " " * level
        if not current:
            return [invoke(continuation, pad)]
        op, payload = next(iter(current[0].items()))
        rest = current[1:]
        if op == "say":
            if rest:
                callback = "function()\n" + "\n".join(seq(rest, level + 2, continuation)) + f"\n{pad}end"
            else:
                callback = continuation
            return [f"{pad}game.stack:push(TextBox.new(game, {lua_string(payload)}, {callback}))"]
        if op == "choice":
            lines = [f"{pad}game.stack:push(ChoiceBox.new(game, function(yes)", f"{pad}  if yes then"]
            lines.extend(seq(tuple(payload.get("yes", ())) + rest, level + 4, continuation))
            lines.append(f"{pad}  else")
            lines.extend(seq(tuple(payload.get("no", ())) + rest, level + 4, continuation))
            lines.extend([f"{pad}  end", f"{pad}end))"])
            return lines
        if op == "if":
            lines = [f"{pad}if {_condition(payload.get('condition'))} then"]
            lines.extend(seq(tuple(payload.get("then", ())) + rest, level + 2, continuation))
            if payload.get("else"):
                lines.append(f"{pad}else")
                lines.extend(seq(tuple(payload.get("else", ())) + rest, level + 2, continuation))
            lines.append(f"{pad}end")
            return lines
        if op == "set_flag":
            return [f"{pad}game.save.flags[{lua_string(payload['flag'])}] = true"] + seq(rest, level, continuation)
        if op == "inventory":
            item, amount = lua_string(payload["item"]), payload.get("amount", 1)
            if payload["op"] == "remove":
                line = f"{pad}game.save.inventory[{item}] = nil"
            elif payload["op"] == "set":
                line = f"{pad}game.save.inventory[{item}] = {amount}"
            else:
                line = f"{pad}game.save.inventory[{item}] = (game.save.inventory[{item}] or 0) + {amount}"
            return [line] + seq(rest, level, continuation)
        if op == "money":
            return [f"{pad}game.save.money = (game.save.money or 0) + ({payload['amount']})"] + seq(rest, level, continuation)
        if op == "script_move":
            callback = continuation if not rest else "function()\n" + "\n".join(seq(rest, level + 2, continuation)) + f"\n{pad}end"
            return [f"{pad}ow:scriptMove(ow.player, {lua_string(payload['direction'])}, {payload.get('steps', 1)}, {callback})"]
        if op == "done":
            return [invoke(continuation, pad)]
        if op == "engage_trainer":
            # Terminal, like "done": the battle system calls `done` itself
            # once the battle ends, so nothing here may also invoke the
            # continuation -- any node after this one in the same branch
            # would be unreachable, matching ow:engageTrainer's own callers
            # in the vanilla scripts, which never do anything past it either.
            return [f"{pad}ow:engageTrainer(npc, done)"]
        raise ValueError(f"unsupported literal handler operation: {op}")

    return seq(nodes, indent, done_expr)


# Descriptive aliases used by callers and downstream scripts.
load_literal_handler_recipes = load_recipes
extract_literal_handlers = extract_handlers
generate_literal_handlers = generate_handlers
