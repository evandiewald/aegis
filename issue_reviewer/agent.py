"""
LangGraph Agent for code review
"""

from swebench_utils import build_swebench_images
from datasets import load_dataset

from environment import Environment
from editor import Editor
from swebench.harness.test_spec.test_spec import make_test_spec
from swebench.harness.constants.constants import SWEbenchInstance
from utils import get_logger

from langchain_aws import ChatBedrockConverse
from langgraph.prebuilt.tool_node import ToolNode
from langgraph.graph import StateGraph, MessagesState, START, END
from langchain_core.messages import HumanMessage

import prompt_templates as pt
from utils import save_result

from dotenv import load_dotenv
from botocore.config import Config
from typing import Annotated, List, Dict, Optional
from concurrent.futures import ProcessPoolExecutor
from functools import partial
import operator
from tqdm import tqdm
import logging
import time

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
def run_instance(instance_details: SWEbenchInstance, run_id: str, logger: logging.Logger):

    test_spec = make_test_spec(instance_details)
    with Environment.from_test_spec(
        test_spec=test_spec,
        run_id=run_id,
        logger=logger,
    ) as env:

        code_editor = Editor(env)

        llm = ChatBedrockConverse(
            model="us.anthropic.claude-3-5-sonnet-20240620-v1:0",
            # model="us.amazon.nova-pro-v1:0",
            config=Config(
                retries={
                    "mode": "adaptive",
                    "max_attempts": 1000,
                },
            ),
            region_name=os.getenv("AWS_REGION", "us-east-1"),
            temperature=0.2,
        )

        def submit():
            """Submit your changes once complete."""
            return env.get_patch()

        tools = [
            code_editor.open_file,
            code_editor.scroll_up,
            code_editor.scroll_down,
            code_editor.ls,
            code_editor.search_files,
            code_editor.code_search,
            code_editor.edit_file,
            code_editor.run_python_file,
            code_editor.execute_command,
            code_editor.rm,
            submit
        ]

        llm_with_tools = llm.bind_tools(tools).with_retry(stop_after_attempt=10)

        class CodeReviewerState(MessagesState):
            patch: str
            trajectory: Annotated[List[str], operator.add]

        def assistant(state: CodeReviewerState):
            # todo: collapse messages efficiently - e.g. remove failed tool calls
            next_message = llm_with_tools.invoke([pt.AGENT_INSTRUCTIONS_NO_REPRODUCE] + state["messages"])
            return {
                "messages": [next_message],
                "trajectory": [next_message.pretty_repr()]
            }

        def get_patch(state: CodeReviewerState):
            # if the agent created a temp file and forgot to delete it
            if code_editor._file_exists("reproduce_issue.py"):
                code_editor.rm("reproduce_issue.py")
            patch = env.get_patch()
            return {"patch": patch}

        def route_messages(state: CodeReviewerState):
            last_message = state["messages"][-1]
            if last_message and "submit" in [t.get("name") for t in last_message.tool_calls]:
                return "get_patch"  # Route to end
            else:
                return "tool_node"  # Route back to tool node

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

        messages = [
            HumanMessage(
                content=pt.START_AGENT.format(
                    repo=instance_details["repo"],
                    problem_statement=instance_details["problem_statement"],
                )
            )
        ]

        model_patch, trajectory, error = None, None, None
        try:
            for chunk in graph.stream({"messages": messages}, {"recursion_limit": 40}, stream_mode="updates"):
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
            model_patch = env.get_patch()

    return model_patch, trajectory, error


def process_instance(
    instance: SWEbenchInstance, run_id: str, dataset_id: str, model_name: str = "ai-review-agent"):

    logger = get_logger(
        __name__, 
        filename=os.path.join("logs", "inference", run_id, f"{instance['instance_id']}.log")
    )

    t_start = time.time()
    model_patch, trajectory, error = run_instance(instance, run_id, logger=logger)

    save_result(
        dataset_id,
        run_id,
        model_name_or_path=model_name,
        instance_id=instance['instance_id'],
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
    
    with ProcessPoolExecutor(max_workers=args.max_workers) as executor:
        # Create partial function with fixed arguments
        process_func = partial(process_instance, run_id=args.run_id, dataset_id=args.dataset_id)
        
        # Submit all tasks and wrap with tqdm
        futures = list(tqdm(
            executor.map(process_func, instances),
            total=len(instances)
        ))
    
    # for instance in tqdm(instances):
    #     t_start = time.time()
    #     model_patch, trajectory, error = run_instance(instance, args.run_id, logger=logger)
    #     save_result(
    #         args.dataset_id,
    #         args.run_id,
    #         instance["instance_id"],
    #         model_patch,
    #         trajectory,
    #         error,
    #         time_sec=round(time.time() - t_start, 1),
    #     )
