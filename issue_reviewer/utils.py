import logging
from typing import Dict, List, Optional

import os
import json


def get_logger(name, level=logging.INFO):
    logger = logging.getLogger(name)
    logger.setLevel(level)
    return logger


def write_to_jsonl(file_path: str, new_row: Dict):
    with open(file_path, "a") as f:
        f.write(json.dumps(new_row) + "\n")


def save_result(dataset_id: str, run_id: str, instance_id: str, model_patch: str, trajectory: List[str], error: Optional[str]):

    # save new row to results file
    result_path = os.path.join("../", "results", dataset_id, f"{run_id}.jsonl")
    write_to_jsonl(result_path, {"instance_id": instance_id, "model_patch": model_patch, "error": error})

    # save trajectory to trajectory file
    traj_path = os.path.join("../", "trajs", dataset_id, run_id, f"{instance_id}.json")
    with open(traj_path, "w") as f:
        json.dump(trajectory, f)
