import git
import os

from tree_sitter import Language, Parser
import tree_sitter_python as tspython

from langchain_core.documents import Document
from langchain_chroma import Chroma

import json
from pydantic import BaseModel
from typing import Optional, List, Tuple, Dict, Literal
import hashlib
import config as cf

import boto3
import json
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm


BASE_REPO_PATH = "../.repos/"
BASE_DB_PATH = "../.vectors/"


def clone_and_checkout(instance_details: dict) -> str:
    local_path = os.path.join(BASE_REPO_PATH, instance_details["repo"].split("/")[-1]) + "/"
    repo_url = f"https://github.com/{instance_details['repo']}.git"
    commit_sha = instance_details["base_commit"]
    # Clone the repository if it doesn't exist
    if not os.path.exists(local_path):
        print(f"Cloning repository from {repo_url}...")
        repo = git.Repo.clone_from(repo_url, local_path)
        print("Repository cloned successfully!")
    else:
        print(f"Repository already exists at {local_path}")
        repo = git.Repo(local_path)

    # Fetch all remote branches
    print("Fetching all remote branches...")
    repo.remotes.origin.fetch()

    # Checkout the specific commit
    repo.git.checkout(commit_sha)
    print(f"Successfully checked out commit {commit_sha}")
    return local_path


class CodeBlock(BaseModel):
    name: str  # Full path (e.g., "module.class.function")
    type: str  # "class" or "function"
    code: str  # Complete code block
    docstring: Optional[str]
    file_path: str
    start_line: int
    end_line: int
    parent: Optional[str]  # Parent class/module name
    category: str  # tests / src
    id: str  # hash of the code

def setup_parser():
    """Initialize the tree-sitter parser for Python."""
    PY_LANGUAGE = Language(tspython.language())
    parser = Parser(PY_LANGUAGE)
    return parser

def extract_docstring(node, source_code):
    """Extract docstring from a class or function node."""
    for child in node.children:
        if child.type == 'block':
            for block_child in child.children:
                if block_child.type == 'expression_statement':
                    for expr_child in block_child.children:
                        if expr_child.type == 'string':
                            return source_code[expr_child.start_byte:expr_child.end_byte].strip('\"\'')
    return None

def get_node_source(node, source_code):
    """Get the source code for a node."""
    return source_code[node.start_byte:node.end_byte]

def process_file(file_path: str, parser: Parser, directory_path: str) -> List[CodeBlock]:
    """Process a single Python file and extract all code blocks."""
    with open(file_path, 'r', encoding='utf-8') as f:
        source_code = f.read()

    tree = parser.parse(bytes(source_code, "utf8"))
    blocks = []
    
    def process_node(node, parent_name=None):

        code = get_node_source(node, source_code)
        id = hashlib.md5(code.encode('utf-8')).hexdigest()
        category = "tests" if "test" in file_path else "src"
        rel_path = file_path.replace(directory_path, "")

        if node.type == 'class_definition':
            name_node = next(child for child in node.children if child.type == 'identifier')
            class_name = source_code[name_node.start_byte:name_node.end_byte]
            
            full_name = f"{parent_name}.{class_name}" if parent_name else class_name
            
            blocks.append(CodeBlock(
                name=full_name,
                type="class",
                code=code,
                docstring=extract_docstring(node, source_code),
                file_path=rel_path,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                parent=parent_name,
                category=category,
                id=id,
            ))
            
            # Process methods within the class
            for child in node.children:
                if child.type == 'block':
                    for block_child in child.children:
                        process_node(block_child, full_name)
                        
        elif node.type == 'function_definition':
            name_node = next(child for child in node.children if child.type == 'identifier')
            func_name = source_code[name_node.start_byte:name_node.end_byte]
            
            full_name = f"{parent_name}.{func_name}" if parent_name else func_name
            
            blocks.append(CodeBlock(
                name=full_name,
                type="function",
                code=code,
                docstring=extract_docstring(node, source_code),
                file_path=rel_path,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                parent=parent_name,
                category=category,
                id=id,
            ))

    # Start processing from the root
    for node in tree.root_node.children:
        process_node(node)
        
    return blocks

def analyze_directory(directory_path: str) -> List[CodeBlock]:
    """
    Analyze all Python files in a directory and extract code blocks.
    
    Args:
        directory_path: Path to the directory to analyze
        
    Returns:
        List of CodeBlock objects containing the extracted information
    """
    parser = setup_parser()
    all_blocks = []
    
    for root, _, files in os.walk(directory_path):
        for file in files:
            if file.endswith('.py'):
                file_path = os.path.join(root, file)
                try:
                    blocks = process_file(file_path, parser, directory_path)
                    all_blocks.extend(blocks)
                except Exception as e:
                    print(f"Error processing {file_path}: {str(e)}")
                    
    return all_blocks


def prepare_code_blocks(code_blocks: List[CodeBlock]) -> Tuple[List[str], List[str], List[Dict]]:
    # texts, ids, metadatas
    texts = ["\n".join([c.name, c.type, c.code]) for c in code_blocks]
    ids = [hashlib.md5(json.dumps(c.model_dump()).encode("utf-8")).hexdigest() for c in code_blocks]
    metadatas = [{
        "file_path": c.file_path,
        "start_line": c.start_line,
        "end_line": c.end_line,
        "category": c.category,
        "type": c.type,
        "name": c.name,
    } for c in code_blocks]
    return texts, ids, metadatas


def embed_cohere(texts: List[str], model_id: str = "cohere.embed-english-v3", input_type: str = "search_query") -> List[List[float]]:
    runtime_client = boto3.client('bedrock-runtime', region_name='us-east-1')
        
    # `truncate` parameter does not seem to do anything
    payload = {
        "texts": [text[:cf.MAX_CHARACTERS_PER_DOC] for text in texts],
        "input_type": input_type,
        "truncate": "END"
    }
    
    response = runtime_client.invoke_model(
        body=json.dumps(payload),
        modelId=model_id,
    )
    
    return json.loads(response['body'].read().decode())["embeddings"]


def process_batch(batch_with_index, model_id: str) -> tuple[int, List[List[float]]]:
    """
    Process a single batch of texts and return embeddings with the batch index.
    
    Args:
        batch_with_index (tuple): Tuple of (batch_index, texts)
        endpoint_name (str): Name of the SageMaker endpoint
    
    Returns:
        tuple: (batch_index, embeddings)
    """
    batch_index, batch_texts = batch_with_index
    try:
        batch_embeddings = embed_cohere(batch_texts, model_id, "search_document") 
        return batch_index, batch_embeddings
        
    except Exception as e:
        print(f"Error processing batch {batch_index}: {str(e)}")
        return batch_index, None

def get_embeddings_parallel(
    texts: List[str],
    model_id: str = "cohere.embed-english-v3",
    batch_size: int = 96,
    max_workers: int = 16,
    show_progress: bool = True
) -> Optional[List[List[float]]]:
    """
    Get embeddings for a list of texts using parallel processing.
    
    Args:
        texts (List[str]): List of strings to get embeddings for
        endpoint_name (str): Name of the SageMaker endpoint
        batch_size (int): Number of texts to process in each batch
        max_workers (int): Maximum number of parallel threads
        show_progress (bool): Whether to show progress bar
    
    Returns:
        Optional[List[List[float]]]: List of embeddings in the same order as input texts
    """
    # Create batches with their indices
    batches = [
        (batch_idx, texts[i:i + batch_size]) 
        for batch_idx, i in enumerate(range(0, len(texts), batch_size))
    ]
    
    # Initialize results storage
    results = {}
    all_embeddings = []
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all batches to the executor
        future_to_batch = {
            executor.submit(process_batch, batch, model_id): batch[0] 
            for batch in batches
        }
        
        # Process completed futures with optional progress bar
        futures_iterator = as_completed(future_to_batch)
        if show_progress:
            futures_iterator = tqdm(
                futures_iterator, 
                total=len(batches),
                desc="Processing batches"
            )
        
        # Collect results
        for future in futures_iterator:
            batch_index, batch_embeddings = future.result()
            if batch_embeddings is None:
                return None
            results[batch_index] = batch_embeddings

    # Combine results in correct order
    for i in range(0, len(batches)):
        all_embeddings.extend(results[i])
    
    return all_embeddings


class CodeIndex:
    def __init__(self, instance_details: dict):
        
        self.instance_details = instance_details
        # self.editor = editor
        self.db_path = os.path.join(BASE_DB_PATH, instance_details["instance_id"])

        # main db for code search
        self.db = Chroma(
            collection_name=instance_details["instance_id"],
            persist_directory=self.db_path,
        )

        # db of just test file names for test identification
        self.db_test_files = Chroma(
            collection_name=f"{instance_details['instance_id']}_test_files",
            persist_directory=f"{self.db_path}_test_files",
        )

        if self.db._collection.count() == 0:
            # populate the vectorstore
            print("Adding Code Blocks to vectorstore")
            repo_path = clone_and_checkout(instance_details)
            code_blocks = analyze_directory(repo_path)
            texts, ids, metadatas = prepare_code_blocks(code_blocks)
            embeddings = get_embeddings_parallel(texts)

            self.db._collection.add(
                ids=ids,
                embeddings=embeddings,
                metadatas=metadatas,
            )

            # add the test files
            print("Adding test files to vectorstore")
            test_file_texts = list(set([c.file_path for c in code_blocks if c.category == "tests"]))
            embeddings_test_files = get_embeddings_parallel(test_file_texts)
            test_file_ids = [hashlib.md5(t.encode("utf-8")).hexdigest() for t in test_file_texts]

            self.db_test_files._collection.add(
                ids=test_file_ids,
                embeddings=embeddings_test_files,
                documents=test_file_texts,
            )
    
    def get_most_similar_test_file(self, src_file_path: str) -> str:
        query_vec = embed_cohere([src_file_path])
        return self.db_test_files._collection.query(query_vec, n_results=1)["documents"][0][0]

    def code_search(self, query: str, category: Literal["tests", "src"], type: Optional[Literal["function", "class"]] = None, n_results: int = cf.RETRIEVE_K_DOCS) -> List[Document]:
        query_vec = embed_cohere([query])
        where = {"$and": [{"category": category}, {"type": type}]} if type else {"category": category}
        return self.db._collection.query(query_vec, n_results=n_results, where=where)["metadatas"][0]
    
    def code_search_formatted_docs(self, query: str, category: Literal["tests", "src"], n_results: int = cf.RETRIEVE_K_DOCS) -> str:
        docs = self.code_search(query, category, n_results)
        return self._format_docs(docs)
    
    def get_docs_by_name(self, doc_ids: List[str]) -> List[Dict]:
        if len(doc_ids) == 1:
            where = {"name": doc_ids[0]}
        else:
            where = {"$or": [{"name": name} for name in doc_ids]}
        return self.db._collection.get(
            where=where
        )["metadatas"]
