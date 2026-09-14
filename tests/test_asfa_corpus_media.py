"""Every media tag a real game declares, resolved and counted.

The fixtures in `ink_engine`'s own resolver tests prove the MECHANISM
works. They cannot show that a game's own rules cover its own corpus: a
resolver answering None for some subset degrades exactly like no resolver
at all, and a few dozen missing pictures among thousands of tags is
invisible without counting them.

So this counts them, against the real ASFA game, from both a directory
and a bundle. It is skipped wherever that game is not present, which is
anywhere but this developer's machine.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest import TestCase, skipUnless

from ink_engine.game_source import DirectoryGameSource, ZipGameSource

ASFA = Path("/Volumes/Support-8tb/Gallery/Quickbbs/Albums/interactive_fiction/asfa")
BUNDLE = Path("/Volumes/Support-8tb/Gallery/if_games/asfa/asfa.zip")

#: A tag on its own line: only whitespace before the `#`.
TAG = re.compile(r"^\s*#\s*(?:image|video):\s*(\S.*?)\s*$")

#: A tag written INSIDE a choice's brackets, which is how a picker offers
#: a picture per option (`+ [Blonde # image: x.jpg] -> ...`). The tag ends
#: at the bracket, so it cannot be read to end of line like the one above.
CHOICE_TAG = re.compile(r"^\s*[+*].*?#\s*(?:image|video):\s*(\S.*?)\s*\]")

#: Tags no resolver can answer, and cannot be made to: each names art the
#: ORIGINAL game references but never shipped (`ellieroom*`, `jesse10`,
#: `home3`), confirmed absent from its own `Images/` tree rather than only
#: from the conversion's. All carry the `!` prefix, the game's own marker
#: for a tag flagged during conversion.
#:
#: Counted rather than excluded: the number moving either way is the
#: signal. It was 30 until the `genericsex/` tags were corrected -- those
#: named files with UNDERSCORES where the original's tree uses SPACES
#: (`be c.mp4`, 54 files there and 78 in its Explicit/, not one with an
#: underscore).
KNOWN_UNRESOLVED = 6


def _literal_tags() -> list[tuple[str, str]]:
    """Return every tag that names a fixed path, as `(file, tag)`.

    Interpolated tags (`{person_value_now(...)}`) name a different file
    per game state and are covered by `asfa_checks/missing_outfit_paths.py`,
    which expands them across every reachable value.
    """
    tags = []
    for story in sorted(ASFA.glob("*.ink")):
        for line in story.read_text(errors="replace").splitlines():
            if line.lstrip().startswith("//"):
                continue
            match = TAG.match(line) or CHOICE_TAG.match(line)
            if match and "{" not in match.group(1):
                tags.append((story.name, match.group(1)))
    return tags


def _unresolved(source, tags) -> list[tuple[str, str]]:
    """Return the tags this source cannot answer."""
    resolver = sys.modules["asfa.image_resolver"]
    return [
        (story, tag)
        for story, tag in tags
        if resolver.resolve_tag(kind="image", tag=tag, source=source, reference=lambda path: path) is None
    ]


@skipUnless(ASFA.is_dir(), "the real ASFA game is not present")
class AsfaCorpusTests(TestCase):
    """The whole corpus, not a sample."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ASFA.parent))
        import asfa.image_resolver  # noqa: F401  (registers the module _unresolved reads)

        cls.tags = _literal_tags()

    def test_the_corpus_is_the_size_it_was(self):
        """A collapse in the tag count would make every other count here
        meaningless without failing anything."""
        self.assertGreater(len(self.tags), 1500)  # 1,591 today

    def test_a_directory_resolves_all_but_the_known_absent(self):
        unresolved = _unresolved(DirectoryGameSource(ASFA), self.tags)
        self.assertEqual(len(unresolved), KNOWN_UNRESOLVED, f"changed: {unresolved[:5]}")

    @skipUnless(BUNDLE.is_file(), "no built bundle to check against")
    def test_a_bundle_resolves_exactly_what_the_directory_does(self):
        """A bundle reads the same files through a different source; a
        difference is a port defect in one of the two."""
        bundle = ZipGameSource(BUNDLE)
        self.addCleanup(bundle.close)
        self.assertEqual(
            len(_unresolved(bundle, self.tags)),
            len(_unresolved(DirectoryGameSource(ASFA), self.tags)),
        )
