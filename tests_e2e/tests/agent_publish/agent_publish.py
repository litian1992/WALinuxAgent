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
import uuid
from datetime import datetime

from azurelinuxagent.common.future import UTC

from tests_e2e.tests.lib.agent_setup_helpers import wait_for_agent_to_complete_provisioning
from tests_e2e.tests.lib.agent_test import AgentVmTest
from tests_e2e.tests.lib.agent_test_context import AgentVmTestContext
from tests_e2e.tests.lib.agent_update_helpers import request_rsm_update, verify_current_agent_version
from tests_e2e.tests.lib.vm_extension_identifier import VmExtensionIds, VmExtensionIdentifier
from tests_e2e.tests.lib.logging import log
from tests_e2e.tests.lib.ssh_client import SshClient
from tests_e2e.tests.lib.virtual_machine_extension_client import VirtualMachineExtensionClient


class AgentPublishTest(AgentVmTest):
    """
    This script verifies if the agent update performed in the vm.
    """

    def __init__(self, context: AgentVmTestContext):
        super().__init__(context)
        self._ssh_client: SshClient = self._context.create_ssh_client()
        self._published_version = self._get_published_version()
        self._latest_version = self._get_latest_version()

    def run(self):
        """
        we run the scenario in the following steps:
            1. Print the current agent version before the update
            2. Prepare the agent for the update
            3. Check for agent update from the log and waagent version
            4. Ensure CSE is working
        """
        # Since we skip install_agent setup, doing it here for the Agent to complete provisioning before starting the test
        wait_for_agent_to_complete_provisioning(self._ssh_client)
        self._get_agent_info()
        self._enable_agent_auto_update() # some distro like SLES and debian disable auto update by default
        log.info("Verify agent updated to prod latest version: %s", self._latest_version)
        verify_current_agent_version(self._ssh_client, self._latest_version)

        log.info("")
        log.info("Testing rsm update({0} -> {1}) flow....".format(self._latest_version, self._published_version))
        self._prepare_agent_for_rsm_update()
        self._check_update_from_log()
        verify_current_agent_version(self._ssh_client, self._published_version)
        self._check_cse()

        log.info("")
        log.info("Testing self update(backed-in Agent -> {0}) flow....".format(self._published_version))
        self._prepare_agent_for_self_update(True)
        self._check_update_from_log()
        verify_current_agent_version(self._ssh_client, self._published_version)

        log.info("")
        log.info("Testing self update({0} -> {1}) flow....".format(self._latest_version, self._published_version))
        self._prepare_agent_for_self_update(False)
        self._check_update_from_log()
        verify_current_agent_version(self._ssh_client, self._published_version)
        self._check_cse()

    def get_ignore_errors_before_timestamp(self) -> datetime:
        timestamp = self._ssh_client.run_command("agent_publish-get_agent_log_record_timestamp.py")
        return datetime.strptime(timestamp.strip(), u'%Y-%m-%d %H:%M:%S.%f%z').replace(tzinfo=UTC)

    def _enable_agent_auto_update(self):
        """
        Enable agent auto update
        """
        log.info("Enabling agent auto update flag")
        self._ssh_client.run_command("update-waagent-conf AutoUpdate.Enabled=y AutoUpdate.UpdateToLatestVersion=y", use_sudo=True)
        log.info("Agent auto update flag enabled")

    def _get_published_version(self):
        """
        Gets version from test_args if provided, else use the release version from source code version.py
        """
        if hasattr(self._context, "published_version"):
            return self._context.published_version

        version = self._ssh_client.run_command("pypy3 -c 'from azurelinuxagent.common.version import AGENT_VERSION; print(AGENT_VERSION)'").rstrip()
        return version

    def _get_latest_version(self):
        """
        Read the latest version from the test context(test_args), else use a dummy version
        """
        if hasattr(self._context, "latest_version"):
            return self._context.latest_version
        return "9.9.9.9"

    def _get_agent_info(self) -> None:
        stdout: str = self._ssh_client.run_command("waagent-version", use_sudo=True)
        log.info('Agent info \n%s', stdout)

    def _verify_agent_reported_supported_feature_flag(self):
        """
        RSM update rely on supported feature flag that agent sends to CRP.So, checking if GA reports feature flag from reported status
        """
        log.info(
            "Executing verify_versioning_supported_feature.py remote script to verify agent reported supported feature flag, so that CRP can send RSM update request")
        self._run_remote_test(self._ssh_client, "agent_update-verify_versioning_supported_feature.py", use_sudo=True)
        log.info("Successfully verified that Agent reported VersioningGovernance supported feature flag")

    def _check_rsm_gs(self, requested_version: str) -> None:
        # This checks if RSM GS available to the agent after we send the rsm update request
        log.info(
            'Executing wait_for_rsm_gs.py remote script to verify latest GS contain requested version after rsm update requested')
        self._run_remote_test(self._ssh_client, f"agent_update-wait_for_rsm_gs.py --version {requested_version}",
                              use_sudo=True)
        log.info('Verified latest GS contain requested version after rsm update requested')

    def _prepare_agent_for_rsm_update(self) -> None:
        """
        This method prepares the agent for the RSM update
        """
        # We send RSM update request for new published test version
        self._verify_agent_reported_supported_feature_flag()
        arch_type = self._ssh_client.get_architecture()
        request_rsm_update(self._published_version, self._context.vm, arch_type, is_downgrade=False)
        self._check_rsm_gs(self._published_version)

        output: str = self._ssh_client.run_command(
                              "update-waagent-conf Debug.EnableGAVersioning=y AutoUpdate.GAFamily=Test", use_sudo=True)
        log.info('Successfully enabled rsm updates \n %s', output)

    def _prepare_agent_for_self_update(self, clean_all_agents) -> None:
        """
        This method prepares the agent for the self update
        """
        log.info("Modifying agent update related config flags and renaming the log file")
        if clean_all_agents:
            setup_script = ("agent-service stop &&  mv /var/log/waagent.log /var/log/waagent.$(date --iso-8601=seconds).log && "
                            "rm -rfv /var/lib/waagent/WALinuxAgent-* && "
                            "update-waagent-conf AutoUpdate.UpdateToLatestVersion=y AutoUpdate.GAFamily=Test AutoUpdate.Enabled=y Extensions.Enabled=y Debug.EnableGAVersioning=n Debug.SelfUpdateHotfixFrequency=90 Debug.SelfUpdateRegularFrequency=90 Autoupdate.Frequency=30")
        else:
            setup_script = ("agent-service stop &&  mv /var/log/waagent.log /var/log/waagent.$(date --iso-8601=seconds).log && "
                            f"rm -rfv /var/lib/waagent/WALinuxAgent-{self._published_version}* && "
                            "update-waagent-conf AutoUpdate.UpdateToLatestVersion=y AutoUpdate.GAFamily=Test AutoUpdate.Enabled=y Extensions.Enabled=y Debug.EnableGAVersioning=n Debug.SelfUpdateHotfixFrequency=90 Debug.SelfUpdateRegularFrequency=90 Autoupdate.Frequency=30")
        self._run_remote_test(self._ssh_client, f"sh -c '{setup_script}'", use_sudo=True)
        log.info('Renamed log file and updated self-update config flags')

    def _check_update_from_log(self) -> None:
        log.info("Verifying for agent update status")
        self._run_remote_test(self._ssh_client, f"agent_publish-check_update.py --published-version {self._published_version}")
        log.info('Successfully checked the agent update')

    def _check_cse(self) -> None:
        custom_script_2_1 = VirtualMachineExtensionClient(
            self._context.vm,
            VmExtensionIdentifier(VmExtensionIds.CustomScript.publisher, VmExtensionIds.CustomScript.type, "2.1"),
            resource_name="CustomScript")

        log.info("Installing %s", custom_script_2_1)
        message = f"Hello {uuid.uuid4()}!"
        custom_script_2_1.enable(
            protected_settings={
                'commandToExecute': f"echo \'{message}\'"
            },
            auto_upgrade_minor_version=False
        )
        custom_script_2_1.assert_instance_view(expected_version="2.1", expected_message=message)


if __name__ == "__main__":
    AgentPublishTest.run_from_command_line()
