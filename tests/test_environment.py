from environment import Environment
from linter import Flake8Linter
from editor import Editor
from swebench_utils import build_swebench_images


def test_environment():
    env = Environment(
        repo="sqlfluff/sqlfluff",
        base_commit="14e1a23a3166b9a645a16de96f694c77a5d4abb7",
    )

    assert env.execute_command(["echo", "hello world"]) == "hello world\n"
    assert env.execute_command("python3 -c \"import sqlfluff; print(sqlfluff.__version__)\"") == "0.7.0a8\n"

    linter = Flake8Linter()

    editor = Editor(
        env=env,
        linter=linter,
    )

    # editor.try_edit_file(
    #
    # )
