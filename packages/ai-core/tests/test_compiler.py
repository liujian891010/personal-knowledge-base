from __future__ import annotations

import unittest

from ai_core import AiWikiPage, SourceNote, answer_ai_wiki, compile_ai_wiki


class AiWikiCompilerTests(unittest.TestCase):
    def test_compile_ai_wiki_generates_index_and_summary_pages(self) -> None:
        result = compile_ai_wiki(
            [
                SourceNote(
                    file_id="file-live",
                    path="Notes/Live.md",
                    text="# Live Note\n\nThis is the first paragraph.\n\n## Detail\nSee [[Other Note]].\n",
                    content_hash="sha256:live",
                )
            ],
            generated_at="2026-05-11T00:00:00Z",
        )

        self.assertEqual(result.schema_version, "v1")
        self.assertEqual(result.source_count, 1)
        self.assertEqual(result.artifact_count, 1)
        self.assertEqual(result.index_path, ".ai/index.md")
        self.assertIn("[[Live Note]]", result.index_text)
        artifact = result.artifacts[0]
        self.assertEqual(artifact.title, "Live Note")
        self.assertTrue(artifact.path.startswith(".ai/wiki/live-note-"))
        self.assertIn("This is the first paragraph.", artifact.text)
        self.assertIn("[[Other Note]]", artifact.text)
        self.assertIn("source_file_id: file-live", artifact.text)

    def test_answer_ai_wiki_returns_citations(self) -> None:
        result = answer_ai_wiki(
            "What mentions knowledge?",
            [
                AiWikiPage(
                    file_id="wiki-live",
                    path=".ai/wiki/live.md",
                    title="Live Note",
                    text="# Live Note\n\nKnowledge base notes explain local search.\n",
                )
            ],
        )

        self.assertEqual(result.schema_version, "v1")
        self.assertEqual(result.citation_count, 1)
        self.assertEqual(result.citations[0].path, ".ai/wiki/live.md")
        self.assertIn("Knowledge base", result.answer)


if __name__ == "__main__":
    unittest.main()
