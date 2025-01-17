from langchain_aws import ChatBedrockConverse, BedrockEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_community.docstore import InMemoryDocstore
import faiss
from datasets import load_dataset
from botocore.config import Config
from typing import TypedDict, List
import os
import utils
import fault_localization as fl
from repair import FileEdits, generate_git_patch
import config as cf
from prompts import *
from datetime import datetime
import argparse
import logging
import time



bedrock_config = Config(
    retries={
        "max_attempts": 100
    }
)

embeddings = BedrockEmbeddings(
    model_id="amazon.titan-embed-text-v2:0",
    model_kwargs={"dimensions": 256},
    config=bedrock_config,
)

llm_light = ChatBedrockConverse(
    model="us.anthropic.claude-3-5-haiku-20241022-v1:0",
    config=bedrock_config,
)

llm_heavy = ChatBedrockConverse(
    model="us.anthropic.claude-3-5-sonnet-20241022-v2:0",
    config=bedrock_config,
)

# make sure models are configured properly
assert len(embeddings.embed_query("foo")) > 0, "Error generating embeddings."

llm_search_queries = llm_light.with_structured_output(fl.CodeSearch)
llm_patch = llm_heavy.with_structured_output(FileEdits)


class Prediction(TypedDict):
    model_name_of_path: str
    instance_id: str
    model_patch: str


def run(args):
    """Generate predictions for the full dataset."""
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    dataset = load_dataset(args.dataset)
    vectorstore_base_path = ".vectorstores/"
    results_path = f"results/{args.dataset.replace('/', '-')}_{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}.jsonl"

    all_preds: List[Prediction] = []
    rows = dataset[args.split].skip(args.start_idx) if args.start_idx else dataset[args.split]

    for row in rows:
        t_start = time.time()
        logger.info(row["instance_id"])

        index_name = row["repo"].split("/")[-1]
        vectorstore_path = os.path.join(vectorstore_base_path, index_name)
        if os.path.exists(vectorstore_path):
            logger.info(f"Found existing vectorstore for repo {row['repo']}")
            vectorstore = FAISS.load_local(
                vectorstore_path,
                embeddings=embeddings,
                index_name=index_name,
                allow_dangerous_deserialization=True,
            )
        else:
            logger.info(f"Creating new vectorstore for repo {row['repo']}")
            index = faiss.IndexFlatL2(len(utils.embed_cohere(["foo"], embed_type="search_query")[0]))
            vectorstore = FAISS(
                embedding_function=embeddings,
                index=index,
                index_to_docstore_id={},
                docstore=InMemoryDocstore(),
            )

        repo_path = f".playground/{row['repo'].split('/')[-1]}"

        logger.info("Cloning repo and retrieving files.")
        code_docs = fl.clone_and_get_contents(
            clone_url=f"https://github.com/{row['repo']}",
            repo_path=repo_path,
            instance_id=row["instance_id"],
            checkout=row["base_commit"],
            target_folder=cf.repo_to_top_folder[row["repo"]],
            file_extensions=cf.FILE_EXTENSIONS,
        )
        filenames = [doc.metadata["file_path"] for doc in code_docs]

        logger.info("Splitting code files into chunks.")
        split_docs = fl.split_code_docs(code_docs)

        logger.info("Adding docs to vectorstore.")
        utils.add_docs_to_store(vectorstore, embeddings, documents=split_docs, max_workers=100)

        logger.info("Generating search queries")
        search_queries = llm_search_queries.invoke(
            PROMPT_FAULT_LOCALIZATION_SEARCH.format(
                problem_statement=row["problem_statement"]
            )
        )
        logger.info(f"Search queries: {search_queries['queries']}")

        logger.info("Searching vectorstore for relevant files")
        unique_files, _ = fl.search_code(
            search_queries,
            vectorstore,
            filenames=filenames,
            instance_id=row["instance_id"],
        )

        # need a more precise localization, e.g. to particular functions / classes
        file_candidates = "\n\n".join([utils.get_code_file(repo_path, fp) for fp in unique_files[:cf.MAX_CANDIDATES]])

        try:
            logger.debug("LLM generating patch")
            file_edits: FileEdits = llm_patch.invoke(
                PROMPT_GENERATE_PATCH.format(
                    problem_statement=row["problem_statement"],
                    file_candidates=file_candidates
                ),
            )
            logger.info(f"File edits: {file_edits}")

            model_patch = generate_git_patch(
                repo_path=repo_path,
                **file_edits,
            )
            logger.info(f"Patch generated: {model_patch}")

            logger.debug("Patch generated. Caching vectorstore.")
            all_preds.append({
                "model_name_or_path": cf.MODEL_NAME,
                "instance_id": row["instance_id"],
                "model_patch": model_patch,
                "unique_files":  unique_files,
                "file_to_edit": file_edits["filename"],
            })
        except Exception as e:
            # have seen errors due to excessive context length - will need a resolution in the future
            logger.error(f"Error generating patch for {row['instance_id']}: {e}")
            all_preds.append({
                "model_name_or_path": cf.MODEL_NAME,
                "instance_id": row["instance_id"],
                "model_patch": "",
                "unique_files": [],
                "file_to_edit": "",
            })

        # save vectorstore to cache most of the embeddings for unchanged files in the same repo
        vectorstore.save_local(vectorstore_path, index_name=index_name)

        if not os.path.exists("results"):
            os.makedirs("results")

        utils.write_jsonl(results_path, all_preds)
        logger.info(f"Time: {time.time() - t_start:.2f}s")



def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=str, default="princeton-nlp/SWE-bench_Lite")
    parser.add_argument("--split", type=str, default="test")
    parser.add_argument("--start-idx", type=int, required=False)
    parser.add_argument("--debug", action="store_true", default=True)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(args)
