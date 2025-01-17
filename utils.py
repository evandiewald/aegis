import hashlib
from langchain_core.embeddings import Embeddings
from langchain_core.documents import Document
from langchain_aws import BedrockEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_community.docstore import InMemoryDocstore
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Literal, Any, Set
import ast
import numpy as np
import os
import json
import boto3


def hash_text(text: str):
    return hashlib.md5(text.encode()).hexdigest()


def _embed_cohere(bedrock: Any, texts: List[str], embed_type: Literal["search_document", "search_query"]) -> List[List[float]]:
    request_body = {
        "modelId": "cohere.embed-multilingual-v3",
        "contentType": "application/json",
        "accept": "application/json",
        "body": json.dumps({"texts": texts, "input_type": embed_type}),
    }

    return json.loads(bedrock.invoke_model(**request_body)["body"].read())["embeddings"]


def embed_cohere(texts: List[str], embed_type: Literal["search_document", "search_query"]) -> List[List[float]]:

    bedrock = boto3.client("bedrock-runtime")

    # parallelize if multiple API calls are needed
    if len(texts) > 96:
        # cohere can take 96 texts per request
        chunks = [texts[i:i + 96] for i in range(0, len(texts), 96)]
        # Dictionary to keep track of the original order
        results_dict = {}

        with ThreadPoolExecutor(max_workers=100) as executor:
            # Submit all tasks and store futures with their indices
            future_to_idx = {
                executor.submit(_embed_cohere, bedrock, chunk, embed_type): idx
                for idx, chunk in enumerate(chunks)
            }

            # Process completed futures and store results
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    result = future.result()
                    results_dict[idx] = result
                except Exception as e:
                    print(f"Error processing text at index {idx}: {str(e)}")
                    results_dict[idx] = None

        # Return results in original order
        vectors = []
        for i in range(len(chunks)):
            vectors.extend(results_dict[i])
        return vectors

    else:
        return _embed_cohere(bedrock, texts, embed_type)


def parallel_embed(texts: List[str], embeddings: Embeddings, max_workers: int = 100) -> List[Any]:
    """
    Parallelize the embedding of texts using a configurable number of workers.

    Args:
        texts (List[str]): List of texts to embed
        max_workers (int): Maximum number of parallel workers (default: 4)

    Returns:
        List[Any]: List of embeddings in the same order as input texts
    """
    # Dictionary to keep track of the original order
    results_dict = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks and store futures with their indices
        future_to_idx = {
            executor.submit(embeddings._embedding_func, text): idx
            for idx, text in enumerate(texts)
        }

        # Process completed futures and store results
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                result = future.result()
                results_dict[idx] = result
            except Exception as e:
                print(f"Error processing text at index {idx}: {str(e)}")
                results_dict[idx] = None

    # Return results in original order
    return [results_dict[i] for i in range(len(texts))]


def add_docs_to_store(
    vectorstore: FAISS,
    embeddings: BedrockEmbeddings,
    documents: List[Document],
    max_workers: int = 100,
):

    if not isinstance(vectorstore.docstore, InMemoryDocstore):
        raise ValueError("Vectorstore docstore must be an instance of InMemoryDocstore")
    print(f"Adding {len(documents)} documents to vectorstore")
    # find the existing docs, if any
    existing_docs = [doc for doc in vectorstore.get_by_ids([d.id for d in documents])]
    print(f"found {len(existing_docs)} existing docs")

    # only need to embed the new/updated files
    existing_doc_ids = [doc.id for doc in existing_docs]
    new_docs = [doc for doc in documents if doc.id not in existing_doc_ids]
    print(f"found {len(new_docs)} new docs")

    assert len(documents) - len(existing_docs) == len(new_docs), "Num. new docs + num updated docs != num docs"

    # first, update the existing docs if necessary
    for doc in existing_docs:
        existing_instance_ids = vectorstore.docstore._dict[doc.id].metadata["instance_ids"]
        vectorstore.docstore._dict[doc.id].metadata["instance_ids"] = list(set(existing_instance_ids + doc.metadata["instance_ids"]))

    if new_docs:
        # now, add the new docs
        # vectors = parallel_embed([doc.page_content for doc in new_docs], embeddings, max_workers=max_workers)
        vectors = embed_cohere([doc.page_content for doc in new_docs], "search_document")
        # add embeddings to FAISS
        vectorstore.index.add(np.array(vectors, dtype=np.float32))
        # add metadata / docs to docstore
        vectorstore.docstore.add({doc.id: doc for doc in new_docs})
        # link metadata with FAISS via index_to_docstore_id
        starting_len = len(vectorstore.index_to_docstore_id)
        index_to_id = {starting_len + j: id_ for j, id_ in enumerate([doc.id for doc in new_docs])}
        vectorstore.index_to_docstore_id.update(index_to_id)


def get_code_file(repo_path: str, file_path: str, line_numbers: bool = True) -> str:
    contents = f"File: {file_path}\n\n"
    with open(os.path.join(repo_path, file_path), "r") as f:
        code_lines = f.read()
    for idx, line in enumerate(code_lines.splitlines()):
        prefix = f"L{idx+1}: " if line_numbers else ""
        contents += f"{prefix}{line}\n"
    return contents


def write_jsonl(path: str, data: List[dict]):
    with open(path, "w") as f:
        for line in data:
            f.write(f"{json.dumps(line)}\n")


def parse_python_file(file_contents: str) -> Set[str]:
    """
    Parse a Python file and return a set of all class definitions, methods, and functions.

    Args:
        file_path (str): Path to the Python file to parse

    Returns:
        Set[str]: Set of strings containing class names, method names (with class prefix),
                 and function names
    """
    try:
        # Parse the content into an AST
        tree = ast.parse(file_contents)

        # Initialize the result set
        definitions = set()

        # Helper function to visit all nodes
        def visit_node(node, class_name=None):
            # Handle class definitions
            if isinstance(node, ast.ClassDef):
                definitions.add(node.name)
                # Visit all nodes inside the class
                for child in ast.iter_child_nodes(node):
                    visit_node(child, node.name)

            # Handle function definitions
            elif isinstance(node, ast.FunctionDef):
                if class_name:
                    # If inside a class, add as method with class prefix
                    definitions.add(f"{class_name}.{node.name}")
                else:
                    # If not in a class, add as regular function
                    definitions.add(node.name)

            # Visit all child nodes if not in a class
            elif not class_name:
                for child in ast.iter_child_nodes(node):
                    visit_node(child)

        # Start visiting from the root
        visit_node(tree)

        return definitions

    except Exception as e:
        print(f"Error parsing file contents: {str(e)}")
        return set()


def get_directory_structure(repo_path: str) -> List[str]:
    """
    Get the directory structure of a repository.

    Args:
        repo_path (str): Path to the repository

    Returns:
        List[str]: List of file paths relative to the repository root
    """
    file_paths = []
    for root, _, files in os.walk(repo_path):
        for file in files:
            # Skip hidden files
            if file.startswith("."):
                continue
            # Get the relative path
            relative_path = os.path.relpath(os.path.join(root, file), repo_path)
            file_paths.append(relative_path)
    return file_paths
