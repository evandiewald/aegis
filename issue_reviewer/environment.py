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
    ):
        self.docker = docker_client or docker.client.from_env()
        self.base_image = base_image
        # todo: abstract this to utils
        self.logger = logger
        self.logger.setLevel(logging.INFO)

        self.workdir = workdir
        self.container = container or self.docker.containers.run(self.base_image, detach=True, tty=True, stdin_open=True)

    @classmethod
    def from_test_spec(cls, test_spec: TestSpec, run_id: str, **kwargs) -> Self:
        docker_client = kwargs.get("client", docker.client.from_env())
        logger = kwargs.get("logger", get_logger(__name__))

        if test_spec.get_instance_container_name(run_id) in [c.name for c in docker_client.containers.list(all=True)]:
            logger.info(f"Container {test_spec.get_instance_container_name(run_id)} already exists. Removing and re-creating.")
            docker_client.containers.get(test_spec.get_instance_container_name(run_id)).remove(force=True)

        run_args = test_spec.docker_specs.get("run_args", {})
        cap_add = run_args.get("cap_add", [])

        logger.info(f"Running container: {test_spec.get_instance_container_name(run_id)}")
        container = docker_client.containers.run(
            image=test_spec.instance_image_key,
            command="tail -f /dev/null",
            name=test_spec.get_instance_container_name(run_id),
            detach=True,
            tty=True,
            stdin_open=True,
            platform=test_spec.platform,
            cap_add=cap_add,
        )

        return cls(
            base_image=test_spec.instance_image_key,
            workdir="/testbed",
            container=container,
            docker_client=docker_client,
            logger=logger,
        )
    
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        self._teardown()

    def __del__(self):
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
        # run command in docker
        exit_code, output_bytes = self.container.exec_run(command, workdir=workdir, **kwargs)
        output = output_bytes.decode("utf-8")
        # return output
        if exit_code != 0 and not ignore_errors:
            raise CommandFailedException(command, output)
        return output

    def ls(self, directory: Optional[str] = None):
        return self.execute_command(f"ls {directory or ''}")

    def get_patch(self):
        self.execute_command(["git", "add", "."])
        return self.execute_command(["git", "diff", "--cached"])
