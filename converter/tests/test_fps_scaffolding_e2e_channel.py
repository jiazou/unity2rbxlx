"""
test_fps_scaffolding_e2e_channel.py — Pin the E2E mouse-delta input channel
in both call sites.

The /e2e-test skill drives gameplay fixtures via Studio MCP. MCP's
``user_mouse_input`` tool synthesises ``Delta = (0, 0)``, so mouse-look
code polling ``UserInputService:GetMouseDelta()`` cannot be exercised
without an alternate input source. The documented runtime channel
(``docs/E2E_INPUT_CHANNEL.md``) is workspace attributes
``E2EMouseSeq`` + ``E2EMouseDeltaX`` + ``E2EMouseDeltaY`` — read
additively inside ``updateCamera()``, guarded on a monotonic seq so
each test bump fires exactly once.

This channel must exist in BOTH places:

1. ``converter/scaffolding/fps.py`` — the static FPS scaffolding that
   ships verbatim when a project matches the FPS heuristic and has no
   user-authored Player controller.

2. ``converter/code_transpiler.py`` — the AI system prompt's canonical
   mouse-look snippet. When the AI transpiles a Unity FPS controller,
   the emitted Player.luau follows this pattern.

If the channel is removed from either, this test fires. Without it,
mouse-look gameplay fixtures silently no-op (a class of failure Codex
flagged in the PR-B1 plan review).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from converter.scaffolding.fps import generate_fps_client_script  # noqa: E402
from converter.code_transpiler import _AI_SYSTEM_PROMPT  # noqa: E402


class TestScaffoldingChannel:
    """The static FPS scaffolding script must read the E2E channel."""

    def _script_source(self) -> str:
        return generate_fps_client_script().source

    def test_reads_e2e_mouse_seq(self) -> None:
        src = self._script_source()
        assert 'workspace:GetAttribute("E2EMouseSeq")' in src, (
            "FPS scaffolding updateCamera() must read E2EMouseSeq — "
            "the /e2e-test skill drives mouse-look through this channel. "
            "See docs/E2E_INPUT_CHANNEL.md."
        )

    def test_reads_both_delta_axes(self) -> None:
        src = self._script_source()
        assert 'workspace:GetAttribute("E2EMouseDeltaX")' in src
        assert 'workspace:GetAttribute("E2EMouseDeltaY")' in src

    def test_uses_seq_guard_pattern(self) -> None:
        """The seq guard (``if seq > _lastE2ESeq then``) is what makes
        the channel a queue rather than a mailbox. Without it, two
        identical writes collapse — see docs/E2E_INPUT_CHANNEL.md."""
        src = self._script_source()
        assert "_lastE2ESeq" in src, (
            "Seq-guard variable missing; channel degenerates to a "
            "mailbox and gameplay fixtures will flake."
        )
        assert "> _lastE2ESeq" in src

    def test_is_additive_not_overriding(self) -> None:
        """The injected delta must ADD to the real mouse delta, not
        replace it. Otherwise normal mouse-look breaks the moment a
        test attribute is set."""
        src = self._script_source()
        # The Vector2.new(...) reconstruction includes a `+` operator
        # combining real and injected deltas. Pinning the exact tokens
        # so a regression to `delta = Vector2.new(ex, ey)` fires.
        assert "delta.X + ex" in src
        assert "delta.Y + ey" in src


class TestPromptChannel:
    """The AI mouse-look prompt snippet must teach the same channel."""

    def test_prompt_mentions_e2e_channel(self) -> None:
        assert "E2E test input channel" in _AI_SYSTEM_PROMPT, (
            "Mouse-look prompt no longer documents the E2E channel — "
            "AI-transpiled Player.luau scripts will miss it and "
            "mouse-look gameplay fixtures will fail on non-scaffolding "
            "projects."
        )

    def test_prompt_includes_seq_guard(self) -> None:
        assert "_lastE2ESeq" in _AI_SYSTEM_PROMPT
        assert 'workspace:GetAttribute("E2EMouseSeq")' in _AI_SYSTEM_PROMPT

    def test_prompt_includes_both_delta_axes(self) -> None:
        assert 'workspace:GetAttribute("E2EMouseDeltaX")' in _AI_SYSTEM_PROMPT
        assert 'workspace:GetAttribute("E2EMouseDeltaY")' in _AI_SYSTEM_PROMPT

    def test_prompt_links_to_doc(self) -> None:
        """Prompt must point at the canonical doc so a curious AI (or
        human reader) lands on the right file rather than improvising
        a different channel."""
        assert "docs/E2E_INPUT_CHANNEL.md" in _AI_SYSTEM_PROMPT
