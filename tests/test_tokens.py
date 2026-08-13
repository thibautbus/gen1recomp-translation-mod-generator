"""Gold's 39 tokens absent from _CORPUS_EXPANSIONS before this backlog step.
Every decision here is justified in pipeline/tokens.py's comments; these
tests lock the resulting behaviour in.
"""
import unittest

from pipeline.tokens import DYNAMIC_TOKEN_RE, TOKEN_RE, check_placeholders, corpus_to_engine


class GoldTokenExpansionTests(unittest.TestCase):
    def test_enemy_joins_the_dynamic_substituents(self):
        self.assertEqual(corpus_to_engine("<ENEMY>\nwants to battle!"), "{ENEMY}\nwants to battle!")
        self.assertTrue(DYNAMIC_TOKEN_RE.fullmatch("{ENEMY}"))

    def test_enemy_placeholder_mismatch_is_caught(self):
        errors = check_placeholders("<ENEMY> wants to battle!", "Bonjour!")
        self.assertIn("missing placeholder {ENEMY} x1", errors)

    def test_sound_and_timing_tokens_render_as_nothing(self):
        for token in (
            "{sound_caught_mon}", "{sound_dex_fanfare_50_79}", "{sound_dex_fanfare_80_109}",
            "{sound_item}", "{sound_slot_machine_start}",
            "{text_pause}", "{text_promptbutton}", "{text_today}", "{text_low}",
        ):
            with self.subTest(token=token):
                self.assertEqual(corpus_to_engine(f"Gotcha!@{token}@"), "Gotcha!")

    def test_poke_compression_byte_expands_like_the_shared_hash_token(self):
        self.assertEqual(corpus_to_engine("<POKE>GEAR"), "POKéGEAR")

    def test_bsp_and_wbr_render_as_a_plain_space(self):
        self.assertEqual(corpus_to_engine("NEW BARK<BSP>TOWN"), "NEW BARK TOWN")
        self.assertEqual(corpus_to_engine("DOUBLON<WBR>VILLE"), "DOUBLON VILLE")

    def test_lf_renders_as_a_newline(self):
        self.assertEqual(corpus_to_engine("TEXT SPEED<LF>BATTLE SCENE"), "TEXT SPEED\nBATTLE SCENE")

    def test_po_and_ke_are_deliberately_left_unmapped(self):
        # Matches this table's existing <PK>/<MN> precedent: naming-screen
        # alphabet fragments, not prose -- see pipeline/tokens.py's comment.
        self.assertEqual(corpus_to_engine("<PO><KE>"), "<PO><KE>")
        self.assertEqual(TOKEN_RE.findall(corpus_to_engine("<PO><KE>")), ["<PO>", "<KE>"])


class GoldBareDynamicTokenTests(unittest.TestCase):
    """Found wiring the release gate against a real Gold pointer
    (65:6092, "Got Y{NUM} for\\n{STRBUF}(S)."):
    RomExtractorGen2.lua:decodeGen2Text decodes Gold's TX_DECIMAL/
    TX_STRINGBUFFER as bare "{NUM}"/"{STRBUF}" (the call site fills the
    value at runtime; the byte stream never names the buffer), unlike
    RBY's own extracted text.lua, which always carries the variable name
    ("{RAM:wBattleMonNick}", "{NUM:wDayCareTotalCost, 2, ...}").
    """

    def test_bare_num_and_strbuf_are_recognised_as_dynamic(self):
        self.assertTrue(DYNAMIC_TOKEN_RE.fullmatch("{NUM}"))
        self.assertTrue(DYNAMIC_TOKEN_RE.fullmatch("{STRBUF}"))

    def test_bare_engine_side_does_not_flag_a_named_corpus_translation(self):
        # The real case: Gold's engine-decoded English is bare; the corpus
        # (via {text_decimal ...}/{text_ram ...}) carries the variable.
        errors = check_placeholders("Got ¥{NUM} for\nSTRBUF(S).".replace("STRBUF", "{STRBUF}"),
                                     "Recu: {NUM:hMoneyTemp, 3, 6}¥\npour {RAM:wStringBuffer2}.")
        self.assertEqual(errors, [])

    def test_strbuf_is_compared_as_the_ram_family(self):
        self.assertEqual(check_placeholders("Hi {STRBUF}!", "Salut {RAM:wStringBuffer1}!"), [])
        self.assertEqual(check_placeholders("Hi {STRBUF}!", "Salut !"),
                          ["missing placeholder {RAM} x1"])

    def test_a_genuinely_missing_dynamic_token_is_still_caught(self):
        errors = check_placeholders("Hi {NUM}!", "Salut !")
        self.assertEqual(errors, ["missing placeholder {NUM} x1"])

    def test_a_genuinely_extra_dynamic_token_is_still_caught(self):
        errors = check_placeholders("Hi!", "Salut {NUM}!")
        self.assertEqual(errors, ["unexpected placeholder {NUM} x1"])

    def test_swapping_which_named_ram_buffer_is_referenced_is_still_caught(self):
        # Regression: family-level comparison alone (added for Gold's bare
        # tokens) must not let RBY's own named buffers be swapped for a
        # different one silently -- same family, same count, wrong buffer.
        errors = check_placeholders(
            "{RAM:wBattleMonNick} fainted!", "{RAM:wPlayerName} s'est evanoui !",
        )
        self.assertEqual(errors, [
            "missing placeholder {RAM:wBattleMonNick} x1",
            "unexpected placeholder {RAM:wPlayerName} x1",
        ])


if __name__ == "__main__":
    unittest.main()
