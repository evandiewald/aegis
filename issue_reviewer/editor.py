"""
Simulated file editor for the agent

Constructed from the Environment class


"""
from environment import Environment
from linter import BaseLinter, Flake8Linter
import config as cf

from typing import Tuple, Optional, TypedDict
import os
import base64
import re


class CurrentWindow(TypedDict):
    file_path: str
    line_number: int


class Editor:
    def __init__(
        self,
        env: Environment,
        linter: Optional[BaseLinter] = Flake8Linter(),
        window_buffer: Tuple[int, int] = cf.WINDOW_BUFFER,
    ):
        self.env = env
        self.linter = linter
        self.linter.install(env)
        self.window_buffer = window_buffer
        self.current_window: CurrentWindow | None = None

    def _file_exists(self, file_path: str) -> bool:
        res = self.env.execute_command(["ls", file_path], ignore_errors=True)
        return res == f"{file_path}\n"

    def _read_file(self, file_path: str) -> str:
        return self.env.execute_command(["cat", file_path])

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
    
    # todo: maybe an opportunity to revert files or "undo" last action, e.g. if a change was made but it did not resolve the issue?

    def search_files(self, filename: str, directory: str = ".") -> str:
        """Searches for files in the given directory with the given filename"""
        results = self.env.execute_command(["find", directory, "-name", filename]).splitlines()
        num_results = len(results)
        if num_results == 0:
            # expand the search
            results = self.env.execute_command(["find", directory, "-name", f"*{filename}"]).splitlines()
            num_results = len(results)
        # if still nothing
        if num_results == 0:
            return f"No results found for file {filename} in directory {directory}"
        elif num_results == 1:
            return f"Found file {filename} in directory {directory}: {results[0]}"
        else:
            res = (f"Found {num_results} files matching {filename} in directory {directory}:\n" +
                    "\n".join(results[:cf.MAX_FILE_SEARCH_RESULTS]))
            suffix = f"\n...{num_results-cf.MAX_FILE_SEARCH_RESULTS} more (try narrowing your search)" \
                if num_results > cf.MAX_FILE_SEARCH_RESULTS else ""
            return res + suffix
                    
    def code_search(self, search_term: str, path: str = "."):
        """
        Returns references of a specific term (e.g. a class or function).
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
    
    def view_file(self, file_path: str, line_number: int = 1) -> Tuple[str, int]:
        """View a file contents without setting the current_window"""
        file_lines = [f"{idx+1}: {line}" for idx, line in enumerate(self._read_file(file_path).splitlines())]
        line_number = min(len(file_lines), line_number)
        start_line = max(0, line_number-self.window_buffer[0])
        end_line = min(len(file_lines), line_number+self.window_buffer[1])

        result = f"Opened file: {file_path}\n"
        if start_line > 0:
            result += f"...{start_line} lines above...\n"
        result += "\n".join(file_lines[start_line:end_line])
        if end_line < len(file_lines):
            result += f"\n...{len(file_lines)-end_line} lines below..."
        else:
            result += f"\n--You've reached the end of the file--"
        # return the annotated editor "window" and the adjusted line_number
        return result, line_number

    def open_file(self, file_path: str, line_number: int = 1) -> str:
        """Opens a file to a specific line. Once open, you can scroll_up or scroll_down"""
        if self._file_exists(file_path):
            # if line_number > len(file), it will be adjusted accordingly by view_file
            result, line_number = self.view_file(file_path, line_number)
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

Here is the relevant portion of code:

{self.view_file(tmp_file, line_number=start_line)[0]}

Check your indentation / line numbers and revise with different `new_content`."""
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
