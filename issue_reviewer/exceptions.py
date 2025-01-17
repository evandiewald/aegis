
from typing import List, Union


class CommandFailedException(Exception):
    def __init__(self, command: Union[str, List[str]], output: str):
        self.command = command
        self.output = output

    def __str__(self):
        return f"Command {self.command} failed with output:\n\n{self.output}"


class LintingFailedException(CommandFailedException):
    def __str__(self):
        return f"Linting failed with output:\n\n{self.output}"
