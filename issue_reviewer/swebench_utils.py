
from swebench.harness.docker_build import build_env_images, build_instance_images
from swebench.harness.utils import load_swebench_dataset
from swebench.harness.test_spec.test_spec import TestSpec, get_test_specs_from_dataset, make_test_spec
from swebench.harness.constants.constants import SWEbenchInstance
from swebench.harness.constants.python import MAP_REPO_VERSION_TO_SPECS_PY
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


def get_test_script(instance_details: SWEbenchInstance, test_files: list[str]) -> list[str]:

    specs = MAP_REPO_VERSION_TO_SPECS_PY[instance_details["repo"]][instance_details["version"]]

    directives = [
        d for d in test_files
    ]

    if instance_details["repo"] == "django/django":
        directives_transformed = []
        for d in directives:
            d = d[: -len(".py")] if d.endswith(".py") else d
            d = d[len("tests/") :] if d.startswith("tests/") else d
            d = d.replace("/", ".")
            directives_transformed.append(d)
        directives = directives_transformed

    test_command = [
        *specs["test_cmd"].split(" "),
        *directives,
    ]
    
    return test_command
