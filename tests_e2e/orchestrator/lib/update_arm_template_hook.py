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

import importlib.util
import logging

from pathlib import Path
from typing import Any

# Disable those warnings, since 'lisa' is an external, non-standard, dependency
# E0401: Unable to import 'lisa.*' (import-error)
# pylint: disable=E0401
from lisa.environment import Environment
from lisa.util import hookimpl, plugin_manager
from lisa.sut_orchestrator.azure.platform_ import AzurePlatformSchema
# pylint: enable=E0401

import tests_e2e
from tests_e2e.tests.lib.network_security_rule import NetworkSecurityRule
from tests_e2e.tests.lib.update_arm_template import UpdateArmTemplate


class UpdateArmTemplateHook:
    """
    This hook allows to customize the ARM template used to create the test VMs (see wiki for details).
    """
    @hookimpl
    def azure_update_arm_template(self, template: Any, environment: Environment) -> None:
        log: logging.Logger = logging.getLogger("lisa")

        azure_runbook: AzurePlatformSchema = environment.platform.runbook.get_extended_runbook(AzurePlatformSchema)
        vm_tags = azure_runbook.vm_tags

        #
        # Add the allow SSH security rule if requested by the runbook
        #
        allow_ssh: str = vm_tags.get("allow_ssh")
        network_security_rule = NetworkSecurityRule(template, is_lisa_template=True)
        # Disabling the default outbound access due to security requirement
        log.info("******** Waagent: Marking subnet to disable default outbound access")
        network_security_rule.disable_default_outbound_access()
        if allow_ssh is not None:
            log.info("******** Waagent: Adding network security rule to allow SSH connections from %s", allow_ssh)
            network_security_rule.add_allow_ssh_rule(allow_ssh)

        #
        # Apply any template customizations provided by the tests.
        #
        # The "templates" tag is a comma-separated list of the template customizations provided by the tests
        test_templates = vm_tags.get("templates")
        if test_templates is not None:
            log.info("******** Waagent: Applying custom templates '%s' to environment '%s'", test_templates, environment.name)

            for t in test_templates.split(","):
                update_arm_template = self._get_update_arm_template(t)
                update_arm_template().update(template, is_lisa_template=True)

    _SOURCE_CODE_ROOT: Path = Path(tests_e2e.__path__[0])

    @staticmethod
    def _get_update_arm_template(test_template: str) -> UpdateArmTemplate:
        """
        Returns the UpdateArmTemplate class that implements the template customization for the test.
        """
        source_file: Path = UpdateArmTemplateHook._SOURCE_CODE_ROOT/"tests"/test_template

        spec = importlib.util.spec_from_file_location(f"tests_e2e.tests.templates.{source_file.name}", str(source_file))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # find all the classes in the module that are subclasses of UpdateArmTemplate but are not UpdateArmTemplate itself.
        matches = [v for v in module.__dict__.values() if isinstance(v, type) and issubclass(v, UpdateArmTemplate) and v != UpdateArmTemplate]
        if len(matches) != 1:
            raise Exception(f"Error in {source_file}: template files must contain exactly one class derived from UpdateArmTemplate)")
        return matches[0]


plugin_manager.register(UpdateArmTemplateHook())
