"""
Sets up the environment to replicate an issue in docker

Environment

Constructors:
    __init__(repo, base_commit, setup_commands[]?)
    .from_swebench(instance_id)
    .from_github_issue(repo, issue_num)
    .from_config(EnvironmentConfig)

Methods:
    .setup() -> clones repo & checks out, builds via setup_commands (e.g. pip install -e .)
    .execute_command() -> docker.run(...) and returns output
"""
import docker
from docker.models.containers import Container
from swebench.harness.docker_build import build_container, build_env_images, build_instance_image
from swebench.harness.test_spec.test_spec import TestSpec
from swebench.harness.constants.python import MAP_REPO_VERSION_TO_SPECS_PY
from swebench.harness.docker_build import build_container
from typing import List, Self, Union, Optional
from exceptions import CommandFailedException
import logging
from utils import get_logger


class Environment:
    def __init__(
        self,
        base_image: str,
        workdir: str,
        container: Optional[Container] = None,
        docker_client: docker.client.DockerClient = docker.client.from_env(),
        logger: logging.Logger = get_logger(__name__),
        timeout: int = 60,
    ):
        self.docker = docker_client or docker.client.from_env()
        self.base_image = base_image
        # todo: abstract this to utils
        self.logger = logger
        self.logger.setLevel(logging.INFO)

        self.timeout = timeout
        self.workdir = workdir
        self.container = container or self.docker.containers.run(self.base_image, detach=True, tty=True, stdin_open=True)

    @classmethod
    def from_test_spec(cls, test_spec: TestSpec, run_id: str, **kwargs) -> Self:
        docker_client = kwargs.pop("docker_client", docker.client.from_env())
        logger = kwargs.pop("logger", get_logger(__name__))
        workdir = kwargs.pop("workdir", "/testbed")

        if test_spec.get_instance_container_name(run_id) in [c.name for c in docker_client.containers.list(all=True)]:
            logger.info(f"Container {test_spec.get_instance_container_name(run_id)} already exists. Removing and re-creating.")
            docker_client.containers.get(test_spec.get_instance_container_name(run_id)).remove(force=True)

        run_args = test_spec.docker_specs.get("run_args", {})
        cap_add = run_args.get("cap_add", [])

        logger.info(f"Running container: {test_spec.get_instance_container_name(run_id)}")
        container = build_container(
            test_spec, docker_client, run_id, logger, nocache=False,
        )

        logger.info("Starting container")
        # container.exec_run(
        #     test_spec.setup_env_script,
        #     workdir=workdir,
        # )
        container.start()

        # pre_install
        logger.info("Running pre_install scripts")
        pre_install_script = MAP_REPO_VERSION_TO_SPECS_PY[test_spec.repo][test_spec.version].get("pre_install")
        if pre_install_script:
            container.exec_run(
                pre_install_script,
                workdir=workdir,
            )

        # install
        logger.info("Running install scripts")
        install_script = MAP_REPO_VERSION_TO_SPECS_PY[test_spec.repo][test_spec.version].get("install")
        container.exec_run(
            install_script,
            workdir=workdir,
        )

        return cls(
            base_image=test_spec.instance_image_key,
            workdir="/testbed",
            container=container,
            docker_client=docker_client,
            logger=logger,
            **kwargs,
        )
    
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        self._teardown()

    def _teardown(self):
        # stop & remove container
        if self.container.status in ["created", "running"]:
            self.container.remove(force=True)

    def reset(self):
        # self.container.stop()
        # self.container.remove()
        # self.container = self.docker.containers.run(self.base_image, detach=True, tty=True, stdin_open=True)
        # todo: fix this - address the case where the container was provided externally
        raise NotImplementedError("Reset not implemented for Environment")

    def execute_command(self, command: Union[str, List[str]], ignore_errors: bool = False, **kwargs) -> str:
        """Runs a docker exec command and returns the logs, if available"""
        workdir = kwargs.pop("workdir", self.workdir)
        timeout = kwargs.pop("timeout", self.timeout)
        # run command in docker
        # if isinstance(command, str):
        #     command = f"timeout {timeout} {command}"
        # elif isinstance(command, list):
        #     command = ["timeout", f"{timeout}"] + command
        exit_code, output_bytes = self.container.exec_run(command, workdir=workdir, **kwargs)
        output = output_bytes.decode("utf-8")
        # check for timeouts
        if exit_code == 124:
            output += f"\nCommand timed out after {timeout} seconds."
        # return output
        if exit_code != 0 and ignore_errors is False:
            raise CommandFailedException(command, output)
        return output

    def ls(self, directory: Optional[str] = None):
        return self.execute_command(f"ls {directory or ''}")

    def get_patch(self, edited_files: Optional[List[str]] = None):
        edited_files = edited_files or ["."]
        # add
        self.execute_command(["git", "add"] + edited_files)
        # diff
        return self.execute_command(["git", "diff", "--cached"] + edited_files)
    