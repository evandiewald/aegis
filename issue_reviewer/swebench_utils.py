
from swebench.harness.docker_build import build_env_images, build_instance_images
from swebench.harness.utils import load_swebench_dataset
from swebench.harness.test_spec.test_spec import TestSpec, get_test_specs_from_dataset, make_test_spec
from swebench.harness.constants.constants import SWEbenchInstance
import docker

from typing import List, Dict


def build_swebench_images(
    dataset_id: str,
    split: str,
    instance_ids: List[str],
    tag: str = "latest",
    **kwargs,
) -> Dict[str, TestSpec]:
    """Returns a mapping from instance_id -> test_spec"""
    docker_client = docker.client.from_env()

    dataset = load_swebench_dataset(dataset_id, split)
    dataset_subset = [i for i in dataset if i["instance_id"] in instance_ids]

    # for multimodal dataset, certain fields are empty strings, which causes failures
    for instance in dataset_subset:
        for _key in ["FAIL_TO_PASS", "PASS_TO_PASS"]:
            if instance[_key] == "":
                instance[_key] = []

    successful, _ = build_instance_images(
        docker_client,
        dataset=dataset_subset,
        tag=tag,
        **kwargs,
    )

    return successful

