"""
Simulated file editor for the agent

Constructed from the Environment class


"""
from environment import Environment
from linter import BaseLinter, Flake8Linter

from typing import Literal, Optional
import os


class Editor:
    def __init__(
        self,
        env: Environment,
        linter: Optional[BaseLinter] = Flake8Linter(),
    ):
        self.env = env
        self.linter = linter
        self.linter.install(env)

    def _file_exists(self, file_path: str) -> bool:
        return self.env.execute_command(["test", "-f", file_path]) == 0

    def _read_file(self, file_path: str) -> str:
        return self.env.execute_command(["cat", file_path])

    def _write_file(self, file_path: str, content: str):
        self.env.execute_command(["echo", f"'{content}' > {file_path}"])

    def _lint_file(self, file_path: str) -> Optional[str]:
        if not self.linter:
            return None
        return self.linter.lint(file_path, self.env)

    def try_edit_file(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
        new_content: str,
    ) -> Optional[str]:
        # get the current file contents
        file_lines = self._read_file(file_path).splitlines()
        # replace the lines
        file_lines[start_line-1:end_line] = new_content.splitlines()
        if not self.linter:
            # write the new file contents to the original file
            updated_file = "\n".join(file_lines)
            self._write_file(file_path, updated_file)
            return None
        else:
            # write the new file contents to a temporary file
            updated_file = "\n".join(file_lines)
            tmp_file = f"/tmp/{os.path.basename(file_path)}"
            self._write_file(file_path, updated_file)
            # lint
            lint_error = self._lint_file(tmp_file)
            if lint_error:
                return lint_error
            else:
                # write the new file contents to the original file
                self._write_file(file_path, updated_file)
                return None
