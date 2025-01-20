
from abc import ABC, abstractmethod
from typing import List, Union, Optional

from environment import Environment
from exceptions import CommandFailedException, LintingFailedException


class BaseLinter:

    def __init__(
        self,
        name: str,
        install_command: Union[str, List[str]],
        lint_command: List[str],
    ):
        self.name = name
        self.install_command = install_command
        self.lint_command = lint_command

    def install(self, env: Environment):
        env.execute_command(self.install_command)

    def lint(self, file_path: str, env: Environment) -> Optional[str]:
        """Returns None if linting succeeds."""
        try:
            env.execute_command(self.lint_command + [file_path])
            return None
        except CommandFailedException as e:
            # don't want to raise it, but return the results
            return e.output


class Flake8Linter(BaseLinter):
    def __init__(self):
        super().__init__(
            name="flake8",
            install_command=["pip", "install", "flake8"],
            lint_command=["flake8", "--isolated", "--select=F821,F822,F831,E111,E112,E113,E999,E902"]
            # lint_command=["flake8", "--ignore=W292,E302,E305"],
        )



