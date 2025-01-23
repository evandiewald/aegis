"""
Simulated file editor for the agent

Constructed from the Environment class


"""
from environment import Environment
from linter import BaseLinter, Flake8Linter
import config as cf
from swebench.harness.constants.constants import SWEbenchInstance

from swebench_utils import get_test_script

from testbeds.swebench.log_parsers import parse_log
from testbeds.schema import TestResult

from typing import Tuple, Optional, TypedDict, List, Dict
import os
import base64
import re
from collections import defaultdict


class CurrentWindow(TypedDict):
    file_path: str
    line_number: int


class Editor:
    def __init__(
        self,
        env: Environment,
        instance: SWEbenchInstance,
        linter: Optional[BaseLinter] = Flake8Linter(),
        window_buffer: Tuple[int, int] = cf.WINDOW_BUFFER,
    ):
        self.env = env
        self.instance = instance
        self.linter = linter
        self.linter.install(env)
        self.window_buffer = window_buffer
        self.current_window: CurrentWindow | None = None

        # track edit history for "undo" functionality
        self._file_history = defaultdict(list)

        self._test_files: List[str] = []

    def _add_test_file(self, file_path: str):
        """
        # adapted from https://github.com/aorwall/moatless-tools/blob/main/moatless/index/code_index.py#L576
        Find the test file related to the provided file path.

        Test files should match the pattern "test_[filename].py" or "[filename]_test.py".
        If there are multiple matches, the one with the most similar directory path is picked.
        """
        filename = os.path.basename(file_path)
        stem = filename.split(".")[0]
        dirname = os.path.dirname(file_path)
        test_patterns = [f"*test_{filename}", f"*{stem}_test.py", f"*{stem}/tests.py"]  # last one is for django

        # if this is already a test file, add it directly
        if "test" in file_path and file_path not in self._test_files:
            self._test_files.append(file_path)
        
        else:
            matched_files = [] 
            for pat in test_patterns:
                matched_files += self._find_files(pat)
            if not matched_files:
                test_file = None

            if len(matched_files) == 1:
                test_file = matched_files[0]

            else:

                # Find the test file with the most similar directory path
                best_match = None
                best_match_score = float("inf")
                for test_file in matched_files:
                    test_dirname = os.path.dirname(test_file)
                    common_prefix = os.path.commonprefix([dirname, test_dirname])
                    score = len(dirname) - len(common_prefix)
                    if score < best_match_score:
                        best_match = test_file
                        best_match_score = score

                test_file = best_match

            if test_file not in self._test_files and test_file is not None:
                self._test_files.append(test_file)
        return self._test_files

    def add_test_file(self, test_file: str):
        """Explicitly add a test_file to be run automatically when edits are made."""
        if not self._file_exists(test_file):
            raise ValueError(f"Test file {test_file} does not exist!")
        if test_file in self._test_files:
            return f"Test file {test_file} already in test_files."
        self._test_files.append(test_file)
        test_results = self.run_tests()
        return f"Test file {test_file} added successfully.\n\n{test_results}\n"

    def _get_test_summary(self, test_result: Dict[str, List[TestResult]]) -> str:
        
        passed = [tr for test_file in test_result for tr in test_result[test_file] if tr.status == "PASSED"]
        failed = [tr for test_file in test_result for tr in test_result[test_file] if tr.status != "PASSED"]
        summary = ("Summary of Test Results:\n"
                   f"Test Files: {list(test_result.keys())}\n"
                   f"{len(passed)} passed\n"
                   f"{len(failed)} failed\n\n")
        
        if failed:
            summary += "Errors:\n\n"
            for failed_res in failed[:5]:
                summary += "-" * 10 + failed_res.name + "-" * 10
                if failed_res.failure_output:
                    failure_trimmed = "\n".join(failed_res.failure_output.splitlines()[:10]) + "\n...(output trimmed)..."
                    summary += f"\n\n{failure_trimmed}\n\n"

        return summary

    def run_tests(self, test_files: Optional[List[str]] = None) -> Dict[str, List[TestResult]]:
        """Runs tests for file paths at self._test_files"""
        test_files = test_files or self._test_files
        result = {}
        for test_file in test_files:
            test_command = ["conda", "run", "-n", "testbed"] + get_test_script(self.instance, [test_file])
            test_output = self.env.execute_command(test_command, ignore_errors=True)
            result[test_file] = parse_log(test_output, self.instance["repo"])
        return self._get_test_summary(result)

    def _file_exists(self, file_path: str) -> bool:
        res = self.env.execute_command(["ls", file_path], ignore_errors=True)
        return res == f"{file_path}\n"

    def _read_file(self, file_path: str) -> str:
        return self.env.execute_command(["cat", file_path])
    
    def _get_file_lines(self, file_path: str) -> List[str]:
        return [f"{idx+1}: {line}" for idx, line in enumerate(self._read_file(file_path).splitlines())]

    def _write_file(self, file_path: str, content: str, chunk_size: int = 1024):  # 1KB chunks by default
        # First, truncate/create the file
        self.env.execute_command(f'bash -c "true > "{file_path}""')
        
        # Convert the entire content to bytes
        content_bytes = content.encode()
        
        # Process the content in chunks
        for i in range(0, len(content_bytes), chunk_size):
            chunk = content_bytes[i:i + chunk_size]
            chunk_b64 = base64.b64encode(chunk).decode()
            
            # Append each chunk to the file using >>
            command = f'bash -c "echo "{chunk_b64}" | base64 -d >> "{file_path}""'
            self.env.execute_command(command)

    def _lint_file(self, file_path: str) -> Optional[str]:
        if not self.linter:
            return None
        return self.linter.lint(file_path, self.env)
    
    def create(self, file_path: str, file_text: str) -> str:
        """Create a new file with `file_text` as the contents."""
        if file_text is None:
            raise ValueError("Parameter `file_text` is required for command: create")
        self._write_file(file_path, file_text)
        self._file_history[file_path].append(file_text)

        success_msg = f"File created successfully at: {file_path}"

        # run tests
        self._add_test_file(file_path)
        test_results = self.run_tests()
        success_msg += f"\n\n{test_results}"

        return success_msg
    
    def insert(self, file_path: str, insert_line: int, new_str: str):
        """Implement the insert command, which inserts new_str at the specified line in the file content."""
        if not self._file_exists(file_path):
            return f"File `{file_path}` does not exist. Use the `create` command to create a new file."
        
        file_text = self._read_file(file_path).expandtabs()
        new_str = new_str.expandtabs()
        file_text_lines = file_text.split("\n")
        n_lines_file = len(file_text_lines)

        if insert_line < 0 or insert_line > n_lines_file:
            raise ValueError(
                f"Invalid `insert_line` parameter: {insert_line}. It should be within the range of lines of the file: {[0, n_lines_file]}"
            )

        new_str_lines = new_str.splitlines()
        new_file_text_lines = (
            file_text_lines[:insert_line]
            + new_str_lines
            + file_text_lines[insert_line:]
        )

        new_file_text = "\n".join(new_file_text_lines)

        self._write_file(file_path, new_file_text)
        self._file_history[file_path].append(file_text)

        success_msg = f"The file {file_path} has been edited.\n"

        success_msg += self.view_file(file_path, max(1, insert_line - cf.SNIPPET_LINES + 1), insert_line + len(new_file_text_lines))
        success_msg += "\nReview the changes and make sure they are as expected (correct indentation, no duplicate lines, etc). Edit the file again if necessary."
        
        # run tests
        self._add_test_file(file_path)
        test_results = self.run_tests()
        success_msg += f"\n\n{test_results}"

        return success_msg
    
    def undo_edit(self, file_path: str):
        """Implement the undo_edit command."""
        if not self._file_history[file_path]:
            return f"No edit history found for {file_path}."

        old_text = self._file_history[file_path].pop()
        self._write_file(file_path, old_text)

        return f"Last edit to {file_path} undone successfully."
    
    def _find_files(self, path_pattern: str, directory: str = ".") -> List[str]:
        return [p[len("./"):] for p in self.env.execute_command(["find", directory, "-path", path_pattern]).splitlines() if p.startswith("./")]
    
    def search_files(self, path_pattern: str, directory: str = ".") -> str:
        """Searches for files in the given directory with the given path_pattern"""
        results = self._find_files(path_pattern, directory)
        num_results = len(results)
        if num_results == 0:
            # expand the search
            results = self._find_files(f"*{path_pattern}", directory)
            num_results = len(results)
        # if still nothing
        if num_results == 0:
            return f"No results found for path_pattern {path_pattern} in directory {directory}"
        elif num_results == 1:
            return f"Found file: {results[0]}"
        else:
            res = (f"Found {num_results} files matching {path_pattern} in directory {directory}:\n" +
                    "\n".join(results[:cf.MAX_FILE_SEARCH_RESULTS]))
            suffix = f"\n...{num_results-cf.MAX_FILE_SEARCH_RESULTS} more (try narrowing your search)" \
                if num_results > cf.MAX_FILE_SEARCH_RESULTS else ""
            return res + suffix
                    
    def code_search(self, search_term: str, path: str = "."):
        """
        Returns explicit references of a specific term (grep-style).
        If you are looking for the instantiation of a class/function, use the appropriate prefix to get the most direct result, e.g. `def my_function` or `class MyClass`.
        Use optional `path` to narrow search to a particular folder/file.
        """
        # todo: support other languages - treesitter?
        # search for references to the class/function
        # only searching .py files for now
        references_results = self.env.execute_command(["grep", "-Irn", "--include=*.py", f"{search_term}", path], ignore_errors=True).splitlines()
        num_results = len(references_results)
        if num_results == 0:
            res = f"\nNo references found for `{search_term}` at path: {path}"
        elif num_results == 1:
            res = f"\nFound 1 reference to `{search_term}` at path {path}:\n{references_results[0]}"
        else:
            # push any files with `test` to the bottom of the list
            references_results.sort(key=lambda x: "test" in x.lower())

            res = (f"\nFound {num_results} references to `{search_term}` in directory {path}:\n" +
                    "\n".join(references_results[:cf.MAX_FILE_SEARCH_RESULTS]))
            suffix = f"\n...{num_results-cf.MAX_FILE_SEARCH_RESULTS} more (try narrowing your search with the `path` arg)" if num_results > cf.MAX_FILE_SEARCH_RESULTS else ""
            res = res + suffix
        return res
    
    def view_file(self, file_path: str, start_line: int, end_line: int) -> str:
        """View a file contents without setting the current_window"""
        file_lines = self._get_file_lines(file_path)

        result = f"Opened file: {file_path}\n"
        if start_line > 1:
            result += f"...{start_line-1} lines above...\n"
        result += "\n".join(file_lines[max(0, start_line-1):min(end_line, len(file_lines))])
        if end_line < len(file_lines):
            result += f"\n...{len(file_lines)-end_line} lines below..."
        else:
            result += f"\n--You've reached the end of the file--"
        # return the annotated editor "window"
        return result

    def open_file(self, file_path: str, line_number: int) -> str:
        """
        Opens a file to a specific line. Once open, you can scroll_up or scroll_down. 
        It's highly recommended that you run `code_search` before using this tool, in order to locate the specific line you're looking for.
        """
        if self._file_exists(file_path):
            num_lines = len(self._get_file_lines(file_path))
            # adjust the line_number if needed
            line_number = min(num_lines, line_number)
            start_line = max(0, line_number-self.window_buffer[0])
            end_line = min(num_lines, line_number+self.window_buffer[1])
            result = self.view_file(file_path, start_line, end_line)
            # set the current window to enable scrolling
            self.current_window = {
                "file_path": file_path,
                "line_number": line_number,
            }
            return result
        else:
            return f"File {file_path} does not exist."

    def scroll_up(self):
        """
        Scrolls UP in the currently-open file window.
        Can only be used after the `open_file` function is used to open a file.
        """
        if not self.current_window:
            return "No file currently open. Use the `open_file(file_path, line_number)` function to open a file first."
        return self.open_file(self.current_window["file_path"], self.current_window["line_number"] - sum(self.window_buffer)+1)

    def scroll_down(self):
        """
        Scrolls DOWN in the currently-open file window.
        Can only be used after the `open_file` function is used to open a file.
        """
        if not self.current_window:
            return "No file currently open. Use the `open_file(file_path, line_number)` function to open a file first."
        return self.open_file(self.current_window["file_path"], self.current_window["line_number"] + sum(self.window_buffer)-1)

    def str_replace(
        self,
        file_path: str,
        old_str: str,
        new_str: str,
    ) -> str:
        """
        Replaces old_str with new_str in the file content of file_path.
        """
        # Read the file content
        file_content = self._read_file(file_path).expandtabs()
        old_str = old_str.expandtabs()
        new_str = new_str.expandtabs() if new_str is not None else ""

        # Check if old_str is unique in the file
        occurrences = file_content.count(old_str)
        if occurrences == 0:
            raise ValueError(
                f"No replacement was performed, `old_str` did not appear verbatim in {file_path}. You may want to view the file again."
            )
        elif occurrences > 1:
            file_content_lines = file_content.split("\n")
            lines = [
                idx + 1
                for idx, line in enumerate(file_content_lines)
                if old_str in line
            ]
            raise ValueError(
                f"No replacement was performed. Multiple occurrences of old_str `{old_str}` in lines {lines}. Please ensure it is unique"
            )

        # Replace old_str with new_str
        new_file_content = file_content.replace(old_str, new_str)

        # Write the new content to the file
        self._write_file(file_path, new_file_content)

        # Save the content to history and add relevant test file
        self._file_history[file_path].append(file_content)

        # Create a snippet of the edited section
        replacement_line = file_content.split(old_str)[0].count("\n")
        start_line = max(0, replacement_line - cf.SNIPPET_LINES)
        end_line = replacement_line + cf.SNIPPET_LINES + new_str.count("\n")

        # Prepare the success message
        success_msg = f"The file {file_path} has been edited.\n"
        success_msg += self.view_file(file_path, start_line, end_line)
        success_msg += "\nReview the changes and make sure they are as expected. Edit the file again if necessary."

        self._add_test_file(file_path)
        test_results = self.run_tests()
        success_msg += f"\n\n{test_results}"

        return success_msg
    
    def edit_file(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
        new_content: str,
    ) -> str:
        """
        Attempt to edit a file by passing the file_path of the file you want to change (or create),
        the start_line and end_line of the code block that needs to be updated, and the new_content
        you wish to update with. Be careful about spacing and indents in your new_content!
        The changes will be passed through a linter to ensure valid syntax.
        """
        new_errors = None
        # create a new file if the provided path does not exist
        if not self._file_exists(file_path):
            is_new_file = True
            file_lines = new_content.splitlines()
            updated_content = "\n".join(file_lines)
        else:
            # get the current file contents and update accordingly
            is_new_file = False
            original_end_line = end_line
            original_file_lines = self._read_file(file_path).splitlines()
            
            # Store the original attempt for potential error message
            file_lines = original_file_lines.copy()
            file_lines[start_line-1:end_line] = new_content.splitlines()
            original_attempt = "\n".join(file_lines)

            # Try with incrementing end_line if needed
            for retry in range(cf.MAX_RETRIES_EDIT_FILE):
                current_end_line = end_line + retry
                file_lines = original_file_lines.copy()
                file_lines[start_line-1:current_end_line] = new_content.splitlines()
                updated_content = "\n".join(file_lines)

                if not self.linter or not file_path.endswith(".py"):
                    break

                # Check existing errors
                existing_errors = []
                existing_lint_msg = self._lint_file(file_path)
                if existing_lint_msg:
                    for lint_msg in existing_lint_msg.splitlines():
                        match = re.search(r'(?:[^:]+:){2}\s*\w+\s+(.+)', lint_msg)
                        if match:
                            existing_errors.append(match.group(1))

                # Test the current attempt
                tmp_file = f"/tmp/{os.path.basename(file_path)}"
                self._write_file(tmp_file, updated_content)
                lint_error = self._lint_file(tmp_file)
                new_errors = [e for e in lint_error.splitlines() if not any([pe in e for pe in existing_errors])] if lint_error else []
                
                if not new_errors:
                    # Found a working solution
                    break
                
                if retry == cf.MAX_RETRIES_EDIT_FILE - 1:
                    # If we've exhausted all retries, use the original attempt for the error message
                    updated_content = original_attempt
                    current_end_line = original_end_line
                    tmp_file = f"/tmp/{os.path.basename(file_path)}"
                    self._write_file(tmp_file, updated_content)
                    lint_error = self._lint_file(tmp_file)
                    new_errors = [e for e in lint_error.splitlines() if not any([pe in e for pe in existing_errors])] if lint_error else []

        if not self.linter or not file_path.endswith(".py"):
            self._write_file(file_path, updated_content)
            return f"File {file_path} updated successfully."
        elif new_errors:
            lint_msg = "\n".join(new_errors)
            return f"""Failed to {'create' if is_new_file else 'update'} file {file_path} due to linting errors:
            
{lint_msg.replace(tmp_file, file_path)}

BEFORE attempted edit:

{self.view_file(file_path, start_line - 2, end_line + 2)}

AFTER attempted edit:

{self.view_file(tmp_file, start_line - 2, end_line + 2)}

Check your indentation & line numbers. You may need to adjust the start_line / end_line if you inadvertently cutoff some text, like a function definition or return statement."""
        else:
            self._write_file(file_path, updated_content)
            return f"File {file_path} {'created' if is_new_file else 'updated'} successfully."

    def rm(self, file_path: str):
        """Removes a file from the environment"""
        if not self._file_exists(file_path):
            return f"File {file_path} does not exist."
        self.env.execute_command(["rm", file_path])
        return f"File {file_path} removed successfully."

    def ls(self, directory: Optional[str] = None):
        """Lists the contents of the current directory or the provided directory"""
        return self.env.ls(directory)

    def execute_command(self, command: str):
        """Executes a command in the environment"""
        return self.env.execute_command(command, ignore_errors=True)

    def run_python_file(self, file_path: str):
        """Runs a python file in the environment"""
        output = self.env.execute_command(["conda", "run", "-n", "testbed", "python3", file_path], ignore_errors=True)
        # trim the output - some modules, like sphinx, have incredibly long outputs
        output_lines = output.splitlines()
        if len(output_lines) > cf.MAX_OUTPUT_LINES:
            output = "\n".join(output_lines[:cf.MAX_OUTPUT_LINES // 2]) + \
                "\n[...logs trimmed...]\n" + \
                "\n".join(output_lines[-cf.MAX_OUTPUT_LINES // 2:])
        return output

    def reset(self):
        """Resets the editor to its initial state"""
        self.env.reset()
        if self.linter:
            self.linter.install(self.env)  # re-install the linter
        self.current_window = None
        return "Editor reset successfully."
