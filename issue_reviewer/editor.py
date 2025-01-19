"""
Simulated file editor for the agent

Constructed from the Environment class


"""
from environment import Environment
from linter import BaseLinter, Flake8Linter
import configuration as cf

from typing import Literal, Optional, TypedDict
import os
import base64


class CurrentWindow(TypedDict):
    file_path: str
    line_number: int


class Editor:
    def __init__(
        self,
        env: Environment,
        linter: Optional[BaseLinter] = Flake8Linter(),
        window_buffer: int = cf.WINDOW_BUFFER,
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

    def _write_file(self, file_path: str, content: str):
        # base64 encoding helps ensure quotes are formatted properly
        content_b64 = base64.b64encode(content.encode()).decode()
        command = f'bash -c "echo "{content_b64}" | base64 -d > "{file_path}""'
        self.env.execute_command(command)

    def _lint_file(self, file_path: str) -> Optional[str]:
        if not self.linter:
            return None
        return self.linter.lint(file_path, self.env)

    def search_files(self, filename: str, directory: str = ".") -> str:
        """Searches for files in the given directory with the given filename"""
        results = self.env.execute_command(["find", directory, "-name", filename]).splitlines()
        if (num_results := len(results)) == 0:
            return f"No results found for file {filename} in directory {directory}."
        elif num_results == 1:
            return f"Found file {filename} in directory {directory}: {results[0]}"
        else:
            return (f"Found {num_results} files matching {filename} in directory {directory}:\n" +
                    "\n".join(results[:cf.MAX_FILE_SEARCH_RESULTS]) +
                    f"\n...{num_results-cf.MAX_FILE_SEARCH_RESULTS} more (try narrowing your search)") \
                if num_results > cf.MAX_FILE_SEARCH_RESULTS else ""

    def code_search(self, search_term: str, path: str = "."):
        """
        Returns references of a specific term (e.g. a class or function).
        Use optional `path` to narrow search to a particular folder/file.
        """
        # todo: support other languages - treesitter?
        # search for references to the class/function
        references_results = self.env.execute_command(["grep", "-Irn", f"{search_term}", path]).splitlines()
        if (num_results := len(references_results)) == 0:
            res = f"\nNo references found for {search_term} at path: {path}."
        elif num_results == 1:
            res = f"\nFound reference to {search_term} at path {path}:\n{references_results[0]}"
        else:
            res = (f"\nFound references to {search_term} in directory {path}:\n" +
                    "\n".join(references_results[:cf.MAX_FILE_SEARCH_RESULTS]) +
                    f"\n...{num_results-cf.MAX_FILE_SEARCH_RESULTS} more (try narrowing your search with the `path` arg)") \
                if num_results > cf.MAX_FILE_SEARCH_RESULTS else ""
        return res

    def open_file(self, file_path: str, line_number: int = 1) -> str:
        """Opens a file to a specific line. Once open, you can scroll_up or scroll_down"""
        file_lines = [f"{idx+1}: {line}" for idx, line in enumerate(self._read_file(file_path).splitlines())]
        start_line = max(0, line_number-self.window_buffer)
        end_line = min(len(file_lines), line_number+self.window_buffer)

        result = f"Opened file: {file_path}\n"
        if start_line > 0:
            result += f"...{start_line} lines above...\n"
        result += "\n".join(file_lines[start_line:end_line])
        if end_line < len(file_lines):
            result += f"\n...{len(file_lines)-end_line} lines below..."
        # set the current window to enable scrolling
        self.current_window = {
            "file_path": file_path,
            "line_number": line_number,
        }
        return result

    def scroll_up(self):
        """
        Scrolls UP in the currently-open file window.
        Can only be used after the `open_file` function is used to open a file.
        """
        if not self.current_window:
            return "No file currently open. Use the `open_file(file_path, line_number)` function to open a file first."
        return self.open_file(self.current_window["file_path"], self.current_window["line_number"] - self.window_buffer*2+1)

    def scroll_down(self):
        """
        Scrolls DOWN in the currently-open file window.
        Can only be used after the `open_file` function is used to open a file.
        """
        if not self.current_window:
            return "No file currently open. Use the `open_file(file_path, line_number)` function to open a file first."
        return self.open_file(self.current_window["file_path"], self.current_window["line_number"] + self.window_buffer*2-1)

    def edit_file(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
        new_content: str,
    ) -> Optional[str]:
        """
        Attempt to edit a file by passing the file_path of the file you want to change (or create),
        the start_line and end_line of the code block that needs to be updated, and the new_content
        you wish to update with. Be careful about spacing and indents in your new_content!
        The changes will be passed through a linter to ensure valid syntax.
        """
        # create a new file if the provided path does not exist
        if not self._file_exists(file_path):
            is_new_file = True
            file_lines = new_content.splitlines()
        else:
            # get the current file contents and update accordingly
            is_new_file = False
            file_lines = self._read_file(file_path).splitlines()
            file_lines[start_line-1:end_line] = new_content.splitlines()

        updated_file = "\n".join(file_lines)
        if not self.linter:
            # write the new file contents to the original file
            self._write_file(file_path, updated_file)
            return f"File {file_path} updated successfully."
        else:
            # write the new file contents to a temporary file
            tmp_file = f"/tmp/{os.path.basename(file_path)}"
            self._write_file(tmp_file, updated_file)
            # linting - replace tmp path with eventual path
            lint_error = self._lint_file(tmp_file)
            if lint_error:
                return f"Failed to {'create' if is_new_file else 'update'} file {file_path} due to linting errors:\n\n{lint_error.replace(tmp_file, file_path)}"
            else:
                # write the new file contents to the original file
                self._write_file(file_path, updated_file)
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
        return self.env.execute_command(["conda", "run", "-n", "testbed", "python3", file_path], ignore_errors=True)

    def reset(self):
        """Resets the editor to its initial state"""
        self.env.reset()
        if self.linter:
            self.linter.install(self.env)  # re-install the linter
        self.current_window = None
        return "Editor reset successfully."
