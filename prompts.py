PROMPT_FAULT_LOCALIZATION_SEARCH = """You are a senior software engineer working on addressing the following issue:

---BEGIN ISSUE---
{problem_statement}
---END ISSUE---

Your first objective is to identify the file(s) that you will need to edit in order to address the issue.

You have access to a code search tool that you can use to search through the codebase. Submit 3-5 search queries that you want to use to find the right file(s).
"""

PROMPT_GENERATE_PATCH = """You are a senior software engineer addressing the following issue:

---BEGIN ISSUE---
{problem_statement}
---END ISSUE---

We've identified that the fix will likely require edits to one or more of the following file(s) (note that line numbers have been added for reference):

---BEGIN CODE FILES---
{file_candidates}
---END CODE FILES---

Instructions:
1. Carefully read the problem statement and the provided code files.
2. Provide the required arguments to edit the file:
    - filename: The path to the file that should be edited.
    - start_line: The start line of the edit block.
    - end_line: The end line of the edit block.
    - new_contents: The contents that will replace this code block. USE PROPER INDENTATION!"""