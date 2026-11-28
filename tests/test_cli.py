import contextlib
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from oneploom.cli import main, parse_args
from oneploom.project import Problem, choose_project


class Terminal(io.StringIO):
    def isatty(self):
        return True


class ParsingTests(unittest.TestCase):
    def test_default_start_and_explicit_start_agree(self):
        self.assertEqual(vars(parse_args([])), vars(parse_args(["start"])))

    def test_invalid_inputs_do_not_become_start(self):
        for argv in [
            [""], ["strat"], ["--project", "."], ["start", "--proj", "."],
            ["start", "--project", ""], ["start", "--project", "  "],
            ["start", "--project", "A", "--project=B"], ["start", "--project=A", "--select"],
            ["start", "--json"], ["resume", "--json"], ["resume", ""],
            ["projects", "list", "--select"], ["projects", "list", "--", "ignored"],
        ]:
            with self.subTest(argv=argv), self.assertRaises(Problem) as caught:
                parse_args(argv)
            self.assertEqual(caught.exception.reason, "invalid_input")
            self.assertEqual(caught.exception.code, 2)

    def test_forwarded_arguments_remain_an_array(self):
        tail = ["--project", "다른 대상", "", 'a"b', "%VALUE%", "--json", "--", "x y"]
        args = parse_args(["resume", "session-id", "--project", "C:/작업 A", "--", *tail])
        self.assertEqual(args.project, "C:/작업 A")
        self.assertEqual(args.session, "session-id")
        self.assertEqual(args.forwarded, tail)
        self.assertFalse(args.json)

    def test_help_and_version_do_not_open_project_or_catalog(self):
        for argv in [["--help"], ["--version"], ["start", "--help"], ["resume", "--help"], ["projects", "list", "--help"]]:
            with self.subTest(argv=argv), patch("oneploom.cli.catalog_path") as catalog, patch("oneploom.cli.resolve_project") as resolve, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    main(argv)
                self.assertEqual(caught.exception.code, 0)
                catalog.assert_not_called()
                resolve.assert_not_called()

    def test_projects_without_action_shows_help(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["projects"]), 0)
        self.assertIn("list", output.getvalue())

    def test_invalid_input_does_not_echo_secret_value(self):
        with contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(main(["start", "--unknown=SECRET_VALUE"]), 2)
        self.assertNotIn("SECRET_VALUE", output.getvalue())

    def test_machine_error_is_single_json_without_prompt(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["start", "--json"]), 2)
        value = json.loads(output.getvalue())
        self.assertEqual(value["reason"], "invalid_input")

    def test_resume_parse_error_keeps_requested_operation(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["resume", "--json"]), 2)
        self.assertEqual(json.loads(output.getvalue())["operation"], "resume")

    def test_json_in_forwarded_arguments_is_not_consumed(self):
        with patch("oneploom.cli.os.name", "posix"), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["start", "--", "--json"]), 5)
        self.assertNotIn('"status"', output.getvalue())


class SelectionTests(unittest.TestCase):
    def test_selection_is_exact_and_invalid_number_reprompts(self):
        paths = ["C:/A/project", "D:/B/project"]
        output = io.StringIO()
        self.assertEqual(choose_project(paths, Terminal("3\n-1\nx\n2\n"), output), paths[1])
        self.assertIn("C:/A/project", output.getvalue())
        self.assertIn("D:/B/project", output.getvalue())

    def test_empty_and_q_cancel_even_one_item(self):
        for text in ["\n", "q\n", "  \n"]:
            with self.subTest(text=text), self.assertRaises(Problem) as caught:
                choose_project(["C:/A"], Terminal(text), io.StringIO())
            self.assertEqual(caught.exception.code, 4)

    def test_eof_and_noninteractive_require_input(self):
        for stream in [Terminal(""), io.StringIO("1\n")]:
            with self.subTest(stream=stream), self.assertRaises(Problem) as caught:
                choose_project(["C:/A"], stream, io.StringIO())
            self.assertEqual(caught.exception.code, 3)

    def test_interrupt_is_cancelled(self):
        stream = Terminal()
        with patch.object(stream, "readline", side_effect=KeyboardInterrupt), self.assertRaises(Problem) as caught:
            choose_project(["C:/A"], stream, io.StringIO())
        self.assertEqual(caught.exception.code, 4)

    def test_displayed_paths_are_not_reloaded(self):
        paths = ["C:/A", "C:/B"]
        stream = Terminal("2\n")
        self.assertEqual(choose_project(paths, stream, io.StringIO()), "C:/B")


@unittest.skipUnless(os.name == "nt", "실제 Windows 경로 검증")
class RoutingTests(unittest.TestCase):
    def test_explicit_failure_never_reads_catalog(self):
        with patch("oneploom.cli.catalog_path") as catalog, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertNotEqual(main(["start", "--project", "C:/oneploom-test-missing-path", "--preview", "--json"]), 0)
        catalog.assert_not_called()
        self.assertNotIn("project_root", json.loads(output.getvalue()))

    def test_selection_in_json_never_prompts_or_reads_catalog(self):
        with patch("oneploom.cli.catalog_path") as catalog, patch("oneploom.cli.choose_project") as choose, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["start", "--select", "--preview", "--json"]), 3)
        self.assertEqual(json.loads(output.getvalue())["reason"], "project_selection_required")
        catalog.assert_not_called()
        choose.assert_not_called()

    def test_current_project_does_not_depend_on_catalog(self):
        root = Path.cwd()
        with patch("oneploom.cli.resolve_project", return_value=root), patch("oneploom.cli.find_module") as module, patch("oneploom.cli.resolve_connection", return_value=None) as connection, patch("oneploom.cli.catalog_path") as catalog, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["start", "--preview", "--json"]), 5)
        catalog.assert_not_called()
        connection.assert_called_once_with(root, module.return_value)
        result = json.loads(output.getvalue())
        self.assertEqual(result["reason"], "runtime_not_implemented")
        self.assertEqual(result["status"], "unsupported")

    def test_interrupted_connection_reports_cancelled(self):
        with patch("oneploom.cli.resolve_project", return_value=Path.cwd()), patch("oneploom.cli.find_module"), patch("oneploom.cli.resolve_connection", side_effect=KeyboardInterrupt), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["start", "--preview", "--json"]), 4)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["reason"], "user_cancelled")
        self.assertNotIn("wiki_root", result)


if __name__ == "__main__":
    unittest.main()
