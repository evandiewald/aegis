from typing import TypedDict, Annotated
from difflib import unified_diff
from pathlib import Path
import os


class FileEdits(TypedDict):
    """File edit configuration that will address the issue."""
    filename: Annotated[str, "The file to edit."]
    start_line: Annotated[int, "The start line of the edit."]
    end_line: Annotated[int, "The end line of the edit."]
    new_content: Annotated[str, "The new content to replace the lines with. MUST USE PROPER INDENTATION."]


def generate_git_patch(filename: str, repo_path: str, start_line: int, end_line: int, new_content: str) -> str:
    """
    Generate a Git patch for replacing content between start_line and end_line with new_content.

    Args:
        filename (str): Path to the file
        start_line (int): Starting line number (1-based)
        end_line (int): Ending line number (1-based)
        new_content (str): New content to replace the lines with

    Returns:
        str: Git patch format string
    """
    try:
        # Read the original file
        file_path = Path(os.path.join(repo_path, filename))
        original_text = file_path.read_text()
        original_lines = original_text.splitlines(keepends=True)

        # Create the modified content
        modified_lines = (
            original_lines[:start_line-1] +
            [line + '\n' if not line.endswith('\n') else line
             for line in new_content.splitlines()] +
            original_lines[end_line:]
        )
        header = f"diff --git a/{filename} b/{filename}\n"

        # Generate the diff
        diff = header + ''.join(unified_diff(
            original_lines,
            modified_lines,
            fromfile=f'a/{filename}',
            tofile=f'b/{filename}',
            n=3  # Context lines
        ))

        return diff

    except FileNotFoundError:
        return f"Error: File '{filename}' not found"
    except Exception as e:
        return f"Error: {str(e)}"






