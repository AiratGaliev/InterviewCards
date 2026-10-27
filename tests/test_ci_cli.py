import tempfile
import unittest
from pathlib import Path

from start_cli import _build_anki_validation_report, build_parser


class AnkiValidationTests(unittest.TestCase):
    def test_valid_anki_basic_export_passes(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "core_anki_basic.txt").write_text(
                "#separator:tab\n"
                "#html:true\n"
                "#notetype:Basic\n"
                "#deck:Interview::Java\n"
                "#tags column:3\n"
                "What is Java?\tA language\tinterview/java\n",
                encoding="utf-8",
            )

            report = _build_anki_validation_report(str(root))

            self.assertEqual(report["error_count"], 0)
            self.assertEqual(report["files_checked"], 1)
            self.assertEqual(report["rows_checked"], 1)

    def test_invalid_tsv_row_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "broken_anki_basic.txt").write_text(
                "#separator:tab\n"
                "#html:true\n"
                "#notetype:Basic\n"
                "#deck:Interview::Java\n"
                "#tags column:3\n"
                "Question\tAnswer\n",
                encoding="utf-8",
            )

            report = _build_anki_validation_report(str(root))

            self.assertGreater(report["error_count"], 0)
            self.assertTrue(any(
                "ожидалось 3 TSV-поля" in message
                for message in report["files"][0]["errors"]
            ))

    def test_empty_directory_fails(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            report = _build_anki_validation_report(temporary_directory)

            self.assertEqual(report["error_count"], 1)
            self.assertEqual(report["files_checked"], 0)

    def test_cli_exposes_ci_validation_arguments(self):
        args = build_parser().parse_args([
            "--validate",
            "--input-dir", "Interview/Materials",
            "--json-report", "reports/validation.json",
        ])

        self.assertTrue(args.validate)
        self.assertEqual(args.input_dir, "Interview/Materials")
        self.assertEqual(args.json_report, "reports/validation.json")


if __name__ == "__main__":
    unittest.main()
