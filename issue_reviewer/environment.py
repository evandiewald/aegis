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

from typing import List, Dict, Any, Tuple, Union, Optional
from exceptions import CommandFailedException
import logging


class Environment:
    def __init__(
        self,
        repo: str,
        base_commit: str,
        build_commands: Optional[Union[str, List[str]]] = None,
        setup_commands: Optional[Union[str, List[str]]] = None,
        base_image: str = "ubuntu:22.04",
        logger: logging.Logger = logging.getLogger(__name__)
    ):
        self.repo = repo
        self.base_commit = base_commit
        # should depend on base image
        self.build_commands = build_commands or [["apt", "update"], ["apt", "install", "-y", "git", "python3", "python3-pip"]]
        self.setup_commands = setup_commands or [["pip", "install", "-e", "."]]
        self.docker = docker.client.from_env()
        self.base_image = base_image
        # todo: abstract this to utils
        self.logger = logger
        self.logger.setLevel(logging.INFO)

        self.workdir = f"/{repo.split('/')[-1]}"
        self._container = self._setup_container()

    def __del__(self):
        self._teardown()

    def _setup_container(self) -> Container:
        # get container
        container = self.docker.containers.run(self.base_image, detach=True, tty=True, stdin_open=True)
        for command in self.build_commands:
            _, logs = container.exec_run(command)
            self.logger.info(logs)
        # clone repo
        _, logs = container.exec_run(["git", "clone", f"https://github.com/{self.repo}.git", self.workdir])
        self.logger.info(logs)
        # checkout base commit
        _, logs = container.exec_run(["git", "checkout", f"{self.base_commit}"], workdir=self.workdir)
        self.logger.info(logs)
        # build via setup_commands
        for command in self.setup_commands:
            _, logs = container.exec_run(command, workdir=self.workdir)
            self.logger.info(logs)
        return container

    def _teardown(self):
        # stop & remove container
        self._container.stop()
        self._container.remove()

    def execute_command(self, command: Union[str, List[str]], ignore_errors: bool = False, **kwargs) -> str:
        """Runs a docker exec command and returns the logs, if available"""
        workdir = kwargs.pop("workdir", self.workdir)
        # run command in docker
        exit_code, output_bytes = self._container.exec_run(command, workdir=workdir, **kwargs)
        output = output_bytes.decode("utf-8")
        # return output
        if exit_code != 0 and not ignore_errors:
            raise CommandFailedException(command, output)
        return output

