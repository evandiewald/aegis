from git import Repo
from git.exc import GitCommandError
from pathlib import Path
from typing import List, Optional, Annotated, TypedDict

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

import os
import config as cf
import utils


def clone_and_get_contents(
    clone_url: str,
    repo_path: str,
    instance_id: str,
    target_folder: Optional[str] = None,
    checkout: Optional[str] = None,
    file_extensions: Optional[List[str]] = None,
) -> List[Document]:
    """
    Clone a repository, checkout a specific commit, and return contents of files from a target folder.

    Args:
        repo_url: URL of the git repository
        commit_hash: Commit hash to checkout
        target_folder: Folder within the repository to traverse

    Returns:
        Dictionary mapping file paths to their contents
    """
    target_folder = target_folder or ""
    documents = []

    try:
        if os.path.isdir(os.path.join(repo_path, ".git")):
            repo = Repo(repo_path)
            # If the existing repository is not the same as the one we're trying to
            # clone, raise an error.
            if repo.remotes.origin.url != clone_url:
                raise ValueError(
                    "A different repository is already cloned at this path."
                )
        else:
            repo = Repo.clone_from(clone_url, repo_path)
        if checkout:
            repo.git.checkout(checkout)
        target_path = os.path.join(repo_path, target_folder)

        # ensure that the target folder exists
        if not os.path.exists(target_path):
            raise Exception(f"Target folder '{target_folder}' not found in repository")

        # Collect all file contents
        for root, _, files in os.walk(target_path):
            for file in files:
                # Get path relative to target folder
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, target_path)
                extension = Path(rel_path).suffix
                if file_extensions:
                    if extension not in file_extensions:
                        continue
                try:
                    # only get the text files
                    with open(full_path, "r", encoding="utf-8") as f:
                        page_content = f.read()
                        documents.append(
                            Document(
                                page_content=page_content,
                                metadata={
                                    "file_path": os.path.join(target_folder, rel_path),
                                    "file_type": extension,
                                    "instance_ids": [instance_id],
                                }
                            )
                        )
                except Exception as e:
                    # non-text files are skipped
                    continue

        return documents

    except GitCommandError as e:
        print(f"Git error occurred: {e}")
        return []
    except Exception as e:
        print(f"Error occurred: {e}")
        return []


def split_code_docs(docs: List[Document]) -> List[Document]:

    # split docs by language
    docs_by_language = {}
    for doc in docs:
        doc_language = cf.extension_to_language[doc.metadata["file_type"]]
        if doc_language not in docs_by_language:
            docs_by_language[doc_language] = [doc]
        else:
            docs_by_language[doc_language].append(doc)

    # splitter
    split_docs = []
    for language in docs_by_language:
        # todo: make chunk size config - 2048 is the max for cohere
        language_splitter = RecursiveCharacterTextSplitter.from_language(
            language=language,
            chunk_size=2048,
            chunk_overlap=128,
        )
        new_docs = language_splitter.split_documents(docs_by_language[language])
        for doc in new_docs:
            # use the file hash as the id
            doc.id = utils.hash_text(doc.page_content)
        split_docs += new_docs

    return split_docs


class CodeSearch(TypedDict):
    """The search queries to submit to the code search engine"""
    queries: Annotated[List[str], "The search queries (can be natural language or exact search)"]


def search_code(code_search: CodeSearch, vectorstore: FAISS, filenames: List[str], instance_id: str, k: int = 5):
    relevant_docs = []

    query_embeddings = utils.embed_cohere(code_search["queries"], "search_query")
    for embedding in query_embeddings:

        relevant_docs += vectorstore.similarity_search_with_score_by_vector(
            embedding=embedding,
            filter=lambda doc: instance_id in doc["instance_ids"],
            fetch_k=k*2,
            k=k,
        )
    sorted_docs = [doc for doc in sorted(relevant_docs, key=lambda x: x[1], reverse=True)]
    unique_files, file_scores = [], []

    # if there is a match to one of the repo files, make sure it is there directly
    for query in code_search["queries"]:
        # doesn't have to have the full, path, just the file name
        query = query.split("/")[-1]
        if any([file.endswith(query) for file in filenames]):
            unique_files.append(query)
            file_scores.append(1.0)

    for doc, score in sorted_docs:
        file_path = doc.metadata["file_path"]
        if file_path not in unique_files:
            unique_files.append(file_path)
            file_scores.append(score)

    return unique_files, file_scores
