"""
LangGraph Agent for code review
"""

from swebench_utils import build_swebench_images
from datasets import load_dataset

from environment import Environment
from editor import Editor
from code_index import CodeIndex
from swebench.harness.test_spec.test_spec import make_test_spec
from swebench.harness.constants.constants import SWEbenchInstance
from utils import get_logger

from langchain_aws import ChatBedrockConverse
from langchain_openai import ChatOpenAI
from langgraph.prebuilt.tool_node import ToolNode
from langgraph.graph import StateGraph, MessagesState, START, END
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

import prompt_templates as pt
from utils import save_result, get_media_type_for_extension
import config as cf

from dotenv import load_dotenv
from botocore.config import Config
from typing import Annotated, List, Dict, Optional, Literal
from pydantic import BaseModel
import operator
import logging
import time
import httpx
import base64

import argparse
import os


load_dotenv("../.env")


def parse_args():
    # todo: more configs here that default to configuration.py values
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-id", type=str, default="princeton-nlp/SWE-bench_Lite")
    parser.add_argument("--split", type=str, default="test")
    # accept multiple instance_ids
    parser.add_argument("--instance-ids", nargs="+", type=str, required=True)
    parser.add_argument("--run-id", type=str, required=True)
    parser.add_argument("--max-workers", type=int, default=os.cpu_count())
    parser.add_argument("--start-from-idx", type=int)
    parser.add_argument("--model", type=str, default="anthropic")
    parser.add_argument("--recursion-limit", type=int, default=cf.RECURSION_LIMIT)
    return parser.parse_args()


def build_images(
    dataset_id: str, 
    split: str, 
    instance_ids: List[str], 
    tag: str = "latest", 
    start_from_idx: Optional[int] = None,
) -> List[Dict]:

    dataset = load_dataset(dataset_id, split=split)
    if "all" in instance_ids:
        instance_ids = [r["instance_id"] for r in dataset]
    # option to skip ahead
    instance_ids = instance_ids[start_from_idx:]# if start_from_idx else instance_ids
    # base -> environment -> instance images
    build_swebench_images(
        dataset_id, split,
        instance_ids=instance_ids,
        tag=tag,
    )

    return [r for r in dataset if r["instance_id"] in instance_ids]


# create environment
def run_instance(
    instance_details: SWEbenchInstance, 
    run_id: str, 
    logger: logging.Logger,
    recursion_limit: int,
):

    test_spec = make_test_spec(instance_details)

    # context manager for graceful deletion
    with Environment.from_test_spec(
        test_spec=test_spec,
        run_id=run_id,
        logger=logger,
    ) as env:

        code_index = CodeIndex(instance_details)
        code_editor = Editor(env, instance=instance_details, code_index=code_index)

        if args.model == "anthropic":
            llm = ChatBedrockConverse(
                model=cf.BEDROCK_MODEL_ID,
                config=Config(
                    retries={
                        "mode": "adaptive",
                        "max_attempts": cf.BOTO3_MAX_ATTEMPTS,
                    },
                ),
                region_name=os.getenv("AWS_REGION", "us-east-1"),
                temperature=cf.MODEL_TEMPERATURE,
            )
            system_prompt = pt.AGENT_INSTRUCTIONS_USE_EXISTING_TESTS
        elif args.model == "openai":
            llm = ChatOpenAI(
                model=cf.OPENAI_MODEL_ID,
                reasoning_effort="high",
            )
            system_prompt = pt.AGENT_INSTRUCTIONS_REASONING_MODEL
        else:
            raise ValueError(f"Unknown model {args.model}")

        llm_light = ChatBedrockConverse(
            model=cf.BEDROCK_MODEL_ID_LIGHT,
            config=Config(
                retries={
                    "mode": "adaptive",
                    "max_attempts": cf.BOTO3_MAX_ATTEMPTS,
                },
            ),
            region_name=os.getenv("AWS_REGION", "us-east-1"),
            temperature=cf.MODEL_TEMPERATURE,
        )

        class CodeBlockIds(BaseModel):
            """code_block_id's relevant to the search query"""
            code_block_ids: List[str]

        def semantic_search(query: str, category: Literal["src", "tests"] = "src", type: Optional[Literal["function", "class"]] = None) -> str:
            """Search the codebase for relevant code blocks pertaining to the provided query. 
            Use the `category` argument to differentiate between source / implementation code (`src`) and test code (`tests`). `src` is the default.
            Use the optional `type` argument (must be either `function` or `class`) to filter to only code blocks of that type. 
            If `type` is unspecified, all relevant blocks will be returned."""
            llm_light_with_structure = llm_light.with_structured_output(CodeBlockIds)

            code_blocks = code_editor.code_search_formatted_docs(query, category, type)

            prompt = pt.CODE_SEARCH.format(query=query, code_blocks=code_blocks)

            code_block_ids = llm_light_with_structure.invoke(prompt).code_block_ids

            return code_editor.get_docs_by_name(code_block_ids)

        def submit(reason: str):
            """Submit your changes once complete. Provide a reason for submitting"""
            logger.info(f"SUBMITTED WITH REASON:\n\n{reason}")

        tools = [
            semantic_search,
            code_editor.explicit_search,
            code_editor.open_file,
            code_editor.scroll_up,
            code_editor.scroll_down,
            # code_editor.ls,
            code_editor.search_files,
            # code_editor.create,
            code_editor.str_replace,
            code_editor.insert,
            # code_editor.undo_edit,
            # code_editor.execute_command,
            submit,
        ]

        llm_with_tools = llm.bind_tools(tools).with_retry(stop_after_attempt=cf.LANGCHAIN_STOP_AFTER_ATTEMPT)

        class CodeReviewerState(MessagesState):
            patch: str
            trajectory: Annotated[List[str], operator.add]
            edited_files: List[str]
            removed_files: List[str]

        def assistant(state: CodeReviewerState):
            
            prompt_template = ChatPromptTemplate([
                ("system", system_prompt),
                MessagesPlaceholder("messages")
            ])

            chain = prompt_template | llm_with_tools
            # todo: collapse messages efficiently - e.g. remove failed tool calls
            next_message = chain.invoke({"messages": state["messages"]})

            # track edited files if `edit_file` tool was called
            edited_files, removed_files = state.get("edited_files", []), state.get("removed_files", [])
            for message in state["messages"]:
                if isinstance(message, AIMessage):
                    for tc in message.tool_calls:
                        if tc["name"] in ["edit_file", "str_replace", "insert", "create"]:
                            # stage changed files
                            if (file_path := tc["args"]["file_path"]) not in edited_files:
                                edited_files.append(file_path)
                        elif tc["name"] == "rm":
                            # removed files
                            if (file_path := tc["args"]["file_path"]) not in removed_files:
                                removed_files.append(file_path)

            return {
                "messages": [next_message],
                "trajectory": [next_message.pretty_repr()],
                "edited_files": edited_files,
                "removed_files": removed_files,
            }

        def get_patch(state: CodeReviewerState):
            # unique set of files that were edited and not removed by the agent
            edited_files = list(set(filter(lambda x: x not in state.get("removed_files", []) + ["reproduce_issue.py"], state["edited_files"])))
            
            logger.info(f"Getting patch for files: {edited_files}")
            patch = env.get_patch(edited_files)
            return {"patch": patch}

        def route_messages(state: CodeReviewerState):
            last_message = state["messages"][-1]
            if last_message and "submit" in [t.get("name") for t in last_message.tool_calls]:
                return "get_patch"  # Route to end
            elif last_message and len(last_message.tool_calls) > 0:
                return "tool_node"  # Route back to tool node
            else:
                return "assistant"

        workflow = StateGraph(CodeReviewerState)

        workflow.add_node("assistant", assistant)
        workflow.add_node("tool_node", ToolNode(tools))
        workflow.add_node("get_patch", get_patch)

        # Define edges: these determine how the control flow moves
        workflow.add_edge(START, "assistant")
        workflow.add_conditional_edges(
            "assistant",
            # If the latest message (result) from assistant is a tool call -> tools_condition routes to tools
            # If the latest message (result) from assistant is a not a tool call -> tools_condition routes to END
            route_messages,
        )
        workflow.add_edge("tool_node", "assistant")
        workflow.add_edge("get_patch", END)
        graph = workflow.compile()

        message_content = [
            {
                "type": "text", 
                "text": pt.START_AGENT.format(
                    repo=instance_details["repo"],
                    problem_statement=instance_details["problem_statement"],
                )
            },
        ]
        
        # multimodal inputs
        if "image_assets" in instance_details:
            for img_url in instance_details["image_assets"]["problem_statement"]:
                # if (extension := img_url.split(".")[-1]) in ["png", "jpg", "gif", "webp"]:
                message_content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": get_media_type_for_extension(img_url.split(".")),
                            "data": base64.b64encode(httpx.get(img_url).content).decode("utf-8"),
                        },
                    }
                )

        # input message
        messages = [
            HumanMessage(content=message_content)
        ]

        model_patch, trajectory, error = None, None, None
        try:
            for chunk in graph.stream({"messages": messages}, {"recursion_limit": recursion_limit}, stream_mode="updates"):
                if "tool_node" in chunk:
                    logger.info(chunk["tool_node"]["messages"][-1].pretty_repr())
                elif "assistant" in chunk:
                    logger.info(chunk["assistant"]["messages"][-1].pretty_repr())
                    trajectory = chunk["assistant"]["trajectory"]
                elif "get_patch" in chunk:
                    model_patch = chunk["get_patch"]["patch"]

            if model_patch:
                logger.info(f"****GOT MODEL PATCH FOR {instance_details['instance_id']}****")
                logger.info(model_patch)
        except Exception as e:
            error = str(e)
            logger.error(e)
            # TODO: update this for JS / multimodal - default path
            model_patch = env.get_patch(["*.py"])

    return model_patch, trajectory, error


def process_instance(
    instance: SWEbenchInstance, 
    run_id: str, 
    dataset_id: str, 
    model_name: str = "ai-review-agent", 
    **kwargs,
):

    logger = get_logger(
        __name__, 
        filename=os.path.join("logs", "inference", run_id, f"{instance['instance_id']}.log")
    )

    t_start = time.time()
    model_patch, trajectory, error = run_instance(instance, run_id, logger=logger, **kwargs)

    save_result(
        dataset_id,
        run_id,
        model_name_or_path=model_name,
        instance_id=instance["instance_id"],
        model_patch=model_patch,
        trajectory=trajectory,
        error=error,
        time_sec=round(time.time() - t_start, 1)
    )


if __name__ == "__main__":
    args = parse_args()

    instances = build_images(
        dataset_id=args.dataset_id, 
        split=args.split, 
        instance_ids=args.instance_ids, 
        start_from_idx=args.start_from_idx,
    )

    for instance in instances:
        process_instance(
            instance,
            run_id=args.run_id,
            dataset_id=args.dataset_id,
            recursion_limit=args.recursion_limit,
        )
