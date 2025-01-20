import logging
from typing import Dict, List, Optional

import os
import sys
import json
from pathlib import Path


def get_logger(name, filename=None, level=logging.INFO):
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Clear any existing handlers
    logger.handlers.clear()
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
        datefmt='%H:%M:%S'
    )
    
    # Create and add stream handler (stdout)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    
    # If filename is specified, add file handler
    if filename:
        p = Path(filename)
        if p.exists():
            os.remove(p)
        check_parent(p)
        file_handler = logging.FileHandler(filename, mode="a")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger


def check_parent(p: Path, **kwargs):
    if not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True, **kwargs)


def write_to_jsonl(file_path: str, new_row: Dict):
    p = Path(file_path)
    check_parent(p)
    with open(file_path, "a") as f:
        f.write(json.dumps(new_row) + "\n")


def save_result(
    dataset_id: str, 
    run_id: str, 
    instance_id: str, 
    model_patch: str, 
    trajectory: List[str], 
    error: Optional[str], 
    model_name_or_path: str = "issue-review-agent",
    **kwargs,
):

    # save new row to results file
    result_path = os.path.join("results", dataset_id, f"{run_id}.jsonl")
    write_to_jsonl(result_path, {
        "instance_id": instance_id, 
        "model_patch": model_patch, 
        "error": error, 
        "model_name_or_path": model_name_or_path,
        **kwargs,
    })

    # save trajectory
    traj_path = os.path.join("results", "trajs", dataset_id, run_id, f"{instance_id}.json")
    p = Path(traj_path)
    check_parent(p)
    with open(traj_path, "w") as f:
        json.dump({"trajectory": trajectory}, f)
