#!/usr/bin/env python3

# Microsoft Azure Linux Agent
#
# Copyright 2018 Microsoft Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

#
# BVT for the agent update scenario
#
# The test verifies agent update for rsm workflow. This test covers three scenarios downgrade, upgrade and no update.
# For each scenario, we initiate the rsm request with target version and then verify agent updated to that target version.
#
import re
from typing import List, Dict, Any

from assertpy import assert_that, fail

from tests_e2e.tests.lib.agent_test import AgentVmTest
from tests_e2e.tests.lib.agent_test_context import AgentVmTestContext
from tests_e2e.tests.lib.agent_update_helpers import request_rsm_update
from tests_e2e.tests.lib.logging import log
from tests_e2e.tests.lib.retry import retry_if_false


class RsmUpdateBvt(AgentVmTest):

    def __init__(self, context: AgentVmTestContext):
        super().__init__(context)
        self._ssh_client = self._context.create_ssh_client()
        self._installed_agent_version = "9.9.9.9"
        self._downgrade_version = "9.9.9.9"

    def get_ignore_error_rules(self) -> List[Dict[str, Any]]:
        ignore_rules = [
            #
            # This is expected as we validate the downgrade scenario
            #
            # WARNING ExtHandler ExtHandler Agent WALinuxAgent-9.9.9.9 is permanently blacklisted
            # Note: Version varies depending on the pipeline branch the test is running on
            {
                'message': rf"Agent WALinuxAgent-{self._installed_agent_version} is permanently blacklisted",
                'if': lambda r: r.prefix == 'ExtHandler' and self._installed_agent_version > self._downgrade_version
            },
            # We don't allow downgrades below then daemon version
            # 2023-07-11T02:28:21.249836Z WARNING ExtHandler ExtHandler [AgentUpdateError] The Agent received a request to downgrade to version 1.4.0.0, but downgrading to a version less than the Agent installed on the image (1.4.0.1) is not supported. Skipping downgrade.
            #
            {
                'message': r"downgrading to a version less than the Agent installed on the image.* is not supported"
            }

        ]
        return ignore_rules

    def run(self) -> None:
        arch_type = self._ssh_client.get_architecture()
        # retrieve the installed agent version in the vm before run the scenario
        self._retrieve_installed_agent_version()
        # Allow agent to send supported feature flag
        self._verify_agent_reported_supported_feature_flag()

        log.info("*******Verifying the Agent Downgrade scenario*******")
        stdout: str = self._ssh_client.run_command("waagent-version", use_sudo=True)
        log.info("Current agent version running on the vm before update is \n%s", stdout)
        self._downgrade_version: str = "2.3.15.0"
        log.info("Attempting downgrade version %s", self._downgrade_version)
        request_rsm_update(self._downgrade_version, self._context.vm, arch_type, is_downgrade=True)
        self._check_rsm_gs(self._downgrade_version)
        self._prepare_agent()
        # Verify downgrade scenario
        self._verify_guest_agent_update(self._downgrade_version)
        self._verify_agent_reported_update_status(self._downgrade_version)

        # Verify upgrade scenario
        log.info("*******Verifying the Agent Upgrade scenario*******")
        stdout: str = self._ssh_client.run_command("waagent-version", use_sudo=True)
        log.info("Current agent version running on the vm before update is \n%s", stdout)
        upgrade_version: str = "2.3.15.1"
        log.info("Attempting upgrade version %s", upgrade_version)
        request_rsm_update(upgrade_version, self._context.vm, arch_type, is_downgrade=False)
        self._check_rsm_gs(upgrade_version)
        self._verify_guest_agent_update(upgrade_version)
        self._verify_agent_reported_update_status(upgrade_version)

        # verify no version update.
        log.info("*******Verifying the no version update scenario*******")
        stdout: str = self._ssh_client.run_command("waagent-version", use_sudo=True)
        log.info("Current agent version running on the vm before update is \n%s", stdout)
        current_version: str = "2.3.15.1"
        log.info("Attempting update version same as current version %s", current_version)
        request_rsm_update(current_version, self._context.vm, arch_type, is_downgrade=False)
        self._check_rsm_gs(current_version)
        self._verify_guest_agent_update(current_version)
        self._verify_agent_reported_update_status(current_version)

        # verify requested version below daemon version
        # All the daemons set to 2.2.53, so requesting version below daemon version
        log.info("*******Verifying requested version below daemon version scenario*******")
        stdout: str = self._ssh_client.run_command("waagent-version", use_sudo=True)
        log.info("Current agent version running on the vm before update is \n%s", stdout)
        version: str = "1.5.0.0"
        log.info("Attempting requested version %s", version)
        request_rsm_update(version, self._context.vm, arch_type, is_downgrade=True)
        self._check_rsm_gs(version)
        self._verify_no_guest_agent_update(version)
        self._verify_agent_reported_update_status(version)

    def _check_rsm_gs(self, requested_version: str) -> None:
        # This checks if RSM GS available to the agent after we send the rsm update request
        log.info(
            'Executing wait_for_rsm_gs.py remote script to verify latest GS contain requested version after rsm update requested')
        self._run_remote_test(self._ssh_client, f"agent_update-wait_for_rsm_gs.py --version {requested_version}",
                              use_sudo=True)
        log.info('Verified latest GS contain requested version after rsm update requested')

    def _prepare_agent(self) -> None:
        """
        This method is to ensure agent is ready for accepting rsm updates. As part of that we update following flags
        1) Changing daemon version since daemon has a hard check on agent version in order to update agent. It doesn't allow versions which are less than daemon version.
        2) Updating GAFamily type "Test" and GAUpdates flag to process agent updates on test versions.
        """
        log.info(
            'Executing modify_agent_version remote script to update agent installed version to lower than requested version')
        output: str = self._ssh_client.run_command("agent_update-modify_agent_version 2.2.53", use_sudo=True)
        log.info('Successfully updated agent installed version \n%s', output)
        log.info(
            'Executing update-waagent-conf remote script to update agent update config flags to allow and download test versions')
        output: str = self._ssh_client.run_command(
                              "update-waagent-conf AutoUpdate.UpdateToLatestVersion=y Debug.EnableGAVersioning=y Debug.EnableRsmDowngrade=y AutoUpdate.GAFamily=Test", use_sudo=True)
        log.info('Successfully updated agent update config \n %s', output)

    def _verify_guest_agent_update(self, requested_version: str) -> None:
        """
        Verify current agent version running on rsm requested version
        """

        def _check_agent_version(requested_version: str) -> bool:
            waagent_version: str = self._ssh_client.run_command("waagent-version", use_sudo=True)
            expected_version = f"Goal state agent: {requested_version}"
            if expected_version in waagent_version:
                return True
            else:
                return False

        waagent_version: str = ""
        log.info("Verifying agent updated to requested version: {0}".format(requested_version))
        success: bool = retry_if_false(lambda: _check_agent_version(requested_version))
        if not success:
            fail("Guest agent didn't update to requested version {0} but found \n {1}. \n "
                 "To debug verify if CRP has upgrade operation around that time and also check if agent log has any errors ".format(
                requested_version, waagent_version))
        waagent_version: str = self._ssh_client.run_command("waagent-version", use_sudo=True)
        log.info(
            f"Successfully verified agent updated to requested version. Current agent version running:\n {waagent_version}")

    def _verify_no_guest_agent_update(self, version: str) -> None:
        """
        verify current agent version is not updated to requested version
        """
        log.info("Verifying no update happened to agent")
        current_agent: str = self._ssh_client.run_command("waagent-version", use_sudo=True)
        assert_that(current_agent).does_not_contain(version).described_as(
            f"Agent version changed.\n Current agent {current_agent}")
        log.info("Verified agent was not updated to requested version")

    def _verify_agent_reported_supported_feature_flag(self):
        """
        RSM update rely on supported flag that agent sends to CRP.So, checking if GA reports feature flag from the agent log
        """

        log.info(
            "Executing verify_versioning_supported_feature.py remote script to verify agent reported supported feature flag, so that CRP can send RSM update request")
        self._run_remote_test(self._ssh_client, "agent_update-verify_versioning_supported_feature.py", use_sudo=True)
        log.info("Successfully verified that Agent reported VersioningGovernance supported feature flag")

    def _verify_agent_reported_update_status(self, version: str):
        """
        Verify if the agent reported update status to CRP after update performed
        """

        log.info(
            "Executing verify_agent_reported_update_status.py remote script to verify agent reported update status for version {0}".format(
                version))
        self._run_remote_test(self._ssh_client,
                              f"agent_update-verify_agent_reported_update_status.py --version {version}", use_sudo=True)
        log.info("Successfully Agent reported update status for version {0}".format(version))

    def _retrieve_installed_agent_version(self):
        """
        Retrieve the installed agent version
        """
        log.info("Retrieving installed agent version")
        stdout: str = self._ssh_client.run_command("waagent-version", use_sudo=True)
        log.info("Retrieved installed agent version \n {0}".format(stdout))
        match = re.search(r'.*Goal state agent: (\S*)', stdout)
        if match:
            self._installed_agent_version = match.groups()[0]
        else:
            log.warning("Unable to retrieve installed agent version and set to default value {0}".format(
                self._installed_agent_version))


if __name__ == "__main__":
    RsmUpdateBvt.run_from_command_line()
