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
from typing import Annotated, List, Dict
import operator
from tqdm import tqdm

import argparse


load_dotenv("../.env")


def parse_args():
    # todo: more configs here that default to configuration.py values
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-id", type=str, default="princeton-nlp/SWE-bench_Lite")
    parser.add_argument("--split", type=str, default="test")
    # accept multiple instance_ids
    parser.add_argument("--instance-ids", nargs="+", type=str, required=True)
    parser.add_argument("--run-id", type=str, required=True)
    return parser.parse_args()


def build_images(dataset_id, split, instance_ids, tag="latest") -> List[Dict]:

    dataset = load_dataset(dataset_id, split=split)
    if "all" in instance_ids:
        instance_ids = [r["instance_id"] for r in dataset]
    # base -> environment -> instance images
    build_swebench_images(
        dataset_id, split,
        instance_ids=instance_ids,
        tag=tag,
    )

    return [r for r in dataset if r["instance_id"] in instance_ids]


# create environment
def run_instance(instance_details: SWEbenchInstance, run_id: str):

    test_spec = make_test_spec(instance_details)
    env = Environment.from_test_spec(
        test_spec=test_spec,
        run_id=run_id,
    )

    code_editor = Editor(env)

    llm = ChatBedrockConverse(
        model="us.anthropic.claude-3-5-sonnet-20240620-v1:0",
        config=Config(
            retries={"max_attempts": 100}
        )
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

    llm_with_tools = llm.bind_tools(tools)

    class CodeReviewerState(MessagesState):
        patch: str
        trajectory: Annotated[List[str], operator.add]

    def assistant(state: CodeReviewerState):
        # todo: collapse messages efficiently - e.g. remove failed tool calls
        next_message = llm_with_tools.invoke([pt.AGENT_INSTRUCTIONS] + state["messages"])
        return {
            "messages": [next_message],
            "trajectory": [next_message.pretty_print()]
        }

    def get_patch(state: CodeReviewerState):
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
                chunk["tool_node"]["messages"][-1].pretty_print()
            elif "assistant" in chunk:
                chunk["assistant"]["messages"][-1].pretty_print()
                trajectory = chunk["assistant"]["trajectory"]
            elif "get_patch" in chunk:
                model_patch = chunk["get_patch"]["patch"]
                trajectory = chunk["get_patch"]["trajectory"]

        if model_patch:
            print("GOT MODEL PATCH")
            print(model_patch)
    except Exception as e:
        error = str(e)
        logger.error(e)
        model_patch = env.get_patch()

    return model_patch, trajectory, error


if __name__ == "__main__":
    logger = get_logger(__name__)
    args = parse_args()
    instances = build_images(
        args.dataset_id, args.split, args.instance_ids
    )
    for instance in tqdm(instances):
        model_patch, trajectory, error = run_instance(instance, args.run_id)
        save_result(
            args.dataset_id,
            args.run_id,
            instance["instance_id"],
            model_patch,
            trajectory,
            error,
        )
